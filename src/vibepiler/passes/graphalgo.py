"""Small pure-Python graph algorithms (networkx's versions need numpy/scipy)."""

from __future__ import annotations

import networkx as nx


def pagerank(g: nx.DiGraph, alpha: float = 0.85, weight: str = "weight", max_iter: int = 100, tol: float = 1e-6) -> dict[str, float]:
    n = g.number_of_nodes()
    if n == 0:
        return {}
    nodes = list(g.nodes)
    rank = {v: 1.0 / n for v in nodes}
    out_weight = {v: sum(float(d.get(weight, 1.0)) for _, _, d in g.out_edges(v, data=True)) for v in nodes}
    for _ in range(max_iter):
        prev = rank
        rank = {v: (1.0 - alpha) / n for v in nodes}
        dangling = alpha * sum(prev[v] for v in nodes if out_weight[v] == 0) / n
        for u in nodes:
            if out_weight[u] == 0:
                continue
            share = alpha * prev[u] / out_weight[u]
            for _, v, d in g.out_edges(u, data=True):
                rank[v] += share * float(d.get(weight, 1.0))
        for v in nodes:
            rank[v] += dangling
        if sum(abs(rank[v] - prev[v]) for v in nodes) < tol:
            break
    return rank
