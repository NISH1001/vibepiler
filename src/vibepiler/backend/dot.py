"""Graphviz DOT output."""

from __future__ import annotations

from vibepiler.backend.common import EDGE_STYLES, KIND_COLORS, cycle_edges, diagnostics_by_node, short_label, worst_severity
from vibepiler.ir.graph import ProgramGraph
from vibepiler.ir.model import Edge, EdgeKind, Node, NodeKind
from vibepiler.ir.program import Program
from vibepiler.passes.diagnostic import Severity


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def emit_dot(program: Program, level: str = "module", include_packages: bool = True) -> str:
    g = program.graph
    diags = diagnostics_by_node(program)
    lines = ["digraph vibepiler {", '  rankdir=LR; node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10]; edge [fontname="Helvetica", fontsize=8];']
    if level == "module":
        lines += [_node_line(n, diags) for n in g.nodes(NodeKind.MODULE)]
    else:
        lines += _module_clusters(g, diags)
    if include_packages:
        lines += [_node_line(n, diags) for n in g.nodes(NodeKind.PACKAGE)]
    lines += _module_edges(program, include_packages) if level == "module" else _symbol_edges(g, include_packages)
    lines.append("}")
    return "\n".join(lines) + "\n"


def _node_line(n: Node, diags: dict, indent: str = "  ") -> str:
    """A node filled by its kind, outlined by its worst finding, dashed when nothing reaches it."""
    pen = {Severity.ERROR: ', color="#C00000", penwidth=3', Severity.WARNING: ', color="#E07B00", penwidth=2'}.get(worst_severity(diags.get(n.id, [])), "")
    if n.attrs.get("reachable") is False and n.kind is not NodeKind.PACKAGE:
        pen += ', style="rounded,filled,dashed", fontcolor="#777777"'
    return f"{indent}{_q(n.id)} [label={_q(short_label(n))}, fillcolor={_q(KIND_COLORS[n.kind] + '33')}{pen}];"


def _module_clusters(g: ProgramGraph, diags: dict) -> list[str]:
    """One cluster per module holding its symbols."""
    inside: dict[str | None, list[Node]] = {}
    for c in g.nodes():
        inside.setdefault(c.module, []).append(c)
    lines = []
    for i, m in enumerate(g.nodes(NodeKind.MODULE)):
        lines += [f"  subgraph cluster_{i} {{", f"    label={_q(m.name)}; style=rounded; color=\"#BBBBBB\";", _node_line(m, diags, "    ")]
        lines += [_node_line(c, diags, "    ") for c in inside.get(m.id, [])]
        lines.append("  }")
    return lines


def _shown(g: ProgramGraph, e: Edge, include_packages: bool) -> bool:
    return not e.attrs.get("implicit") and (include_packages or g.node(e.dst).kind is not NodeKind.PACKAGE)


def _module_edges(program: Program, include_packages: bool) -> list[str]:
    g, cyc = program.graph, cycle_edges(program)
    lines = []
    for e in g.edges(EdgeKind.IMPORTS):
        if _shown(g, e, include_packages):
            color = "red" if (e.src, e.dst) in cyc else EDGE_STYLES[EdgeKind.IMPORTS][0]
            lines.append(f"  {_q(e.src)} -> {_q(e.dst)} [color={_q(color)}{', penwidth=2' if color == 'red' else ''}];")
    return lines


def _symbol_edges(g: ProgramGraph, include_packages: bool) -> list[str]:
    lines = []
    for e in g.edges((EdgeKind.CALLS, EdgeKind.REFERENCES, EdgeKind.INHERITS, EdgeKind.IMPORTS)):
        if not _shown(g, e, include_packages) or (e.kind is EdgeKind.IMPORTS and g.node(e.dst).kind is not NodeKind.PACKAGE):
            continue  # module imports are implied by symbol edges at this level
        color, style = EDGE_STYLES[e.kind]
        lines.append(f"  {_q(e.src)} -> {_q(e.dst)} [color={_q(color)}, style={style}];")
    return lines
