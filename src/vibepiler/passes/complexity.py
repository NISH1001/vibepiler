"""complexity: cyclomatic and cognitive complexity, length, nesting and parameter count per function."""

from __future__ import annotations

from vibepiler.ir.model import CALLABLE_KINDS
from vibepiler.ir.program import Program
from tree_sitter import Node as TSNode

from vibepiler.frontend.languages.base import LanguageSpec
from vibepiler.frontend.loader import SourceFile
from vibepiler.passes.astutil import max_nesting
from vibepiler.passes.base import Pass
from vibepiler.passes.cognitive import cognitive
from vibepiler.passes.halstead import halstead, maintainability
from vibepiler.passes.diagnostic import Diagnostic, Severity


def cyclomatic(body: TSNode, spec: LanguageSpec, sf: SourceFile) -> int:
    """McCabe's number, 1 + decision points, counted as radon counts them; nested functions count on their own."""
    cc, stack = 1, list(body.children)
    while stack:
        n = stack.pop()
        if n.type in spec.function_types:
            continue
        if spec.is_branch(n, sf) or n.type in spec.opaque_branch_types:
            cc += 1
        if n.type not in spec.opaque_branch_types:
            stack.extend(n.children)
    return cc


class ComplexityPass(Pass):
    name = "complexity"
    description = "Cyclomatic and cognitive complexity, function length, nesting depth and parameter counts over thresholds."
    analogue = "control-flow analysis"
    cost = 1

    def run(self, program: Program) -> list[Diagnostic]:
        th = program.config.thresholds
        diags: list[Diagnostic] = []
        rows = []
        for n in program.graph.nodes(CALLABLE_KINDS):
            d = program.symbol_def(n)
            if d is None or d.node is None:
                continue
            spec = program.spec_for(n)
            sf = program.facts[n.module].file  # type: ignore[index]
            body = d.node
            cc = cyclomatic(body, spec, sf)
            nesting = max_nesting(body, spec.nesting_types, spec.function_types)
            cog = cognitive(n.name, body, spec, sf)
            n.attrs["complexity"] = cc
            n.attrs["cognitive_complexity"] = cog
            n.attrs["halstead"] = h = halstead(body, spec, sf)
            n.attrs["mi"] = maintainability(h["volume"], cc, n.loc)
            n.attrs["nesting"] = nesting
            n.attrs["loc"] = n.loc
            n.attrs["param_count"] = len(n.attrs.get("params", []))
            rows.append({"id": n.id, "qualname": n.qualname, "file": n.file, "line": n.start_line, "complexity": cc, "cognitive": cog, "loc": n.loc, "nesting": nesting})
            if cc > th.complexity:
                diags.append(
                    self.diag(
                        "high-complexity",
                        Severity.WARNING,
                        f"`{n.qualname}` has cyclomatic complexity {cc} (threshold {th.complexity})",
                        node=n,
                        suggestion="Split into smaller functions, replace branch ladders with lookup tables or early returns.",
                        complexity=cc,
                    )
                )
            if cog > th.cognitive:
                diags.append(
                    self.diag(
                        "high-cognitive-complexity",
                        Severity.WARNING,
                        f"`{n.qualname}` has cognitive complexity {cog} (threshold {th.cognitive})",
                        node=n,
                        suggestion="Flatten it: return early, pull nested blocks into named functions, split long boolean chains.",
                        cognitive=cog,
                    )
                )
            if n.loc > th.function_loc:
                diags.append(self.diag("long-function", Severity.INFO, f"`{n.qualname}` is {n.loc} lines long (threshold {th.function_loc})", node=n, loc=n.loc))
            if nesting > th.nesting:
                diags.append(self.diag("deep-nesting", Severity.INFO, f"`{n.qualname}` nests {nesting} levels deep (threshold {th.nesting})", node=n, nesting=nesting))
            params = [p for p in n.attrs.get("params", []) if p not in ("self", "cls")]
            if len(params) > th.params:
                diags.append(self.diag("too-many-params", Severity.INFO, f"`{n.qualname}` takes {len(params)} parameters (threshold {th.params})", node=n, params=params))
        rows.sort(key=lambda r: (-r["cognitive"], -r["complexity"]))
        program.metrics["complexity"] = {
            "functions": len(rows),
            "avg": round(sum(r["complexity"] for r in rows) / len(rows), 2) if rows else 0,
            "max": max((r["complexity"] for r in rows), default=0),
            "top": rows[: th.hotspot_top_n],
        }
        return diags
