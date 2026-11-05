"""duplicates: structurally identical or near-identical functions."""

from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict

from vibepiler.ir.model import CALLABLE_KINDS, Node
from vibepiler.ir.program import Program
from vibepiler.passes.astutil import type_sequence
from vibepiler.passes.base import Pass
from vibepiler.passes.diagnostic import Diagnostic, Severity

MAX_LENGTH_RATIO = 1.3  # a near copy is at most this much longer than its twin


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
                    ids=[m.id for m in members],
                    locations=[m.location for m in members],
                    loc=sum(m.loc for m in members),
                )
            )

        near = 0
        shingled = [
            (n, self._shingles(seq), len(seq))
            for n, seq in entries
            if n.id not in exact_members and not g.node(n.module).attrs.get("is_test")  # type: ignore[arg-type]
        ]
        pairs = []
        for i, j, sim in similar_pairs([sh for _, sh, _ in shingled], th.near_duplicate_similarity):
            (a, _, la), (b, _, lb) = shingled[i], shingled[j]
            if max(la, lb) > min(la, lb) * MAX_LENGTH_RATIO or (a.file == b.file and a.start_line == b.start_line):
                continue
            if (a.file or "", a.start_line or 0) > (b.file or "", b.start_line or 0):
                a, b = b, a
            pairs.append((a, b, sim))
        pairs.sort(key=lambda t: (t[0].file or "", t[0].start_line or 0, t[1].file or "", t[1].start_line or 0))
        for a, b, sim in pairs:
            near += 1
            diags.append(
                self.diag(
                    "near-duplicate-function",
                    Severity.INFO,
                    f"`{a.qualname}` and `{b.qualname}` are {sim:.0%} structurally similar",
                    node=a,
                    suggestion="Consider merging them behind a parameter.",
                    members=[a.qualname, b.qualname],
                    ids=[a.id, b.id],
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


def similar_pairs(sets: list[set[str]], threshold: float) -> list[tuple[int, int, float]]:
    """Every pair of sets with Jaccard similarity >= threshold, exactly, without comparing all pairs: prefix
    filtering (the All-Pairs similarity join). Each set's tokens are ordered rarest first; two sets that similar
    must share a token among the first |s| - ceil(t|s|) + 1 of each, so only sets sharing a rare token meet."""
    freq = Counter(tok for s in sets for tok in s)
    index: dict[str, list[int]] = defaultdict(list)
    out: list[tuple[int, int, float]] = []
    for i in sorted(range(len(sets)), key=lambda k: (len(sets[k]), k)):  # smaller sets first: each pair meets once
        s = sets[i]
        n = len(s)
        if not n:
            continue
        prefix = n - math.ceil(threshold * n - 1e-9) + 1
        candidates: set[int] = set()
        for tok in sorted(s, key=lambda w: (freq[w], w))[:prefix]:
            for j in index[tok]:
                if len(sets[j]) >= threshold * n - 1e-9:  # a set smaller than t|s| cannot reach t
                    candidates.add(j)
            index[tok].append(i)
        for j in candidates:
            inter = len(s & sets[j])
            sim = inter / (n + len(sets[j]) - inter)
            if sim >= threshold:
                out.append((min(i, j), max(i, j), sim))
    return sorted(out)
