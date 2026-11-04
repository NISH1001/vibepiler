"""Flow shapes: what a statement does to control flow and to names, for the liveness dataflow.

Each language adapter classifies a statement into one of six shapes; the engine in `passes/dataflow.py` folds them
backward to a fixpoint without knowing which language it is looking at.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from tree_sitter import Node

DefKind = Literal["param", "local", "attribute", "destructure", "loop", "with", "except"]
GLOBAL = "global:"  # a Def named "global:x" marks `global x` / `nonlocal x`: x is never a local store in this function


@dataclass(slots=True)
class Def:
    name: str
    node: Node  # the statement (or parameter) that makes the definition
    kind: DefKind
    group: int = 0  # destructuring siblings share a group id; 0 = none
    rhs_has_call: bool = False  # the right-hand side contains a call, await or yield
    attr_of: str | None = None  # "self" / "this" for an attribute store


@dataclass(slots=True)
class Plain:
    node: Node
    defs: list[Def] = field(default_factory=list)
    uses: set[str] = field(default_factory=set)


@dataclass(slots=True)
class Branch:
    node: Node
    uses: set[str]
    arms: list[tuple[Node, list[Node]]]  # (clause, statements) in source order; the else arm last when has_else
    has_else: bool
    constant: bool | None = None  # the first condition's literal value, if it is a literal


@dataclass(slots=True)
class Loop:
    node: Node
    defs: list[Def]
    uses: set[str]
    body: list[Node]
    else_body: list[Node] = field(default_factory=list)
    constant: bool | None = None
    label: str | None = None


@dataclass(slots=True)
class Try:
    node: Node
    body: list[Node]
    handlers: list[tuple[list[Def], list[Node]]]
    else_body: list[Node] = field(default_factory=list)
    final: list[Node] = field(default_factory=list)


@dataclass(slots=True)
class With:
    node: Node
    defs: list[Def]
    uses: set[str]
    body: list[Node]


@dataclass(slots=True)
class Jump:
    node: Node
    kind: Literal["return", "raise", "break", "continue"]
    uses: set[str] = field(default_factory=set)
    label: str | None = None


Flow = Plain | Branch | Loop | Try | With | Jump


def contains_any(node: Node, types: frozenset[str]) -> bool:
    """True when `node` or anything under it has one of `types`."""
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type in types:
            return True
        stack.extend(n.children)
    return False


def statements(block: Node | None) -> list[Node]:
    """The statements of a block, without comments."""
    return [c for c in block.named_children if c.type != "comment"] if block is not None else []
