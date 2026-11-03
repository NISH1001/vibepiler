"""responsibilities (-O3): modules whose functions fall into several unrelated groups."""

from __future__ import annotations

from vibepiler.ir.model import NodeKind
from vibepiler.ir.program import Program
from vibepiler.passes.base import Pass
from vibepiler.passes.diagnostic import Diagnostic, Severity
from vibepiler.passes.semantic_duplicates import SKIP_HINT
from vibepiler.semantic.embed import symbol_vectors

MIN_SYMBOLS = 6
MIN_CLUSTER = 3
MAX_REPORTED = 25


class ResponsibilitiesPass(Pass):
    name = "responsibilities"
    description = "Modules that mix unrelated concerns: their functions cluster into several groups by meaning."
    analogue = "code layout / function partitioning"
    cost = 3

    def run(self, program: Program) -> list[Diagnostic]:
        th = program.config.thresholds
        nodes, vectors, model = symbol_vectors(program)
        if nodes is None:
            program.metrics["responsibilities"] = {"skipped": SKIP_HINT}
            return []
        import numpy as np

        by_module: dict[str, list[int]] = {}
        for i, n in enumerate(nodes):
            if n.module:
                by_module.setdefault(n.module, []).append(i)
        diags: list[Diagnostic] = []
        for mid, idxs in by_module.items():
            if len(idxs) < MIN_SYMBOLS:
                continue
            clusters = self._cluster(vectors[idxs], th.responsibility_similarity)
            big = [c for c in clusters if len(c) >= MIN_CLUSTER]
            if len(big) < 2:
                continue
            mod = program.graph.node(mid)
            groups = []
            for c in sorted(big, key=len, reverse=True):
                members = [nodes[idxs[k]] for k in c]
                groups.append({"members": [m.qualname for m in members], "lines": sum(m.loc for m in members)})
            cohesion = float(np.mean(vectors[idxs] @ vectors[idxs].T))
            diags.append(
                self.diag(
                    "mixed-responsibilities",
                    Severity.INFO,
                    f"`{mod.name}` mixes {len(big)} unrelated groups of functions: " + "; ".join("[" + ", ".join(g["members"][:3]) + ("…" if len(g["members"]) > 3 else "") + "]" for g in groups),
                    node=mod,
                    suggestion="Split the module along those groups, or move the odd group next to the code that uses it.",
                    clusters=groups,
                    cohesion=round(cohesion, 3),
                    qualname=mod.name,
                )
            )
        diags.sort(key=lambda d: d.data["cohesion"])
        diags = diags[:MAX_REPORTED]
        program.metrics["responsibilities"] = {"modules": len(diags), "model": model}
        return diags

    @staticmethod
    def _cluster(vectors, threshold: float) -> list[list[int]]:  # noqa: ANN001
        """Greedy centroid clustering: deterministic, O(n·k), good enough for 'are there several groups here'."""
        import numpy as np

        clusters: list[list[int]] = []
        centroids: list = []
        for i in range(vectors.shape[0]):
            v = vectors[i]
            best, best_sim = -1, threshold
            for k, c in enumerate(centroids):
                s = float(v @ c / (np.linalg.norm(c) or 1.0))
                if s > best_sim:
                    best, best_sim = k, s
            if best < 0:
                clusters.append([i])
                centroids.append(v.copy())
            else:
                clusters[best].append(i)
                centroids[best] = centroids[best] + v
        return clusters
