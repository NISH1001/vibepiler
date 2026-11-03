"""unused-imports: import bindings never used in their module."""

from __future__ import annotations

from pathlib import Path

from vibepiler.ir.model import NodeKind
from vibepiler.ir.program import Program
from vibepiler.passes.base import Pass
from vibepiler.passes.diagnostic import Diagnostic, Severity


class UnusedImportsPass(Pass):
    name = "unused-imports"
    description = "Imports whose bound name is never used in the importing file."
    analogue = "unused-declaration elimination"
    cost = 1

    def run(self, program: Program) -> list[Diagnostic]:
        diags: list[Diagnostic] = []
        for m in program.graph.nodes(NodeKind.MODULE):
            fname = Path(m.file or "").name
            if fname in {"__init__.py", "index.js", "index.ts", "index.tsx", "index.jsx", "conftest.py"}:
                continue
            all_names = set(m.attrs.get("__all__") or [])
            for rec in m.attrs.get("imports", []):
                if rec["used"] or rec["kind"] in ("wildcard", "side_effect"):
                    continue
                local = rec["local_name"]
                if local is None or local in all_names or rec["module"] == "__future__":
                    continue
                if rec["alias"] and rec["alias"] == rec["name"]:
                    continue  # explicit re-export `from x import y as y`
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
