"""Embeddings for symbols, with a content-addressed on-disk cache.

Backends (no PyTorch in either):
- fastembed (ONNX runtime) for transformer models. Default: nomic-ai/CodeRankEmbed, a 137M-parameter
  code-trained encoder (CoIR 60.1), run as the int8 ONNX build published at
  nishparadox/CodeRankEmbed-onnx-int8 (138 MB, pinned to a commit). Measured on an Apple-silicon
  laptop: ~33 functions/s on whole function bodies, batches sorted by length.
- model2vec (pure numpy static embeddings) for minishlab/potion-* models. potion-code-16M-v2
  embeds ~17,000 functions/s; weaker on "same job, different code" but fine as a fast mode.

Nothing is truncated: each model's own context length (8192 tokens for both defaults) is the only
limit. Speed comes from sorting texts by length so a batch never pads short functions up to a long one.
"""

from __future__ import annotations

import hashlib
import os
import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, Sequence

from vibepiler.ir.model import CALLABLE_KINDS, Node
from vibepiler.ir.program import Program

DEFAULT_MODEL = "nomic-ai/CodeRankEmbed"


@dataclass(frozen=True)
class ModelSpec:
    """An ONNX model fastembed does not know by itself: where the file is and how to pool it."""

    hf: str  # Hugging Face repo holding the ONNX file and tokenizer
    model_file: str
    pooling: str  # "cls" | "mean"
    dim: int
    max_length: int  # tokens; set explicitly because tokenizer configs often carry a stale 512
    revision: str | None = None  # commit to pin, so a repo change cannot alter results silently


#: logical model name -> where to get it; anything else is passed to fastembed by name
MODELS: dict[str, ModelSpec] = {
    "nomic-ai/CodeRankEmbed": ModelSpec(
        hf="nishparadox/CodeRankEmbed-onnx-int8",
        model_file="onnx/model_quantized.onnx",
        pooling="cls",
        dim=768,
        max_length=8192,
        revision="79db5e4905bde1ce1cebae501893d7c0b48f13d6",
    ),
    "jinaai/jina-embeddings-v2-base-code": ModelSpec(
        hf="jinaai/jina-embeddings-v2-base-code",
        model_file="onnx/model_quantized.onnx",
        pooling="mean",
        dim=768,
        max_length=8192,
    ),
}

#: per-model defaults for the similarity thresholds (cosine distributions differ a lot between models)
MODEL_THRESHOLDS: dict[str, tuple[float, float]] = {
    # model prefix: (semantic_similarity, responsibility_similarity)
    "nomic-ai/CodeRankEmbed": (0.72, 0.32),
    "jinaai/jina-embeddings-v2-base-code": (0.82, 0.40),
    "BAAI/bge-small-en-v1.5": (0.88, 0.70),
    "minishlab/potion-code": (0.90, 0.60),
}
_FALLBACK_THRESHOLDS = (0.88, 0.70)

#: batching: at most this many texts per call, and never more than this many characters of
#: padded width (longest text x batch size), so one long function does not inflate 31 short ones
BATCH_MAX = 32
BATCH_CHAR_BUDGET = 32 * 800

_CACHE_BASE = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "vibepiler"
CACHE_DIR = _CACHE_BASE / "embeddings"
MODELS_DIR = _CACHE_BASE / "models"

