"""Self-contained interactive HTML viewer.

The page (viewer.html next to this file) is organized around the compiler
phases: Parse -> Link -> Program graph -> Passes. The graph uses a
deterministic layered layout computed from the entry points, so toggling
filters never moves a node; cytoscape.js is loaded from cdnjs and the whole
IR is embedded as JSON.
"""

from __future__ import annotations

import json
from html import escape
from importlib import resources

from vibepiler.backend.common import summarize
from vibepiler.backend.json_out import emit_json
from vibepiler.ir.program import Program
from vibepiler.passes.registry import all_passes

CYTOSCAPE = "https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.30.4/cytoscape.min.js"


def emit_html(program: Program) -> str:
    s = summarize(program)
    data = json.dumps(emit_json(program), separators=(",", ":")).replace("</", "<\\/")
    passes = json.dumps([{"name": p.name, "analogue": p.analogue, "description": p.description, "cost": p.cost} for p in all_passes()])
    res = s["resolution"]
    total = res.get("calls_total", 0)
    linked = res.get("calls_resolved", 0) + res.get("calls_external", 0) + res.get("calls_builtin", 0)
    pct = f"{linked / total:.0%}" if total else "n/a"
    return (
        resources.files("vibepiler.backend").joinpath("viewer.html").read_text(encoding="utf-8")
        .replace("__CYTOSCAPE__", CYTOSCAPE)
        .replace("__NAME__", escape(program.root.name))
        .replace("__FILES__", str(s["files"]))
        .replace("__SYMBOLS__", str(s["symbols"]))
        .replace("__LINKED__", pct)
        .replace("__EDGES__", str(s["edges"]))
        .replace("__FINDINGS__", str(len(program.diagnostics)))
        .replace("__PASSES__", passes)
        .replace("__DATA__", data)
    )
