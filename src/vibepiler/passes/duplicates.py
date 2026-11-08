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
        entries = self._entries(program)
        exact, dup_loc = self._exact(entries)
        exact_members = {i for d in exact for i in d.data["ids"]}
        near = self._near(program, [(n, seq) for n, seq in entries if n.id not in exact_members])
        program.metrics["duplicates"] = {"exact_groups": len(exact), "near_pairs": len(near), "removable_loc": dup_loc, "candidates": len(entries)}
        return exact + near

    @staticmethod
    def _entries(program: Program) -> list[tuple[Node, list[str]]]:
        """Every function big enough to compare, with its structural fingerprint; migrations are left alone."""
        g, min_nodes = program.graph, program.config.thresholds.duplicate_min_nodes
        entries = []
        for n in g.nodes(CALLABLE_KINDS):
            d = program.symbol_def(n)
            if d is None or d.node is None or g.node(n.module).attrs.get("is_migration"):  # type: ignore[arg-type]
                continue  # each migration is frozen once written: repeating the last one's shape is the point
            seq = type_sequence(d.node.child_by_field_name("body") or d.node, program.spec_for(n).leaf_types)
            if len(seq) >= min_nodes:
                entries.append((n, seq))
        return entries

    def _exact(self, entries: list[tuple[Node, list[str]]]) -> tuple[list[Diagnostic], int]:
        """Groups with the same fingerprint, the first copy kept; and the lines the other copies take."""
        groups: dict[str, list[Node]] = defaultdict(list)
        for n, seq in entries:
            h = hashlib.sha1(" ".join(seq).encode()).hexdigest()
            n.attrs["shape_hash"] = h[:12]
            groups[h].append(n)
        diags, dup_loc = [], 0
        for members in groups.values():
            if len(members) < 2:
                continue
            members.sort(key=lambda m: (m.file or "", m.start_line or 0))
            extra = sum(m.loc for m in members[1:])
            dup_loc += extra
            diags.append(self.diag(
                "duplicate-function", Severity.WARNING,
                f"{len(members)} structurally identical functions: " + ", ".join(f"`{m.qualname}`" for m in members),
                node=members[0], suggestion=f"Keep one and delete the rest ({extra} lines), or parameterize the differences.",
                members=[m.qualname for m in members], ids=[m.id for m in members], locations=[m.location for m in members], loc=sum(m.loc for m in members),
            ))
        return diags, dup_loc

    def _near(self, program: Program, entries: list[tuple[Node, list[str]]]) -> list[Diagnostic]:
        """Pairs past the similarity threshold, of similar length, outside tests, in file order."""
        g = program.graph
        shingled = [(n, self._shingles(seq), len(seq)) for n, seq in entries if not g.node(n.module).attrs.get("is_test")]  # type: ignore[arg-type]
        pairs = []
        for i, j, sim in similar_pairs([sh for _, sh, _ in shingled], program.config.thresholds.near_duplicate_similarity):
            (a, _, la), (b, _, lb) = shingled[i], shingled[j]
            if max(la, lb) > min(la, lb) * MAX_LENGTH_RATIO or (a.file == b.file and a.start_line == b.start_line):
                continue
            a, b = sorted((a, b), key=lambda m: (m.file or "", m.start_line or 0))
            pairs.append((a, b, sim))
        pairs.sort(key=lambda t: (t[0].file or "", t[0].start_line or 0, t[1].file or "", t[1].start_line or 0))
        return [
            self.diag(
                "near-duplicate-function", Severity.INFO, f"`{a.qualname}` and `{b.qualname}` are {sim:.0%} structurally similar",
                node=a, suggestion="Consider merging them behind a parameter.",
                members=[a.qualname, b.qualname], ids=[a.id, b.id], locations=[a.location, b.location], similarity=round(sim, 3),
            )
            for a, b, sim in pairs
        ]

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
