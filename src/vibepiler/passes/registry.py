"""Pass registry and runner (the optimizer's pass manager)."""

from __future__ import annotations

import fnmatch
import re
import time

from vibepiler.ir.program import Program
from vibepiler.passes.abstraction import AbstractionPass
from vibepiler.passes.base import Pass
from vibepiler.passes.bloat import BloatPass
from vibepiler.passes.bloat_score import Bloat, BloatScorePass
from vibepiler.passes.complexity import ComplexityPass
from vibepiler.passes.concepts import ConceptsPass
from vibepiler.passes.cycles import CyclesPass
from vibepiler.passes.dead_code import DeadCodePass
from vibepiler.passes.diagnostic import Diagnostic
from vibepiler.passes.duplicates import DuplicatesPass
from vibepiler.passes.entry_points import EntryPointsPass
from vibepiler.passes.hotspots import HotspotsPass
from vibepiler.passes.liveness import Liveness, LivenessPass
from vibepiler.passes.measures import METRICS, MeasuresPass
from vibepiler.passes.metric import Metric
from vibepiler.passes.responsibilities import ResponsibilitiesPass
from vibepiler.passes.scope import is_scoped, keep, recount
from vibepiler.passes.semantic_duplicates import SemanticDuplicatesPass
from vibepiler.passes.unused_imports import UnusedImportsPass
from vibepiler.passes.wrappers import WrappersPass

_PASSES: list[Pass] = [
    EntryPointsPass(),
    DeadCodePass(),
    LivenessPass(),
    UnusedImportsPass(),
    CyclesPass(),
    ComplexityPass(),
    MeasuresPass(),
    WrappersPass(),
    HotspotsPass(),
    AbstractionPass(),
    BloatPass(),
    DuplicatesPass(),
    SemanticDuplicatesPass(),
    ResponsibilitiesPass(),
    ConceptsPass(),
    BloatScorePass(),  # last: it scores whatever the passes above found
]


def all_passes() -> list[Pass]:
    return list(_PASSES)


def all_metrics() -> list[type[Metric]]:
    """Every score, in the order the size line prints them."""
    return [Bloat, Liveness, *METRICS]


def parse_gate(expr: str) -> tuple[type[Metric], str, float] | None:
    """`bloat>0.25` or `live<0.9` -> (metric, op, limit); None for anything else (a severity name)."""
    m = re.fullmatch(r"([a-z]+)([<>])(\d*\.?\d+)", expr.strip())
    if m is None:
        return None
    cls = next((c for c in all_metrics() if c.label == m.group(1)), None)
    return (cls, m.group(2), float(m.group(3))) if cls else None


def get_pass(name: str) -> Pass:
    for p in _PASSES:
        if p.name == name:
            return p
    raise KeyError(f"unknown pass {name!r}; known: {[p.name for p in _PASSES]}")


def run_pass(name: str, program: Program) -> list[Diagnostic]:
    """Run one pass (and its prerequisites) without touching program.diagnostics."""
    p = get_pass(name)
    for req in p.requires:
        if req not in program.metrics.get("_ran", set()):
            run_pass(req, program)
    diags = p.run(program)
    program.metrics.setdefault("_ran", set()).add(name)
    return diags


def is_accepted(d: Diagnostic, patterns: list[str]) -> bool:
    """`accept = ["class-as-function:*Embedder", "single-use-helper"]`: a code, or a code and a qualname glob. A finding
    about several functions (a duplicate pair or group) matches when the glob names any of them."""
    names = [d.data.get("qualname", "") or "", *(m for m in d.data.get("members", []) if isinstance(m, str))]
    for pat in patterns:
        code, _, glob = pat.partition(":")
        if d.code == code and (not glob or any(fnmatch.fnmatch(n, glob) for n in names)):
            return True
    return False


def _selected(p: Pass, opt_level: int, only: list[str] | None, skip: list[str] | None) -> bool:
    """`only` names passes (entry-points always runs, the others need it); otherwise the -O level picks by cost."""
    if skip and p.name in skip:
        return False
    if only:
        return p.name in only or p.name == "entry-points"
    return p.cost <= opt_level


def _drop_accepted(program: Program, diags: list[Diagnostic], accepted: list[dict]) -> list[Diagnostic]:
    """The findings config `accept` does not cover; the accepted ones are recorded in `accepted` and marked on their node."""
    kept = []
    for d in diags:
        if not is_accepted(d, program.config.accept):
            kept.append(d)
            continue
        if d.node_id and program.graph.has_node(d.node_id):
            program.graph.node(d.node_id).attrs["accepted"] = True
        accepted.append({"code": d.code, "qualname": d.data.get("qualname", ""), "file": d.file})
    return kept


def run_passes(
    program: Program,
    opt_level: int = 2,
    only: list[str] | None = None,
    skip: list[str] | None = None,
    on_pass=None,  # noqa: ANN001  callback(pass, diags, seconds)
) -> list[Diagnostic]:
    """Run the pass pipeline. -O0 builds the graph only; -O1 cheap passes; -O2 adds duplicates; -O3 adds the embedding tier."""
    program.diagnostics = []
    program.metrics["_ran"] = set()
    program.metrics["opt_level"] = opt_level
    if opt_level <= 0 and not only:
        return []
    selected = [p for p in _PASSES if _selected(p, opt_level, only, skip)]
    timings: dict[str, float] = {}
    accepted: list[dict] = []
    scoped = is_scoped(program)  # paths were named: the whole source was read, only they are reported
    for p in selected:
        t0 = time.perf_counter()
        diags = run_pass(p.name, program)
        timings[p.name] = round(time.perf_counter() - t0, 4)
        kept = _drop_accepted(program, diags, accepted)
        diags = [d for d in kept if keep(d, program.config.include)] if scoped else kept
        program.diagnostics.extend(diags)
        if on_pass is not None:
            on_pass(p, diags, timings[p.name])
    if scoped:
        recount(program)
    program.metrics["timings"] = timings
    program.metrics["accepted"] = accepted
    program.metrics.pop("_ran", None)
    program.diagnostics.sort(key=lambda d: (-int(d.severity), d.file or "", d.line or 0))
    return program.diagnostics
