"""Nearest-neighbour queries over symbol embeddings (powers `vibepiler similar`)."""

from __future__ import annotations

from vibepiler.ir.model import Node
from vibepiler.ir.program import Program
from vibepiler.semantic.embed import symbol_vectors


def similar_symbols(program: Program, node_id: str, top: int = 10) -> list[tuple[Node, float]]:
    nodes, vectors, _ = symbol_vectors(program)
    if nodes is None:
        raise RuntimeError("embedding backend not available; install vibepiler[semantic]")
    index = {n.id: i for i, n in enumerate(nodes)}
    if node_id not in index:
        raise KeyError(f"{node_id} is not an embeddable function/method")
    sims = vectors @ vectors[index[node_id]]
    order = sims.argsort()[::-1]
    out = []
    for i in order:
        if nodes[i].id == node_id:
            continue
        out.append((nodes[i], float(sims[i])))
        if len(out) >= top:
            break
    return out
