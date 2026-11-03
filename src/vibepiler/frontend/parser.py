"""Thin wrapper over tree-sitter: grammar lookup, parsing, and query helpers."""

from __future__ import annotations

from functools import lru_cache
from typing import Iterator

from tree_sitter import Language, Node, Parser, Query, QueryCursor, Tree
from tree_sitter_language_pack import get_language, get_parser

# vibepiler language name -> tree-sitter-language-pack grammar name
GRAMMARS: dict[str, str] = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
}


@lru_cache(maxsize=None)
def language(name: str) -> Language:
    return get_language(GRAMMARS[name])  # type: ignore[arg-type]


@lru_cache(maxsize=None)
def parser(name: str, rel: str = "") -> Parser:
    grammar = GRAMMARS[name]
    if name == "typescript" and rel.endswith(".tsx"):
        grammar = "tsx"
    if name == "javascript" and rel.endswith(".jsx"):
        grammar = "tsx"  # tsx grammar handles JSX; the javascript grammar also does, but tsx is stricter
    return get_parser(grammar)  # type: ignore[arg-type]


def parse(name: str, source: bytes, rel: str = "") -> Tree:
    p = get_parser("tsx") if (name == "typescript" and rel.endswith(".tsx")) else get_parser(GRAMMARS[name])  # type: ignore[arg-type]
    return p.parse(source)


@lru_cache(maxsize=None)
def compile_query(name: str, source: str, grammar: str | None = None) -> Query:
    lang = get_language(grammar) if grammar else language(name)  # type: ignore[arg-type]
    return Query(lang, source)


def matches(query: Query, node: Node) -> Iterator[tuple[int, dict[str, list[Node]]]]:
    cur = QueryCursor(query)
    yield from cur.matches(node)


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


def ancestors(node: Node) -> Iterator[Node]:
    cur = node.parent
    while cur is not None:
        yield cur
        cur = cur.parent
