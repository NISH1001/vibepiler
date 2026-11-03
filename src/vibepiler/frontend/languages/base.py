"""Base class for language adapters.

An adapter knows how to turn one parsed file into `FileFacts`, how to name a
module from its path, and which module names an import could refer to. All
cross-file linking happens in the IR builder, which is language-agnostic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from tree_sitter import Node

from dataclasses import dataclass

from vibepiler.frontend.facts import FileFacts, ImportDef
from vibepiler.frontend.loader import SourceFile


@dataclass(slots=True)
class ImportOption:
    candidates: list[str]  # module names to probe, in order
    member: str | None = None  # symbol imported from the matched module (None = the module itself)
    binding_path: str | None = None  # dotted path the local name is bound to; None = the matched module


def decorator_parts(dec: Node, call_type: str) -> tuple[Node | None, Node | None]:
    """A decorator's expression and the thing it names: `@a.b(c)` -> (the call, `a.b`); `@x` -> (`x`, `x`)."""
    expr = next((c for c in dec.named_children), None)
    if expr is None:
        return None, None
    return expr, (expr.child_by_field_name("function") if expr.type == call_type else expr)


class LanguageSpec(ABC):
    name: str
    #: tree-sitter node types that add a branch to cyclomatic complexity
    branch_types: frozenset[str] = frozenset()
    #: node types that count as a nesting level
    nesting_types: frozenset[str] = frozenset()
    #: node types whose text should be normalized away for clone detection
    leaf_types: frozenset[str] = frozenset({"identifier", "string", "number", "integer", "float"})
    #: node types that define a function body (for clone detection / complexity scope)
    function_types: frozenset[str] = frozenset()

    @abstractmethod
    def module_name(self, sf: SourceFile) -> tuple[str, list[str]]:
        """Return (canonical module name, aliases)."""

    @abstractmethod
    def extractor(self, sf: SourceFile, facts: FileFacts):  # noqa: ANN201 - the language's tree walker
        """The visitor that fills `facts` from the file's syntax tree."""

    def extract(self, sf: SourceFile) -> FileFacts:
        """Extract definitions, imports, calls and references from one file."""
        name, aliases = self.module_name(sf)
        facts = FileFacts(file=sf, module_name=name, aliases=aliases, is_test=self.is_test_file(sf.rel))
        facts.attrs["loc"] = sf.lines
        ex = self.extractor(sf, facts)
        ex.visit(sf.tree.root_node, scope=None, class_scope=None)
        ex.finish()
        return facts

    @abstractmethod
    def import_targets(self, imp: ImportDef, facts: FileFacts) -> list[ImportOption]:
        """Ordered options for resolving an import inside the project (first match wins)."""

    @abstractmethod
    def package_target(self, imp: ImportDef) -> tuple[str, tuple[str, ...]] | None:
        """(package name, member prefix) when an import refers to an external package; None otherwise."""

    #: names that resolve to the language runtime rather than to project code
    builtins: frozenset[str] = frozenset()
    #: receiver names meaning "the enclosing class instance"
    self_names: frozenset[str] = frozenset()

    def is_test_file(self, rel: str) -> bool:
        return False

    def implicit_imports(self, module_name: str) -> list[str]:
        """Modules that are implicitly loaded when `module_name` is imported (python parent packages)."""
        return []

    def is_entry_module(self, rel: str) -> bool:
        """Files whose exported symbols are consumed by a framework/bundler rather than project code."""
        return False

    # --- AST hooks used by passes -------------------------------------------
    def is_branch(self, node: Node, sf: SourceFile) -> bool:
        return node.type in self.branch_types

    def body_statements(self, def_node: Node, sf: SourceFile) -> list[Node]:
        """Statements of a function body, without docstrings/comments."""
        return []

    def statement_call(self, stmt: Node, sf: SourceFile) -> Node | None:
        """If `stmt` is `return f(...)` / `f(...)` / `return await f(...)`, the call node."""
        return None

    def call_parts(self, call: Node, sf: SourceFile) -> tuple[tuple[str, ...] | None, list[str]]:
        """(callee chain, argument texts) for a call node."""
        return None, []

    def except_clauses(self, def_node: Node, sf: SourceFile) -> list[tuple[Node, list[Node], bool]]:
        """(clause, body statements, catches-everything) for every except/catch inside the function."""
        return []

    def is_stub_body(self, stmts: list[Node], sf: SourceFile) -> bool:
        return False

    def is_log_call(self, stmt: Node, sf: SourceFile) -> bool:
        call = self.statement_call(stmt, sf)
        if call is None:
            return False
        chain, _ = self.call_parts(call, sf)
        if not chain:
            return False
        head = chain[0].lower()
        return head in {"logger", "logging", "log", "console", "_logger", "print"} or (len(chain) > 1 and chain[-1] in {"error", "warning", "warn", "exception", "info", "debug"})

    # --- shared helpers -------------------------------------------------------
    @staticmethod
    def line(node: Node) -> int:
        return node.start_point[0] + 1

    @staticmethod
    def end_line(node: Node) -> int:
        return node.end_point[0] + 1
