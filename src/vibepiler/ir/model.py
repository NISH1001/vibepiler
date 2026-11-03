"""Node and edge model for the program graph.

This is the "IR" of vibepiler. Everything the frontend extracts and everything
the passes compute is stored on these two dataclasses so that any backend can
serialize the whole state without knowing which pass produced it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class NodeKind(StrEnum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    VARIABLE = "variable"
    PACKAGE = "package"  # external dependency (not parsed)


class EdgeKind(StrEnum):
    CONTAINS = "contains"  # module -> symbol, class -> method
    IMPORTS = "imports"  # module -> module | package
    CALLS = "calls"  # function|module -> function|class|package
    INHERITS = "inherits"  # class -> class
    REFERENCES = "references"  # symbol|module -> symbol (non-call use)


CALLABLE_KINDS = frozenset({NodeKind.FUNCTION, NodeKind.METHOD})


@dataclass(slots=True)
class Node:
    id: str
    kind: NodeKind
    name: str
    qualname: str = ""
    module: str | None = None  # id of the owning module node
    file: str | None = None  # path relative to project root
    language: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    attrs: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.kind = NodeKind(self.kind)
        if not self.qualname:
            self.qualname = self.name

    @property
    def loc(self) -> int:
        if self.start_line is None or self.end_line is None:
            return 0
        return self.end_line - self.start_line + 1

    @property
    def location(self) -> str:
        if self.file is None:
            return self.name
        if self.start_line is None:
            return self.file
        return f"{self.file}:{self.start_line}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "name": self.name,
            "qualname": self.qualname,
            "module": self.module,
            "file": self.file,
            "language": self.language,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "attrs": self.attrs,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Node:
        return cls(**d)


@dataclass(slots=True)
class Edge:
    src: str
    dst: str
    kind: EdgeKind
    line: int | None = None
    count: int = 1
    attrs: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.kind = EdgeKind(self.kind)

    def to_dict(self) -> dict[str, Any]:
        return {
            "src": self.src,
            "dst": self.dst,
            "kind": self.kind.value,
            "line": self.line,
            "count": self.count,
            "attrs": self.attrs,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Edge:
        return cls(**d)
