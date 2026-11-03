"""JSON IR: the whole graph, every diagnostic and every metric."""

from __future__ import annotations

from typing import Any

from vibepiler import __version__
from vibepiler.ir.program import Program


def _clean(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def emit_json(program: Program) -> dict[str, Any]:
    data = program.graph.to_dict()
    return {
        "vibepiler": __version__,
        "meta": _clean(data["meta"]),
        "nodes": _clean(data["nodes"]),
        "edges": _clean(data["edges"]),
        "diagnostics": [_clean(d.to_dict()) for d in program.diagnostics],
        "metrics": _clean(program.metrics),
    }
