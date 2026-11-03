"""Embeddings for symbols, with a content-addressed on-disk cache.

Backends (no PyTorch in either):
- fastembed (ONNX runtime) for transformer models. Default: jinaai/jina-embeddings-v2-base-code,
  a 161M-parameter encoder trained on code (30 languages), run as int8 ONNX. Measured on an
  Apple-silicon laptop: ~23 functions/s with 800-char inputs, same speed as bge-small but a
  cleaner separation of true twins.
- model2vec (pure numpy static embeddings) for minishlab/potion-* models. potion-code-16M-v2
  embeds ~17,000 functions/s; weaker on "same job, different code" but fine as a fast mode.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Callable, Protocol, Sequence

from vibepiler.ir.model import CALLABLE_KINDS, Node
from vibepiler.ir.program import Program

DEFAULT_MODEL = "jinaai/jina-embeddings-v2-base-code"
FAST_MODEL = "minishlab/potion-code-16M-v2"
#: characters of source embedded per symbol; beyond this, speed drops faster than quality rises
MAX_CHARS = 800

#: models with an int8 ONNX file in their HF repo that fastembed does not pick by itself: name -> (file, dim)
QUANTIZED_VARIANTS: dict[str, tuple[str, int]] = {
    "jinaai/jina-embeddings-v2-base-code": ("onnx/model_quantized.onnx", 768),
}

#: per-model defaults for the similarity thresholds (cosine distributions differ a lot between models)
MODEL_THRESHOLDS: dict[str, tuple[float, float]] = {
    # model prefix: (semantic_similarity, responsibility_similarity)
    "jinaai/jina-embeddings-v2-base-code": (0.82, 0.40),
    "BAAI/bge-small-en-v1.5": (0.88, 0.70),
    "minishlab/potion-code": (0.90, 0.60),
}
_FALLBACK_THRESHOLDS = (0.88, 0.70)

CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "vibepiler" / "embeddings"


class Embedder(Protocol):
    name: str

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...


class FastEmbedEmbedder:
    def __init__(self, model: str, quantized: bool = True) -> None:
        from fastembed import TextEmbedding  # type: ignore[import-not-found]

        self.name = model
        name = model
        if quantized and model in QUANTIZED_VARIANTS:
            from fastembed.common.model_description import ModelSource, PoolingType  # type: ignore[import-not-found]

            file, dim = QUANTIZED_VARIANTS[model]
            name = f"{model}:int8"
            if name not in {m["model"] for m in TextEmbedding.list_supported_models()}:
                TextEmbedding.add_custom_model(
                    model=name, pooling=PoolingType.MEAN, normalization=True, sources=ModelSource(hf=model), dim=dim, model_file=file
                )
            self.name = name
        self._model = TextEmbedding(model_name=name)

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return [list(map(float, v)) for v in self._model.embed(list(texts), batch_size=32)]


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
def symbol_text(program: Program, node: Node, max_chars: int = MAX_CHARS) -> str:
    """What gets embedded: the symbol's source (signature + body), lightly normalized."""
    d = program.symbol_def(node)
    if d is None or d.node is None:
        return node.qualname
    sf = program.facts[node.module].file  # type: ignore[index]
    src = sf.text(d.node)
    src = re.sub(r"[ \t]+", " ", src)
    return src[:max_chars]


def words(name: str) -> str:
    """snake_case / camelCase identifier -> space separated words."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name).replace("_", " ").replace(".", " ")
    return s.lower().strip()


# --------------------------------------------------------------------------- cache
def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "__", s)


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()


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
        vectors = embedder.embed([by_hash[h] for h in missing])
        for h, v in zip(missing, vectors):
            cached[h] = np.asarray(v, dtype=np.float32)
        try:
            keys = list(cached)
            np.savez(path, hashes=np.array(keys), vectors=np.stack([cached[k] for k in keys]))
        except OSError:
            pass
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
        and (g.parent(n.id) is None or g.parent(n.id).kind.value != "function")  # nested closures/wrappers
    ]
    nodes.sort(key=lambda n: (n.file or "", n.start_line or 0))
    vectors = embed_texts(embedder, [symbol_text(program, n) for n in nodes])
    program.metrics["_semantic"] = {"nodes": nodes, "vectors": vectors, "model": embedder.name}
    return nodes, vectors, embedder.name
