"""Shared presentation helpers."""

from __future__ import annotations

from collections import defaultdict

from vibepiler.ir.model import EdgeKind, Node, NodeKind
from vibepiler.ir.program import Program
from vibepiler.passes.diagnostic import Diagnostic, Severity

KIND_COLORS = {
    NodeKind.MODULE: "#4C78A8",
    NodeKind.PACKAGE: "#9D9D9D",
    NodeKind.CLASS: "#F58518",
    NodeKind.FUNCTION: "#54A24B",
    NodeKind.METHOD: "#72B7B2",
    NodeKind.VARIABLE: "#B279A2",
}
EDGE_STYLES = {
    EdgeKind.IMPORTS: ("#4C78A8", "solid"),
    EdgeKind.CALLS: ("#54A24B", "solid"),
    EdgeKind.REFERENCES: ("#B279A2", "dashed"),
    EdgeKind.INHERITS: ("#F58518", "bold"),
    EdgeKind.CONTAINS: ("#CCCCCC", "dotted"),
}
SEVERITY_ORDER = [Severity.ERROR, Severity.WARNING, Severity.INFO]


def diagnostics_by_node(program: Program) -> dict[str, list[Diagnostic]]:
    out: dict[str, list[Diagnostic]] = defaultdict(list)
    for d in program.diagnostics:
        if d.node_id:
            out[d.node_id].append(d)
    return out


def worst_severity(diags: list[Diagnostic]) -> Severity | None:
    return max((d.severity for d in diags), default=None)


def cycle_edges(program: Program) -> set[tuple[str, str]]:
    g = program.graph
    pairs: set[tuple[str, str]] = set()
    for cyc in program.metrics.get("cycles", {}).get("cycles", []):
        members = {f"mod:{f}" for f in cyc["modules"]}
        for e in g.edges(EdgeKind.IMPORTS):
            if e.src in members and e.dst in members:
                pairs.add((e.src, e.dst))
    return pairs


def short_label(n: Node) -> str:
    if n.kind is NodeKind.MODULE:
        return n.name
    if n.kind is NodeKind.PACKAGE:
        return f"[{n.name}]"
    return n.qualname


def size_parts(b: dict) -> dict[str, str]:
    """The pieces of the `size` line, plain text; the terminal and markdown backends only add their markup."""
    conf = ", ".join(f"{k} {v:.0f}" for k, v in b.get("waste_by_confidence", {}).items() if k != "model")
    conf += f"; +{b['model_loc']:.0f} if the semantic pairs merge" if b.get("model_loc") else ""
    conf += f"; {b['unverified_loc']:.0f} alive only by name" if b.get("unverified_loc") else ""
    c = b.get("conditions", {})
    stamp = ", ".join(x for x in [f"-O{c.get('opt_level')}" if c.get("opt_level") is not None else "", f"{c['linked']:.0%} linked" if c.get("linked") is not None else "", c.get("model") or ""] if x)
    return {
        "waste": f"{b['waste']:.1%}",
        "lines": f"{b['removable_loc']:.0f} of {b['loc']} lines" + (f"; {conf}" if conf else ""),
        "tangle": f"{b['tangle']:.1%}",
        "reasons": ", ".join(f"{r['code']} ×{r['count']}" for r in b.get("tangle_reasons", [])[:3]),
        "bloat": f"{b['score']:.1%}",
        "stamp": stamp,
        "tests": f"tests ({b['tests']['loc']} lines) scored apart: waste {b['tests']['waste']:.0%}" if b.get("tests", {}).get("loc") else "",
    }


def liveness_parts(lv: dict | None) -> dict[str, str]:
    """The live fragment of the size line, plain text: {"live": "94.1%", "defs": "448 of 476 definitions; dead: ..."}."""
    from vibepiler.passes.liveness import Liveness

    m = Liveness.of(lv) if lv else None
    if m is None:
        return {}
    return {"live": f"{m.value:.1%}", "defs": str(m).split("(", 1)[1].rstrip(")")}


def summarize(program: Program) -> dict:
    g = program.graph
    counts = {s.label: 0 for s in SEVERITY_ORDER}
    for d in program.diagnostics:
        counts[d.severity.label] += 1
    by_pass: dict[str, int] = defaultdict(int)
    for d in program.diagnostics:
        by_pass[d.pass_name] += 1
    return {
        "files": g.meta.get("files", 0),
        "modules": len(g.nodes(NodeKind.MODULE)),
        "symbols": g.meta.get("symbols", 0),
        "packages": len(g.nodes(NodeKind.PACKAGE)),
        "edges": len(g.edges()),
        "loc": sum(m.attrs.get("loc", 0) for m in g.nodes(NodeKind.MODULE)),
        "languages": g.meta.get("languages", {}),
        "resolution": g.meta.get("resolution", {}),
        "severity": counts,
        "by_pass": dict(by_pass),
    }


def project_name(program) -> str:  # noqa: ANN001 - a Program
    """The name a report shows: a cloned repo is named after the repo (`repo@ref`), not its cache folder
    (`host__owner__repo@ref`); anything else after its folder."""
    root = program.root
    if root.parent.name == "repos" and "__" in root.name:
        return root.name.rsplit("__", 1)[-1]
    return root.name
