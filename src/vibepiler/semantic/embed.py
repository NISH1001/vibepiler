"""Embeddings for symbols, with a content-addressed on-disk cache.

Backend: fastembed (ONNX runtime, no PyTorch). Default model BAAI/bge-small-en-v1.5:
33M parameters, 384 dimensions, hundreds of functions per second on a laptop CPU.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Callable, Protocol, Sequence

from vibepiler.ir.model import CALLABLE_KINDS, Node
from vibepiler.ir.program import Program

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "vibepiler" / "embeddings"


class Embedder(Protocol):
    name: str

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...


class FastEmbedEmbedder:
    def __init__(self, model: str) -> None:
        from fastembed import TextEmbedding  # type: ignore[import-not-found]

        self.name = model
        self._model = TextEmbedding(model_name=model)

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return [list(map(float, v)) for v in self._model.embed(list(texts), batch_size=64)]


def default_factory(model: str) -> Embedder | None:
    try:
        return FastEmbedEmbedder(model)
    except ImportError:
        return None


#: swapped out by tests; returns None when the optional backend is missing
EMBEDDER_FACTORY: Callable[[str], Embedder | None] = default_factory


def get_embedder(model: str = DEFAULT_MODEL) -> Embedder | None:
    return EMBEDDER_FACTORY(model)


# --------------------------------------------------------------------------- text
def symbol_text(program: Program, node: Node, max_chars: int = 2000) -> str:
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
