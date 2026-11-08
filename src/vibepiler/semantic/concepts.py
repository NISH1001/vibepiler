"""Concept layer (-O3): every function in the codebase grouped by meaning, laid out as a map.

A concept is a community in the k-nearest-neighbour graph of function embeddings (Louvain with a
fixed seed, so the result is stable), labelled by the identifier words its members share. Concepts
whose centres are close form a theme, the coarse level of the map. Every function gets a point: a
2-D projection of its embedding (UMAP when installed, else PCA), so distance on the map is distance
in meaning; a concept or theme is labelled at its middle member. Edges between concepts count the call
edges between their members. A concept whose members live in many modules with no clear home is a
scattered concern; a module split over many concepts is a mixed one.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from vibepiler.ir.model import EdgeKind
from vibepiler.ir.program import Program
from vibepiler.progress import timed
from vibepiler.semantic.embed import nearest, symbol_vectors, words

KNN = 8  # neighbours per function in the similarity graph
MIN_SIM = 0.15  # weaker neighbours carry no meaning
MIN_CONCEPT = 3  # smaller communities are noise
RESOLUTION = (1.0, 6.0, 500)  # Louvain resolution grows from 1 to 6 at one step per 500 functions: big repos get concepts
# of a few to a few dozen functions, not one per design area; small ones keep groups big enough to mean something
THEME_NEIGHBOURS = 4  # each concept links to its nearest concepts by meaning...
THEME_SIM = 0.55  # ...when their centres are at least this similar; communities of that graph are themes
MAX_LABEL_WORDS = 3
SCATTER_MODULES = 3  # a concept over at least this many modules...
SCATTER_SHARE = 0.34  # ...none holding even a third of it, is scattered...
PATTERN_SHARE = 0.5  # ...unless half its members share one name: that is an interface implemented everywhere
UMAP_MIN_POINTS = 8  # below this UMAP has nothing to learn; PCA is used

STOP = frozenset("get set is has to from of the a an self cls init new do and or for with in on by as at it its this that my our not no if use".split())


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
    top: list[str] = []
    for w in sorted(tf, key=lambda w: (-(tf[w] * idf.get(w, 1.0)), w)):
        if _singular(w) not in {_singular(t) for t in top}:
            top.append(w)
        if len(top) == k:
            break
    return " · ".join(top), top


def _singular(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def _communities(vectors) -> list[list[int]]:  # noqa: ANN001
    """Louvain communities of the k-nearest-neighbour graph over the vectors; stable because seeded."""
    import networkx as nx
    from networkx.algorithms.community import louvain_communities

    n = vectors.shape[0]
    idx, sims = nearest(vectors, KNN)  # a block of rows at a time: memory block x n, not n x n
    G = nx.Graph()
    G.add_nodes_from(range(n))
    for i in range(n):
        for j, s in zip(idx[i].tolist(), sims[i].tolist()):
            if s >= MIN_SIM:
                G.add_edge(i, j, weight=max(s, G[i][j]["weight"] if G.has_edge(i, j) else 0.0))
    comms = [sorted(c) for c in louvain_communities(G, weight="weight", resolution=_resolution(n), seed=0) if len(c) >= MIN_CONCEPT]
    return sorted(comms, key=lambda c: (-len(c), c[0]))


def _resolution(n: int) -> float:
    lo, hi, per = RESOLUTION
    return max(lo, min(hi, n / per))


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
    """Cluster the embedded functions into concepts and themes and place every function on the map;
    None when the embedding backend is missing."""
    nodes, vectors, model = symbol_vectors(program)
    if nodes is None:
        return None

    result: dict = {"model": model, "symbols": len(nodes), "clustered": 0, "concepts": [], "themes": [], "points": [], "edges": []}
    if len(nodes) < MIN_CONCEPT:
        return result
    docs, idf = identifier_idf([nd.qualname for nd in nodes])
    with timed(f"concepts: {len(nodes)} functions grouped"):
        described = [_describe(ci, c, nodes, vectors, docs, idf) for ci, c in enumerate(_communities(vectors))]
    concepts = [c for c, _ in described]
    with timed("concepts: map projected"):
        xy, result["projection"] = project_2d(vectors, min_dist=0.08)
    owner = {m: ci for ci, c in enumerate(concepts) for m in c["members"]}
    result["points"] = [{"id": nd.id, "x": round(float(x), 4), "y": round(float(y), 4), "c": owner.get(nd.id, -1)} for nd, (x, y) in zip(nodes, xy)]
    row = {nd.id: i for i, nd in enumerate(nodes)}
    for c in concepts:
        c["x"], c["y"] = _medoid(xy[[row[m] for m in c["members"]]])
    result["themes"] = _themes(concepts, [v for _, v in described], docs, idf, row, xy)
    result["edges"] = _call_edges(program, concepts)
    result["concepts"] = concepts
    result["clustered"] = sum(c["size"] for c in concepts)
    return result


def _themes(concepts: list[dict], centroids, docs, idf, row: dict[str, int], xy) -> list[dict]:  # noqa: ANN001
    """Concepts whose centres are close, grouped (Louvain over the concepts' nearest-neighbour graph)."""
    if not concepts:
        return []
    import networkx as nx
    import numpy as np
    from networkx.algorithms.community import louvain_communities

    C = np.stack(centroids)
    sims = C @ C.T
    np.fill_diagonal(sims, -1.0)
    G = nx.Graph()
    G.add_nodes_from(range(len(concepts)))
    for i in range(len(concepts)):
        for j in np.argsort(-sims[i], kind="stable")[:THEME_NEIGHBOURS]:
            if sims[i, j] >= THEME_SIM:
                G.add_edge(i, int(j), weight=float(sims[i, j]))
    groups = [sorted(g, key=lambda ci: -concepts[ci]["size"]) for g in louvain_communities(G, weight="weight", seed=0)]
    groups.sort(key=lambda g: (-sum(concepts[ci]["size"] for ci in g), g[0]))
    themes = []
    for ti, g in enumerate(groups):
        members = [m for ci in g for m in concepts[ci]["members"]]
        label, _ = label_for([docs[row[m]] for m in members], idf, k=2)
        size = len(members)
        for ci in g:
            concepts[ci]["theme"] = ti
        x, y = _medoid(xy[[row[m] for m in members]])
        themes.append({"id": f"theme:{ti}", "label": label or concepts[g[0]]["label"], "size": size, "concepts": g, "x": x, "y": y})
    return themes


def _medoid(pts) -> tuple[float, float]:  # noqa: ANN001
    """The member at the middle of a group on the map: a concept spread over the map is labelled where it actually
    has a function, never at an empty average between its clumps."""
    import numpy as np

    sample = pts if len(pts) <= 400 else pts[np.linspace(0, len(pts) - 1, 400).astype(int)]
    d = np.linalg.norm(sample[:, None, :] - sample[None, :, :], axis=2).sum(axis=1)
    x, y = sample[int(np.argmin(d))]
    return round(float(x), 4), round(float(y), 4)


def project_2d(M, prefer_umap: bool = True, min_dist: float = 0.3):  # noqa: ANN001
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

            xy = umap.UMAP(n_components=2, n_neighbors=min(15, n - 1), min_dist=min_dist, metric="cosine", random_state=0).fit_transform(M)
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
