"""Pass registry and runner (the optimizer's pass manager)."""

from __future__ import annotations

import time

from vibepiler.ir.program import Program
from vibepiler.passes.abstraction import AbstractionPass
from vibepiler.passes.base import Pass
from vibepiler.passes.bloat import BloatPass
from vibepiler.passes.complexity import ComplexityPass
from vibepiler.passes.cycles import CyclesPass
from vibepiler.passes.dead_code import DeadCodePass
from vibepiler.passes.diagnostic import Diagnostic
from vibepiler.passes.duplicates import DuplicatesPass
from vibepiler.passes.entry_points import EntryPointsPass
from vibepiler.passes.hotspots import HotspotsPass
from vibepiler.passes.responsibilities import ResponsibilitiesPass
from vibepiler.passes.semantic_duplicates import SemanticDuplicatesPass
from vibepiler.passes.unused_imports import UnusedImportsPass
from vibepiler.passes.wrappers import WrappersPass

_PASSES: list[Pass] = [
    EntryPointsPass(),
    DeadCodePass(),
    UnusedImportsPass(),
    CyclesPass(),
    ComplexityPass(),
    WrappersPass(),
    HotspotsPass(),
    AbstractionPass(),
    BloatPass(),
    DuplicatesPass(),
    SemanticDuplicatesPass(),
    ResponsibilitiesPass(),
]


def all_passes() -> list[Pass]:
    return list(_PASSES)


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
    if opt_level <= 0 and not only:
        return []
    selected: list[Pass] = []
    for p in _PASSES:
        if only and p.name not in only and p.name != "entry-points":
            continue
        if skip and p.name in skip:
            continue
        if not only and p.cost > opt_level:
            continue
        selected.append(p)
    timings: dict[str, float] = {}
    for p in selected:
        t0 = time.perf_counter()
        diags = run_pass(p.name, program)
        timings[p.name] = round(time.perf_counter() - t0, 4)
        program.diagnostics.extend(diags)
        if on_pass is not None:
            on_pass(p, diags, timings[p.name])
    program.metrics["timings"] = timings
    program.metrics.pop("_ran", None)
    program.diagnostics.sort(key=lambda d: (-int(d.severity), d.file or "", d.line or 0))
    return program.diagnostics
