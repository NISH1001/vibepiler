"""unused-imports: import bindings never used in their module."""

from __future__ import annotations

from pathlib import Path

from vibepiler.ir.model import Node, NodeKind
from vibepiler.ir.program import Program
from vibepiler.passes.base import Pass
from vibepiler.passes.diagnostic import Diagnostic, Severity


REEXPORT_FILES = frozenset({"__init__.py", "index.js", "index.ts", "index.tsx", "index.jsx", "conftest.py"})


def is_binding(rec: dict) -> bool:
    """An import that binds a name a reader could use: not `*`, not a side-effect import, not `__future__`."""
    return rec["local_name"] is not None and rec["kind"] not in ("wildcard", "side_effect") and rec["module"] != "__future__"


def is_unused_import(m: Node, rec: dict) -> bool:
    """The one rule for an unused import, shared by this pass and the liveness metric."""
    if rec["used"] or not is_binding(rec) or Path(m.file or "").name in REEXPORT_FILES:
        return False
    if rec["local_name"] in set(m.attrs.get("__all__") or []):
        return False
    return not (rec["alias"] and rec["alias"] == rec["name"])  # `from x import y as y` is an explicit re-export


class UnusedImportsPass(Pass):
    name = "unused-imports"
    description = "Imports whose bound name is never used in the importing file."
    analogue = "unused-declaration elimination"
    cost = 1

    def run(self, program: Program) -> list[Diagnostic]:
        diags: list[Diagnostic] = []
        for m in program.graph.nodes(NodeKind.MODULE):
            for rec in m.attrs.get("imports", []):
                if not is_unused_import(m, rec):
                    continue
                local = rec["local_name"]
                diags.append(
                    self.diag(
                        "unused-import",
                        Severity.INFO,
                        f"`{local}` is imported but never used",
                        file=m.file,
                        line=rec["line"],
                        suggestion="Remove the import.",
                        local_name=local,
                        module=rec["module"],
                        name=rec["name"],
                        qualname=f"{m.qualname}:{local}",
                    )
                )
        program.metrics["unused_imports"] = {"count": len(diags)}
        return diags
