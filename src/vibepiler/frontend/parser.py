"""Thin wrapper over tree-sitter: parsing and tree traversal."""

from __future__ import annotations

from typing import Iterator

from tree_sitter import Node, Tree
from tree_sitter_language_pack import get_parser

# vibepiler language name -> tree-sitter-language-pack grammar name
GRAMMARS: dict[str, str] = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
}


def parse(name: str, source: bytes, rel: str = "") -> Tree:
    p = get_parser("tsx") if (name == "typescript" and rel.endswith(".tsx")) else get_parser(GRAMMARS[name])  # type: ignore[arg-type]
    return p.parse(source)


def walk(node: Node) -> Iterator[Node]:
    """Pre-order traversal of every node (named and anonymous)."""
    cursor = node.walk()
    reached_root = False
    while not reached_root:
        yield cursor.node
        if cursor.goto_first_child():
            continue
        if cursor.goto_next_sibling():
            continue
        while True:
            if not cursor.goto_parent():
                reached_root = True
                break
            if cursor.goto_next_sibling():
                break


def named_descendants(node: Node) -> Iterator[Node]:
    for n in walk(node):
        if n.is_named:
            yield n

