"""Reporting scope: when a compile names paths inside its source, the whole source is still read (so callers and entry
points outside those paths count) but only findings about the named paths are kept, and the summary counts are
recounted from them. Like compiling one unit against the whole program: you see the rest, you emit for this part."""

from __future__ import annotations

from collections import Counter

from vibepiler.frontend.loader import in_scope
from vibepiler.ir.model import NodeKind
from vibepiler.ir.program import Program
from vibepiler.passes.diagnostic import Diagnostic

DEAD_SYMBOL_CODES = ("dead-symbol", "unused-export", "unreachable-symbol")


def is_scoped(program: Program) -> bool:
    """Were paths named, and did the read reach beyond them?"""
    return bool(program.config.include) and any(not m.attrs.get("in_scope", True) for m in program.graph.nodes(NodeKind.MODULE))


def files_of(d: Diagnostic) -> set[str]:
    """Every file a finding is about: its own, a duplicate group's members, an undeclared package's importers, a cycle's modules."""
    out = {d.file} if d.file else set()
    out |= {loc.rsplit(":", 1)[0] for loc in d.data.get("locations", []) if isinstance(loc, str)}
    for key in ("files", "modules"):
        out |= {f for f in d.data.get(key, []) if isinstance(f, str)}
    return out


def keep(d: Diagnostic, include: list[str]) -> bool:
    """A finding stays when any file it is about is inside the named paths."""
    return any(in_scope(f, include) for f in files_of(d))


def recount(program: Program) -> None:
    """The per-pass summary counts, recounted from the findings that stayed (the passes counted the whole source)."""
    m, ds = program.metrics, program.diagnostics
    codes = Counter(d.code for d in ds)
    if "dead_code" in m:
        dead = [d for d in ds if d.code in DEAD_SYMBOL_CODES]
        by_file = Counter()
        for d in dead:
            by_file[d.file or ""] += d.data.get("loc", 0)
        m["dead_code"] = {**m["dead_code"], "symbols": len(dead), "loc": sum(by_file.values()), "by_file": dict(by_file.most_common()), "unreachable_modules": codes["unreachable-module"]}
    if "duplicates" in m:
        groups = [d for d in ds if d.code == "duplicate-function"]
        removable = sum(d.data.get("loc", 0) - _first_loc(program, d) for d in groups)
        m["duplicates"] = {**m["duplicates"], "exact_groups": len(groups), "near_pairs": codes["near-duplicate-function"], "removable_loc": removable}
    if "wrappers" in m:
        m["wrappers"] = {**m["wrappers"], "count": codes["pass-through-wrapper"] + codes["thin-wrapper"], "chains": codes["indirection-chain"]}
    if "abstraction" in m:
        m["abstraction"] = {**m["abstraction"], "count": sum(1 for d in ds if d.pass_name == "abstraction")}
    if "bloat" in m:
        m["bloat"] = {**m["bloat"], "unused_dependencies": codes["unused-dependency"], "undeclared_dependencies": codes["undeclared-dependency"],
                      "swallowed_exceptions": codes["swallowed-exception"], "redundant_try": codes["redundant-try"], "stubs": codes["stub-function"]}
    if "unused_imports" in m:
        m["unused_imports"] = {**m["unused_imports"], "count": codes["unused-import"]}
    if "cycles" in m:
        kept = [c for c in m["cycles"].get("cycles", []) if any(in_scope(f, program.config.include) for f in c.get("modules", []))]
        m["cycles"] = {**m["cycles"], "count": codes["import-cycle"], "cycles": kept, "largest": max((c.get("size", 0) for c in kept), default=0)}
    if "liveness" in m:
        m["liveness"] = {**m["liveness"], "dead_stores": codes["dead-store"], "unused_params": codes["unused-param"], "unused_attributes": codes["unused-attribute"],
                         "unreachable": codes["unreachable-code"], "dead_branches": codes["dead-branch"]}


def _first_loc(program: Program, d: Diagnostic) -> int:
    """The kept copy of a duplicate group: the first member, whose lines stay."""
    n = program.graph.node(d.node_id) if d.node_id and program.graph.has_node(d.node_id) else None
    return n.loc if n is not None else 0
