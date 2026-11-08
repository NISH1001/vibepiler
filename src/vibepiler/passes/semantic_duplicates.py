"""semantic-duplicates (-O3): functions that do the same job with different code, by embedding similarity."""

from __future__ import annotations

from vibepiler.ir.program import Program
from vibepiler.passes.base import Pass
from vibepiler.passes.diagnostic import Diagnostic, Severity
from vibepiler.progress import timed
from vibepiler.semantic.embed import default_thresholds, symbol_vectors

SKIP_HINT = "semantic tier needs the embedding backend: pip install 'vibepiler[semantic]' (or: uv tool install 'vibepiler[semantic]')"
MAX_SYMBOLS = 6000
MAX_REPORTED = 100


class SemanticDuplicatesPass(Pass):
    name = "semantic-duplicates"
    description = "Functions whose bodies mean the same thing even though their structure differs (embedding similarity)."
    analogue = "value numbering (semantic CSE)"
    cost = 3

    def run(self, program: Program) -> list[Diagnostic]:
        th = program.config.thresholds
        nodes, vectors, model = symbol_vectors(program)
        if nodes is None:
            program.metrics["semantic_duplicates"] = {"skipped": SKIP_HINT, "model": model}
            return []
        threshold = th.semantic_similarity if th.semantic_similarity is not None else default_thresholds(program.config.embedding_model)[0]
        if len(nodes) > MAX_SYMBOLS:
            nodes, vectors = nodes[:MAX_SYMBOLS], vectors[:MAX_SYMBOLS]
        diags: list[Diagnostic] = []
        if len(nodes) >= 2:
            import numpy as np

            with timed(f"semantic-duplicates: {len(nodes)} x {len(nodes)} similarities"):
                sims = vectors @ vectors.T
                np.fill_diagonal(sims, -1.0)
            # a pair counts only when each is the other's nearest neighbour: this keeps the list
            # linear in the number of symbols instead of quadratic, and surfaces real twins.
            nearest = sims.argmax(axis=1)
            for i in range(len(nodes)):
                j = int(nearest[i])
                if j <= i or int(nearest[j]) != i:
                    continue
                s = float(sims[i, j])
                if s < threshold:
                    continue
                a, b = nodes[i], nodes[j]
                if not self._worth_reporting(a, b):
                    continue
                if a.attrs.get("shape_hash") and a.attrs.get("shape_hash") == b.attrs.get("shape_hash"):
                    continue  # the structural duplicates pass already reported this pair
                identical = s >= 0.995
                if identical and min(a.loc, b.loc) < 3:
                    continue  # two-line identical helpers are not worth a finding
                diags.append(
                    self.diag(
                        "semantic-duplicate",
                        Severity.WARNING if s >= threshold + 0.05 else Severity.INFO,
                        f"`{a.qualname}` and `{b.qualname}` are copies of each other" if identical else f"`{a.qualname}` and `{b.qualname}` do the same thing with different code ({s:.0%} similar)",
                        node=a,
                        suggestion=f"Keep one. `{a.qualname}` ({a.loc} lines) vs `{b.qualname}` ({b.loc} lines): pick the clearer one and delete the other.",
                        members=[a.qualname, b.qualname],
                        ids=[a.id, b.id],
                        locations=[a.location, b.location],
                        similarity=round(s, 3),
                        loc=min(a.loc, b.loc),
                    )
                )
        diags.sort(key=lambda d: -d.data["similarity"])
        diags = diags[:MAX_REPORTED]
        program.metrics["semantic_duplicates"] = {"pairs": len(diags), "symbols": len(nodes), "model": model, "threshold": threshold}
        return diags

    @staticmethod
    def _worth_reporting(a, b) -> bool:  # noqa: ANN001
        from vibepiler.passes.entry_points import is_dunder

        if is_dunder(a.name) or is_dunder(b.name):
            return False  # constructors / protocol methods are boilerplate by design
        if a.name == b.name and a.kind.value == "method" and b.kind.value == "method":
            return False  # same-named methods on different classes implement an interface, not a clone
        return True
