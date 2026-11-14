"""Per-file facts extracted by a language adapter.

These are the "symbol table" entries for one translation unit. The IR builder
links them across files into the program graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from vibepiler.ir.model import NodeKind

if TYPE_CHECKING:
    from tree_sitter import Node

    from vibepiler.frontend.loader import SourceFile


@dataclass(slots=True)
class SymbolDef:
    kind: NodeKind
    name: str
    qualname: str
    start_line: int
    end_line: int
    node: Node | None = field(default=None, repr=False)
    parent_qualname: str | None = None
    decorators: list[str] = field(default_factory=list)
    bases: list[str] = field(default_factory=list)
    params: list[str] = field(default_factory=list)
    exported: bool = True
    is_async: bool = False
    attrs: dict[str, Any] = field(default_factory=dict)


ImportKind = Literal["module", "member", "namespace", "default", "side_effect", "wildcard", "by_name"]  # by_name: "a.b" in a string


@dataclass(slots=True)
class ImportDef:
    module: str  # raw module specifier: "a.b", "./x", "@scope/pkg"
    name: str | None = None  # member imported via `from m import name`; None for `import m`
    alias: str | None = None  # local alias, if any
    line: int = 0
    level: int = 0  # python relative import level (number of leading dots)
    kind: ImportKind = "module"
    scope: str | None = None  # qualname of enclosing def for local imports
    type_only: bool = False  # under `if TYPE_CHECKING:`: read by type checkers, never run
    reexport: bool = False  # `export ... from`: the names are passed on, not used here

    @property
    def local_name(self) -> str | None:
        """Identifier bound in the importing module's namespace."""
        if self.kind in ("wildcard", "side_effect", "by_name"):
            return None
        if self.alias:
            return self.alias
        if self.name:
            return self.name
        return self.module.split(".")[0].split("/")[0]


@dataclass(slots=True)
class CallSite:
    chain: tuple[str, ...]  # ("os", "path", "join"), ("self", "run"), ("process",)
    line: int
    scope: str | None  # qualname of enclosing def; None = module-level code
    is_new: bool = False  # constructor call (JS `new X()`)
    args: int = 0


RefContext = Literal["decorator", "argument", "base", "jsx", "export", "name", "type", "store"]  # store: an assignment target, not a read


@dataclass(slots=True)
class RefSite:
    chain: tuple[str, ...]
    line: int
    scope: str | None
    context: RefContext = "name"


HintSource = Literal["annotation", "call", "name"]


@dataclass(slots=True)
class TypeHint:
    """What a name holds, as written in the source: `x: Foo`, `x = Foo()`, `self.db: Database`, `x = make()`. The
    linker turns it into a class; a name given two different hints, or a value it cannot type, holds no known class."""

    scope: str | None  # where it is written, and where `chain` is looked up: the def a local lives in, None at module level
    name: str
    chain: tuple[str, ...]  # the annotation's dotted name, the callee, or the name copied; () = something we cannot type
    source: HintSource = "annotation"  # call: what calling `chain` returns (a class's instance, a function's return type); name: whatever `chain` holds
    of_class: str | None = None  # `self.name = ...` or a class-body `name: T`: a field of every instance of this class


@dataclass(slots=True)
class EntryHint:
    kind: str  # "main_guard", "route", "command", "test", "handler", "export_default", ...
    qualname: str | None  # None = module-level
    line: int
    detail: str = ""


@dataclass
class FileFacts:
    file: SourceFile
    module_name: str
    aliases: list[str] = field(default_factory=list)  # alternative module names (e.g. "src.app.core")
    defs: list[SymbolDef] = field(default_factory=list)
    imports: list[ImportDef] = field(default_factory=list)
    calls: list[CallSite] = field(default_factory=list)
    refs: list[RefSite] = field(default_factory=list)
    entry_hints: list[EntryHint] = field(default_factory=list)
    dispatches: list[tuple[str | None, str]] = field(default_factory=list)  # (scope, prefix): `getattr(self, f"visit_{x}")`
    escapes: list[tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...], int]] = field(default_factory=list)  # (scope, callee, receiver's constructor or (), constructor, line)
    types: list[TypeHint] = field(default_factory=list)
    has_main_guard: bool = False
    is_test: bool = False
    attrs: dict[str, Any] = field(default_factory=dict)
    _by_qualname: dict[str, SymbolDef] = field(default_factory=dict, init=False, repr=False, compare=False)
    _by_qualname_of: int = field(default=-1, init=False, repr=False, compare=False)  # len(defs) it was built from

    @property
    def rel(self) -> str:
        return self.file.rel

    @property
    def language(self) -> str:
        return self.file.language

    def defs_by_qualname(self) -> dict[str, SymbolDef]:
        """Built once; rebuilt only if a definition was added since (extraction appends, nothing removes)."""
        if self._by_qualname_of != len(self.defs):
            self._by_qualname = {d.qualname: d for d in self.defs}
            self._by_qualname_of = len(self.defs)
        return self._by_qualname
