"""concepts (-O3): the codebase partitioned by meaning; concepts without a home are scattered concerns."""

from __future__ import annotations

from vibepiler.ir.program import Program
from vibepiler.passes.base import Pass
from vibepiler.passes.diagnostic import Diagnostic, Severity
from vibepiler.passes.semantic_duplicates import SKIP_HINT
from vibepiler.semantic.concepts import build_concepts

MAX_REPORTED = 10


class ConceptsPass(Pass):
    name = "concepts"
    description = "Functions grouped by meaning across the whole codebase; a concept spread over many modules is a scattered concern."
    analogue = "program partitioning / region formation"
    cost = 3

    def run(self, program: Program) -> list[Diagnostic]:
        result = build_concepts(program)
        if result is None:
            program.metrics["concepts"] = {"skipped": SKIP_HINT}
            return []
        program.metrics["concepts"] = result
        g = program.graph
        diags: list[Diagnostic] = []
        scattered = sorted((c for c in result["concepts"] if c["scattered"]), key=lambda c: (-len(c["modules"]), -c["size"]))
        for c in scattered[:MAX_REPORTED]:
            files = [g.node(m).file or g.node(m).name for m in c["modules"]]
            diags.append(
                self.diag(
                    "scattered-concept",
                    Severity.INFO,
                    f"concept `{c['label']}` ({c['size']} functions) is spread across {len(files)} modules: " + ", ".join(files[:5]) + ("…" if len(files) > 5 else ""),
                    node=g.node(c["exemplar"]),
                    suggestion="Gather these functions in one module, or name the shared concept and give it a home.",
                    concept=c["id"],
                    label=c["label"],
                    modules=files,
                    size=c["size"],
                )
            )
        return diags
