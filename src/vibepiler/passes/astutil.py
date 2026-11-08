"""Small tree-sitter helpers shared by passes."""

from __future__ import annotations

from tree_sitter import Node


def type_sequence(node: Node, leaf_types: frozenset[str]) -> list[str]:
    """Structural fingerprint: node types in pre-order, identifiers/literals normalized to their type."""
    seq: list[str] = []
    stack = [node]
    while stack:
        cur = stack.pop()
        if cur.type in leaf_types:
            seq.append(cur.type)
            continue
        if cur.is_named or not cur.type.isalnum():
            seq.append(cur.type)
        for child in reversed(cur.children):
            if child.type in {"comment", "(", ")", ",", ":", ";", "{", "}", "[", "]"}:
                continue
            stack.append(child)
    return seq



def max_nesting(node: Node, nesting_types: frozenset[str], stop_types: frozenset[str]) -> int:
    best = 0
    stack: list[tuple[Node, int]] = [(node, 0)]
    while stack:
        cur, depth = stack.pop()
        for child in cur.children:
            if child.type in stop_types and child is not node:
                continue
            d = depth + (1 if child.type in nesting_types else 0)
            best = max(best, d)
            stack.append((child, d))
    return best