_TOKENIZER_FILES = ("config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "vocab.txt")


class Embedder(Protocol):
    name: str

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...


# --------------------------------------------------------------------------- model files
def _hf_snapshot(spec: ModelSpec, dest: Path) -> None:
    from huggingface_hub import snapshot_download  # type: ignore[import-not-found]

    snapshot_download(
        repo_id=spec.hf,
        revision=spec.revision,
        local_dir=str(dest),
        allow_patterns=[*_TOKENIZER_FILES, spec.model_file, spec.model_file + "_data"],
    )


def prepare_model_dir(
    model: str,
    spec: ModelSpec,
    cache_dir: Path | None = None,
    *,
    snapshot: Callable[[ModelSpec, Path], None] | None = None,
) -> tuple[Path, str]:
    """Make sure the model's files are on disk; returns (directory, model file relative to it)."""
    dest = (cache_dir or MODELS_DIR) / _slug(model)
    if not (dest / spec.model_file).is_file():
        dest.mkdir(parents=True, exist_ok=True)
        (snapshot or _hf_snapshot)(spec, dest)
    return dest, spec.model_file


class FastEmbedEmbedder:
    def __init__(self, model: str) -> None:
        from fastembed import TextEmbedding  # type: ignore[import-not-found]

        self.name = model
        spec = MODELS.get(model)
        if spec is None:
            self._model = TextEmbedding(model_name=model, cache_dir=str(MODELS_DIR))
            return
        from fastembed.common.model_description import ModelSource, PoolingType  # type: ignore[import-not-found]

        directory, model_file = prepare_model_dir(model, spec)
        name = f"{model}:int8"
        if name not in {m["model"] for m in TextEmbedding.list_supported_models()}:
            TextEmbedding.add_custom_model(
                model=name,
                pooling=PoolingType.CLS if spec.pooling == "cls" else PoolingType.MEAN,
                normalization=True,
                sources=ModelSource(hf=spec.hf),
                dim=spec.dim,
                model_file=model_file,
            )
        self.name = name
        self._model = TextEmbedding(model_name=name, specific_model_path=str(directory))
        try:  # fastembed takes the stricter of the tokenizer config's two length keys; we know the real one
            self._model.model.tokenizer.enable_truncation(max_length=spec.max_length)
        except AttributeError:
            warnings.warn(f"could not set the tokenizer length for {model}; fastembed's internals changed, long functions may be truncated", RuntimeWarning, stacklevel=2)

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return [list(map(float, v)) for v in self._model.embed(list(texts), batch_size=max(1, len(texts)))]


class Model2VecEmbedder:
    """Static embeddings (minishlab/potion-*): pure numpy, thousands of functions per second."""

    def __init__(self, model: str) -> None:
        from model2vec import StaticModel  # type: ignore[import-not-found]

        self.name = model
        self._model = StaticModel.from_pretrained(model)

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return self._model.encode(list(texts)).tolist()


def backend_for(model: str) -> str:
    return "model2vec" if model.startswith("minishlab/") or "potion" in model else "fastembed"


def default_thresholds(model: str) -> tuple[float, float]:
    """(semantic_similarity, responsibility_similarity) defaults for a model."""
    for prefix, th in MODEL_THRESHOLDS.items():
        if model.startswith(prefix):
            return th
    return _FALLBACK_THRESHOLDS


def default_factory(model: str) -> Embedder | None:
    try:
        if backend_for(model) == "model2vec":
            return Model2VecEmbedder(model)
        return FastEmbedEmbedder(model)
    except ImportError:
        return None


#: swapped out by tests; returns None when the optional backend is missing
EMBEDDER_FACTORY: Callable[[str], Embedder | None] = default_factory


def get_embedder(model: str = DEFAULT_MODEL) -> Embedder | None:
    return EMBEDDER_FACTORY(model)


# --------------------------------------------------------------------------- text
def symbol_text(program: Program, node: Node, max_chars: int | None = None) -> str:
    """What gets embedded: the symbol's whole source (signature + body), lightly normalized."""
    d = program.symbol_def(node)
    if d is None or d.node is None:
        return node.qualname
    sf = program.facts[node.module].file  # type: ignore[index]
    src = sf.text(d.node)
    src = re.sub(r"[ \t]+", " ", src)
    return src if max_chars is None else src[:max_chars]


def words(name: str) -> str:
    """snake_case / camelCase identifier -> space separated words."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name).replace("_", " ").replace(".", " ")
    return s.lower().strip()


# --------------------------------------------------------------------------- cache
def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "__", s)


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()


def batches(texts: Sequence[str], max_batch: int = BATCH_MAX, char_budget: int = BATCH_CHAR_BUDGET) -> list[list[int]]:
    """Indices grouped for embedding: sorted by length, each batch bounded by count and padded width."""
    order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
    out: list[list[int]] = []
    i = 0
    while i < len(order):
        j = i + 1
        while j < len(order) and j - i < max_batch and len(texts[order[j]]) * (j - i + 1) <= char_budget:
            j += 1
        out.append(order[i:j])
        i = j
    return out


def embed_texts(embedder: Embedder, texts: Sequence[str], cache_dir: Path | None = None):
    """Embed texts, reusing cached vectors for texts seen before (keyed by content hash and model)."""
    import numpy as np

    cache_dir = cache_dir or CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{_slug(embedder.name)}.npz"
    cached: dict[str, "np.ndarray"] = {}
    if path.is_file():
        try:
            data = np.load(path, allow_pickle=False)
            cached = dict(zip(data["hashes"].tolist(), data["vectors"]))
        except (OSError, ValueError, KeyError):
            cached = {}
    hashes = [_hash(t) for t in texts]
    missing = sorted({h for h in hashes if h not in cached})
    if missing:
        by_hash = {_hash(t): t for t in texts}
        todo = [by_hash[h] for h in missing]
        for batch in batches(todo):
            for k, v in zip(batch, embedder.embed([todo[k] for k in batch])):
                cached[missing[k]] = np.asarray(v, dtype=np.float32)
        try:
            keys = list(cached)
            np.savez(path, hashes=np.array(keys), vectors=np.stack([cached[k] for k in keys]))
        except OSError as exc:
            warnings.warn(f"embedding cache not written ({exc}); vectors will be recomputed next run", RuntimeWarning, stacklevel=2)
    mat = np.stack([cached[h] for h in hashes]) if hashes else np.zeros((0, 1), dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


# --------------------------------------------------------------------------- program-level
def symbol_vectors(program: Program, min_loc: int = 2):
    """(nodes, unit vectors) for every function/method worth embedding; memoized on the program."""
    memo = program.metrics.get("_semantic")
    if memo is not None:
        return memo["nodes"], memo["vectors"], memo["model"]
    model = program.config.embedding_model
    embedder = get_embedder(model)
    if embedder is None:
        return None, None, model
    g = program.graph
    nodes = [
        n
        for n in g.nodes(CALLABLE_KINDS)
        if n.loc >= min_loc
        and program.symbol_def(n) is not None
        and not g.node(n.module).attrs.get("is_test")  # tests are repetitive by design
        and not g.node(n.module).attrs.get("is_migration")  # each migration repeats the last one's shape on purpose
        and (g.parent(n.id) is None or g.parent(n.id).kind.value != "function")  # nested closures/wrappers
    ]
    nodes.sort(key=lambda n: (n.file or "", n.start_line or 0))
    vectors = embed_texts(embedder, [symbol_text(program, n) for n in nodes])
    program.metrics["_semantic"] = {"nodes": nodes, "vectors": vectors, "model": embedder.name}
    return nodes, vectors, embedder.name
