"""duplicates: structurally identical or near-identical functions."""

from __future__ import annotations

import hashlib
from collections import defaultdict

from vibepiler.ir.model import CALLABLE_KINDS, Node
from vibepiler.ir.program import Program
from vibepiler.passes.astutil import type_sequence
from vibepiler.passes.base import Pass
from vibepiler.passes.diagnostic import Diagnostic, Severity

MAX_NEAR_CANDIDATES = 4000


class DuplicatesPass(Pass):
    name = "duplicates"
    description = "Functions whose structure is identical (ignoring names/literals) or nearly identical."
    analogue = "common-subexpression elimination"
    cost = 2

    def run(self, program: Program) -> list[Diagnostic]:
        th = program.config.thresholds
        g = program.graph
        entries: list[tuple[Node, list[str]]] = []
        for n in g.nodes(CALLABLE_KINDS):
            d = program.symbol_def(n)
            if d is None or d.node is None:
                continue
            if g.node(n.module).attrs.get("is_migration"):  # type: ignore[arg-type]
                continue  # each migration is frozen once written: repeating the last one's shape is the point
            spec = program.spec_for(n)
            body = d.node.child_by_field_name("body") or d.node
            seq = type_sequence(body, spec.leaf_types)
            if len(seq) < th.duplicate_min_nodes:
                continue
            entries.append((n, seq))

        diags: list[Diagnostic] = []
        exact: dict[str, list[Node]] = defaultdict(list)
        for n, seq in entries:
            h = hashlib.sha1(" ".join(seq).encode()).hexdigest()
            n.attrs["shape_hash"] = h[:12]
            exact[h].append(n)
        exact_members: set[str] = set()
        dup_loc = 0
        for h, members in exact.items():
            if len(members) < 2:
                continue
            members.sort(key=lambda m: (m.file or "", m.start_line or 0))
            exact_members.update(m.id for m in members)
            dup_loc += sum(m.loc for m in members[1:])
            first = members[0]
            diags.append(
                self.diag(
                    "duplicate-function",
                    Severity.WARNING,
                    f"{len(members)} structurally identical functions: " + ", ".join(f"`{m.qualname}`" for m in members),
                    node=first,
                    suggestion=f"Keep one and delete the rest ({sum(m.loc for m in members[1:])} lines), or parameterize the differences.",
                    members=[m.qualname for m in members],
                    locations=[m.location for m in members],
                    loc=sum(m.loc for m in members),
                )
            )

        near = 0
        if len(entries) <= MAX_NEAR_CANDIDATES:
            shingled = [
                (n, self._shingles(seq), len(seq))
                for n, seq in entries
                if n.id not in exact_members and not g.node(n.module).attrs.get("is_test")  # type: ignore[arg-type]
            ]
            shingled.sort(key=lambda t: t[2])
            for i, (a, sa, la) in enumerate(shingled):
                for b, sb, lb in shingled[i + 1 :]:
                    if lb > la * 1.3:
                        break
                    if a.file == b.file and a.start_line == b.start_line:
                        continue
                    inter = len(sa & sb)
                    if not inter:
                        continue
                    sim = inter / len(sa | sb)
                    if sim >= th.near_duplicate_similarity:
                        near += 1
                        diags.append(
                            self.diag(
                                "near-duplicate-function",
                                Severity.INFO,
                                f"`{a.qualname}` and `{b.qualname}` are {sim:.0%} structurally similar",
                                node=a,
                                suggestion="Consider merging them behind a parameter.",
                                members=[a.qualname, b.qualname],
                                locations=[a.location, b.location],
                                similarity=round(sim, 3),
                            )
                        )
        program.metrics["duplicates"] = {
            "exact_groups": sum(1 for d in diags if d.code == "duplicate-function"),
            "near_pairs": near,
            "removable_loc": dup_loc,
            "candidates": len(entries),
        }
        return diags

    @staticmethod
    def _shingles(seq: list[str], k: int = 4) -> set[str]:
        return {" ".join(seq[i : i + k]) for i in range(max(1, len(seq) - k + 1))}
