"""Embeddings for symbols, with a content-addressed on-disk cache.

Backends (no PyTorch in either):
- fastembed (ONNX runtime) for transformer models. Default: nomic-ai/CodeRankEmbed, a 137M-parameter
  code-trained encoder (CoIR 60.1), run as the int8 ONNX build published at
  nishparadox/CodeRankEmbed-onnx-int8 (138 MB, pinned to a commit). Measured on an Apple-silicon
  laptop: ~33 functions/s on whole function bodies, batches sorted by length.
- model2vec (pure numpy static embeddings) for minishlab/potion-* models. potion-code-16M-v2
  embeds ~17,000 functions/s; weaker on "same job, different code" but fine as a fast mode.

Each function is embedded up to `embedding_max_tokens` (4096 by default, about 400-500 lines, so nearly every function
whole) of the model's context (8192 for both defaults): a transformer's memory grows with the square of the input, and
one 8192-token function took 6.8 GB where 4096 tokens takes 1.9 and 2048 takes 0.8. 0 lifts the cap. Speed comes from sorting texts by length so a batch never pads short functions up to a long one.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, Sequence

from vibepiler.ir.model import CALLABLE_KINDS, Node, NodeKind
from vibepiler.ir.program import Program
from vibepiler.progress import log, mem_text, progress, timed

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
            self._spec, self.max_tokens = None, None
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
        self._spec = spec
        self.max_tokens: int | None = None
        self._model = TextEmbedding(model_name=name, specific_model_path=str(directory))
        self.truncate(spec.max_length)

    def truncate(self, tokens: int) -> None:
        """Embed at most `tokens` of each text (never past the model's own context)."""
        limit = min(tokens, self._spec.max_length) if self._spec else tokens
        try:  # fastembed takes the stricter of the tokenizer config's two length keys; we set the one we mean
            self._model.model.tokenizer.enable_truncation(max_length=limit)
            self.max_tokens = limit
        except AttributeError:
            warnings.warn(f"could not set the tokenizer length for {self.name}; fastembed's internals changed", RuntimeWarning, stacklevel=2)

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


def get_embedder(model: str = DEFAULT_MODEL, max_tokens: int | None = None) -> Embedder | None:
    """The embedder for `model`, capped at `max_tokens` per text where the model can be (static models cost linear)."""
    e = EMBEDDER_FACTORY(model)
    if e is not None and max_tokens and hasattr(e, "truncate"):
        e.truncate(max_tokens)
    return e


CHARS_PER_TOKEN = 4  # code runs ~3 characters a token: cutting at 4 leaves the tokenizer the final say


def text_cap(config) -> int | None:  # noqa: ANN001 - a Config
    """Tokens embedded per function; None when `embedding_max_tokens` is 0, the model's own context is the limit."""
    return config.embedding_max_tokens or None


def chars_for(tokens: int) -> int:
    """How much of a function's text to hand the tokenizer for a cap of `tokens`."""
    return tokens * CHARS_PER_TOKEN


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
    cap = getattr(embedder, "max_tokens", None)  # the same text embeds differently under another cap
    path = cache_dir / f"{_slug(embedder.name)}{f'__{cap}t' if cap else ''}.npz"
    cached: dict[str, "np.ndarray"] = {}
    if path.is_file():
        try:
            data = np.load(path, allow_pickle=False)
            cached = dict(zip(data["hashes"].tolist(), data["vectors"]))
        except (OSError, ValueError, KeyError):
            cached = {}
    hashes = [_hash(t) for t in texts]
    missing = sorted({h for h in hashes if h not in cached})
    log.debug("cache %s: %d reused, %d to embed with %s", str(path).replace(str(Path.home()), "~", 1), len(set(hashes)) - len(missing), len(missing), embedder.name)
    if missing:
        by_hash = {_hash(t): t for t in texts}
        todo = [by_hash[h] for h in missing]
        plan, done, start = batches(todo), 0, time.perf_counter()
        saved_at, unsaved = start, 0
        try:
            for b, batch in enumerate(plan, 1):
                t = time.perf_counter()
                for k, v in zip(batch, embedder.embed([todo[k] for k in batch])):
                    cached[missing[k]] = np.asarray(v, dtype=np.float32)
                done += len(batch)
                unsaved += len(batch)
                rate = done / max(time.perf_counter() - start, 1e-9)
                log.debug("batch %d/%d  %d functions, longest %d chars  %.2f s  %s", b, len(plan), len(batch), max(len(todo[k]) for k in batch), time.perf_counter() - t, mem_text())
                progress("embedding", done, len(todo), batch=b, batches=len(plan), rate=rate)
                if time.perf_counter() - saved_at >= CHECKPOINT_SECONDS:  # a killed run keeps what it embedded
                    _save(path, cached)
                    saved_at, unsaved = time.perf_counter(), 0
        finally:  # done, stopped or failed: keep every vector embedded so far
            if unsaved:
                _save(path, cached)
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
    tokens = text_cap(program.config)
    with timed(f"model {model} loaded, up to {tokens or 'its whole context of'} tokens a function"):
        embedder = get_embedder(model, tokens)
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
    log.debug("embedding %d functions (%d lines in all)", len(nodes), sum(n.loc for n in nodes))
    vectors = embed_texts(embedder, [symbol_text(program, n, max_chars=chars_for(tokens) if tokens else None) for n in nodes])
    program.metrics["_semantic"] = {"nodes": nodes, "vectors": vectors, "model": embedder.name}
    return nodes, vectors, embedder.name


CHECKPOINT_SECONDS = 30.0  # while embedding, the cache is written at least this often


def _save(path: Path, cached: dict) -> None:
    """Write the cache whole to a temporary file, then move it into place: a run killed mid-write leaves the old one."""
    import numpy as np

    tmp = path.with_name(path.name + ".tmp.npz")
    try:
        keys = list(cached)
        np.savez(tmp, hashes=np.array(keys), vectors=np.stack([cached[k] for k in keys]))
        os.replace(tmp, path)
        log.debug("cache saved, %d vectors", len(keys))
    except OSError as exc:
        warnings.warn(f"embedding cache not written ({exc}); vectors will be recomputed next run", RuntimeWarning, stacklevel=2)


def unit_vectors(program: Program):
    """(units, unit vectors) for every class and module with at least one embedded function: the line-weighted mean of
    the functions inside it, so a unit means what its code does, all of it, with nothing more to embed. A method counts
    for every class around it. (None, None) without the embedding backend; memoized on the program."""
    memo = program.metrics.get("_semantic_units")
    if memo is not None:
        return memo
    import numpy as np

    nodes, vectors, _ = symbol_vectors(program)
    if nodes is None:
        return None, None
    g = program.graph
    members: dict[str, list[int]] = {}
    for i, n in enumerate(nodes):
        if n.module:
            members.setdefault(n.module, []).append(i)
        p = g.parent(n.id)
        while p is not None and p.kind is not NodeKind.MODULE:
            if p.kind is NodeKind.CLASS:
                members.setdefault(p.id, []).append(i)
            p = g.parent(p.id)
    units = sorted((g.node(u) for u in members), key=lambda u: (u.file or "", u.start_line or 0, u.kind.value))
    if not units:
        out = (units, np.zeros((0, vectors.shape[1]), dtype=np.float32))
    else:
        loc = np.array([float(n.loc) for n in nodes], dtype=np.float32)
        mat = np.stack([(vectors[members[u.id]] * loc[members[u.id], None]).sum(axis=0) for u in units])
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        out = (units, mat / np.where(norms > 0, norms, 1.0))
    program.metrics["_semantic_units"] = out
    return out


NEAREST_BUDGET = 16_000_000  # similarities held at once (64 MB as float32, a few hundred MB with the sort's temporaries)


def nearest(vectors, k: int, block: int | None = None):  # noqa: ANN001, ANN201 - numpy arrays
    """Each row's `k` most similar other rows, best first (ties to the lower index), worked a block of rows at a time:
    memory is block x n, never n x n; by default the block shrinks as n grows, so it stays near a fixed budget."""
    import numpy as np

    n = vectors.shape[0]
    block = block or max(1, min(2048, NEAREST_BUDGET // max(n, 1)))
    k = max(0, min(k, n - 1))
    idx, sims = np.zeros((n, k), dtype=np.int64), np.zeros((n, k), dtype=np.float32)
    if k == 0:
        return idx, sims
    for start in range(0, n, block):
        part = vectors[start : start + block] @ vectors.T
        rows = np.arange(part.shape[0])
        part[rows, start + rows] = -np.inf  # never your own neighbour
        top = np.argpartition(-part, k - 1, axis=1)[:, :k]
        val = np.take_along_axis(part, top, axis=1)
        order = np.lexsort((top, -val), axis=1)
        idx[start : start + block] = np.take_along_axis(top, order, axis=1)
        sims[start : start + block] = np.take_along_axis(val, order, axis=1)
    return idx, sims

