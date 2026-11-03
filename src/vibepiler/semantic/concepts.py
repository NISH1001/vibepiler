"""Concept layer (-O3): every function in the codebase grouped by meaning, laid out as a map.

A concept is a community in the k-nearest-neighbour graph of function embeddings (Louvain with a
fixed seed, so the result is stable), labelled by the identifier words its members share. Edges
between concepts count the call edges between their members. Positions are a 2-D projection (PCA)
of the concept centroids: nearby concepts mean similar things. A concept whose members live in many
modules with no clear home is a scattered concern; a module split over many concepts is a mixed one.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from vibepiler.ir.model import EdgeKind
from vibepiler.ir.program import Program
from vibepiler.semantic.embed import symbol_vectors, words

KNN = 8  # neighbours per function in the similarity graph
MIN_SIM = 0.15  # weaker neighbours carry no meaning
MIN_CONCEPT = 3  # smaller communities are noise
RESOLUTION = 2.0  # Louvain resolution: higher = more, smaller concepts; 2.0 gives design-area sized groups
MAX_LABEL_WORDS = 3
SCATTER_MODULES = 3  # a concept over at least this many modules...
SCATTER_SHARE = 0.34  # ...none holding even a third of it, is scattered...
PATTERN_SHARE = 0.5  # ...unless half its members share one name: that is an interface implemented everywhere
UMAP_MIN_POINTS = 8  # below this UMAP has nothing to learn; PCA is used

STOP = frozenset("get set is has to from of the a an self cls init new do and or for with in on by as at it its this that my our not no if".split())


def identifier_words(name: str) -> list[str]:
    """Meaningful words of an identifier: snake/camel split, stop words and version tags dropped."""
    out = []
    for w in words(name).split():
        if w in STOP or len(w) < 2 or re.fullmatch(r"v?\d+", w):
            continue
        out.append(w)
    return out


def identifier_idf(names: list[str]) -> tuple[list[list[str]], dict[str, float]]:
    """Words per name, and an inverse-document-frequency weight per word over all names."""
    docs = [identifier_words(n) for n in names]
    df = Counter(w for ws in docs for w in set(ws))
    n = len(docs)
    return docs, {w: math.log((1 + n) / (1 + c)) + 1.0 for w, c in df.items()}


def label_for(docs: list[list[str]], idf: dict[str, float], k: int = MAX_LABEL_WORDS) -> tuple[str, list[str]]:
    """The words that best describe a group: frequent inside it, rare elsewhere."""
    tf = Counter(w for ws in docs for w in set(ws))
    top = sorted(tf, key=lambda w: (-(tf[w] * idf.get(w, 1.0)), w))[:k]
    return " · ".join(top), top


def _communities(vectors) -> list[list[int]]:  # noqa: ANN001
    """Louvain communities of the k-nearest-neighbour graph over the vectors; stable because seeded."""
    import networkx as nx
    import numpy as np
    from networkx.algorithms.community import louvain_communities

    n = vectors.shape[0]
    sims = vectors @ vectors.T
    np.fill_diagonal(sims, -1.0)
    k = min(KNN, n - 1)
    G = nx.Graph()
    G.add_nodes_from(range(n))
    for i in range(n):
        for j in np.argsort(-sims[i], kind="stable")[:k]:
            s = float(sims[i, j])
            if s >= MIN_SIM:
                j = int(j)
                G.add_edge(i, j, weight=max(s, G[i][j]["weight"] if G.has_edge(i, j) else 0.0))
    comms = [sorted(c) for c in louvain_communities(G, weight="weight", resolution=RESOLUTION, seed=0) if len(c) >= MIN_CONCEPT]
    return sorted(comms, key=lambda c: (-len(c), c[0]))


def _describe(ci: int, members: list[int], nodes, vectors, docs, idf):  # noqa: ANN001
    """One concept: its label, exemplar, module spread, cohesion, and whether it is scattered or a pattern."""
    import numpy as np

    vecs = vectors[members]
    centroid = vecs.mean(axis=0)
    centroid = centroid / (np.linalg.norm(centroid) or 1.0)
    sim_c = vecs @ centroid
    exemplar = nodes[members[int(np.argmax(sim_c))]]
    label, top = label_for([docs[i] for i in members], idf)
    mods = Counter(nodes[i].module for i in members)
    names = Counter(nodes[i].name for i in members)
    pattern = names.most_common(1)[0][1] / len(members) >= PATTERN_SHARE
    concept = {
        "id": f"concept:{ci}",
        "label": label or exemplar.name,
        "words": top,
        "exemplar": exemplar.id,
        "size": len(members),
        "members": [nodes[i].id for i in members],
        "modules": dict(mods.most_common()),
        "cohesion": round(float(sim_c.mean()), 3),
        "scattered": len(mods) >= SCATTER_MODULES and max(mods.values()) / len(members) < SCATTER_SHARE and not pattern,
        "pattern": names.most_common(1)[0][0] if pattern else None,
    }
    return concept, centroid


def _call_edges(program: Program, concepts: list[dict]) -> list[dict]:
    owner = {m: c["id"] for c in concepts for m in c["members"]}
    counts: Counter = Counter()
    for e in program.graph.edges(EdgeKind.CALLS):
        a, b = owner.get(e.src), owner.get(e.dst)
        if a and b and a != b:
            counts[(a, b)] += e.count or 1
    return [{"src": a, "dst": b, "calls": c} for (a, b), c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def build_concepts(program: Program) -> dict | None:
    """Cluster the embedded functions into concepts; None when the embedding backend is missing."""
    nodes, vectors, model = symbol_vectors(program)
    if nodes is None:
        return None
    import numpy as np

    result: dict = {"model": model, "symbols": len(nodes), "clustered": 0, "concepts": [], "edges": []}
    if len(nodes) < MIN_CONCEPT:
        return result
    docs, idf = identifier_idf([nd.qualname for nd in nodes])
    described = [_describe(ci, c, nodes, vectors, docs, idf) for ci, c in enumerate(_communities(vectors))]
    concepts = [c for c, _ in described]
    xy, result["projection"] = project_2d(np.stack([v for _, v in described])) if described else (np.zeros((0, 2)), "none")
    for c, p in zip(concepts, xy):
        c["x"], c["y"] = round(float(p[0]), 4), round(float(p[1]), 4)
    result["edges"] = _call_edges(program, concepts)
    result["concepts"] = concepts
    result["clustered"] = sum(c["size"] for c in concepts)
    return result


def project_2d(M, prefer_umap: bool = True):  # noqa: ANN001
    """Centroids -> points in the unit square. UMAP when `vibepiler[map]` is installed and there are enough
    points (seeded, so the map is the same every run), otherwise PCA with signs fixed. Returns (xy, method)."""
    import numpy as np

    n = M.shape[0]
    if n == 0:
        return np.zeros((0, 2)), "none"
    if n == 1:
        return np.full((1, 2), 0.5), "none"
    xy, method = None, "pca"
    if prefer_umap and n >= UMAP_MIN_POINTS:
        try:
            import umap  # type: ignore[import-not-found]

            xy = umap.UMAP(n_components=2, n_neighbors=min(15, n - 1), min_dist=0.3, metric="cosine", random_state=0).fit_transform(M)
            method = "umap"
        except ImportError:
            xy = None
    if xy is None:
        C = M - M.mean(axis=0)
        U, S, _ = np.linalg.svd(C, full_matrices=False)
        xy = U[:, :2] * S[:2]
        if xy.shape[1] < 2:
            xy = np.hstack([xy, np.zeros((n, 1))])
        for d in range(2):
            if xy[int(np.argmax(np.abs(xy[:, d]))), d] < 0:
                xy[:, d] *= -1
    xy = np.asarray(xy, dtype=float)
    lo, hi = xy.min(axis=0), xy.max(axis=0)
    return (xy - lo) / np.where(hi - lo > 0, hi - lo, 1.0), method
