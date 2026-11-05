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

from vibepiler.backend.common import project_name, summarize
from vibepiler.backend.json_out import emit_json
from vibepiler.ir.program import Program
from vibepiler.passes.registry import all_passes

CYTOSCAPE = "https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.30.4/cytoscape.min.js"


MAX_SOURCE_LINES = 40  # per function on the meaning map
MAX_SOURCE_CHARS = 4_000_000  # over the whole report, so a huge repo still yields a page a browser opens


def emit_html(program: Program) -> str:
    s = summarize(program)
    payload = emit_json(program)
    payload["sources"] = _map_sources(program)
    data = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    passes = json.dumps([{"name": p.name, "analogue": p.analogue, "description": p.description, "cost": p.cost} for p in all_passes()])
    res = s["resolution"]
    total = res.get("calls_total", 0)
    linked = res.get("calls_resolved", 0) + res.get("calls_external", 0) + res.get("calls_builtin", 0)
    pct = f"{linked / total:.0%}" if total else "n/a"
    return (
        resources.files("vibepiler.backend").joinpath("viewer.html").read_text(encoding="utf-8")
        .replace("__CYTOSCAPE__", CYTOSCAPE)
        .replace("__NAME__", escape(project_name(program)))
        .replace("__FILES__", str(s["files"]))
        .replace("__SYMBOLS__", str(s["symbols"]))
        .replace("__LINKED__", pct)
        .replace("__EDGES__", str(s["edges"]))
        .replace("__FINDINGS__", str(len(program.diagnostics)))
        .replace("__PASSES__", passes)
        .replace("__DATA__", data)
    )


def _map_sources(program: Program) -> dict[str, str]:
    """The first lines of every function on the meaning map, so clicking a dot shows its code without the repo."""
    points = (program.metrics.get("concepts") or {}).get("points") or []
    out: dict[str, str] = {}
    files: dict[str, list[str]] = {}
    total = 0
    for p in points:
        if not program.graph.has_node(p["id"]):
            continue
        n = program.graph.node(p["id"])
        if not n.file or not n.start_line:
            continue
        if n.file not in files:
            try:
                files[n.file] = (program.root / n.file).read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                files[n.file] = []
        lines = files[n.file][n.start_line - 1 : n.start_line - 1 + min(MAX_SOURCE_LINES, max(1, n.loc))]
        text = "\n".join(lines)
        if total + len(text) > MAX_SOURCE_CHARS:
            break
        out[p["id"]] = text
        total += len(text)
    return out
