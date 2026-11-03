"""Graphviz DOT output."""

from __future__ import annotations

from vibepiler.backend.common import EDGE_STYLES, KIND_COLORS, cycle_edges, diagnostics_by_node, short_label, worst_severity
from vibepiler.ir.model import EdgeKind, NodeKind
from vibepiler.ir.program import Program
from vibepiler.passes.diagnostic import Severity


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def emit_dot(program: Program, level: str = "module", include_packages: bool = True) -> str:
    g = program.graph
    diags = diagnostics_by_node(program)
    cyc = cycle_edges(program)
    lines = ["digraph vibepiler {", '  rankdir=LR; node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10]; edge [fontname="Helvetica", fontsize=8];']

    def node_line(n, indent="  ") -> str:  # noqa: ANN001
        color = KIND_COLORS[n.kind]
        sev = worst_severity(diags.get(n.id, []))
        pen = ""
        if sev is Severity.ERROR:
            pen = ', color="#C00000", penwidth=3'
        elif sev is Severity.WARNING:
            pen = ', color="#E07B00", penwidth=2'
        if n.attrs.get("reachable") is False and n.kind is not NodeKind.PACKAGE:
            pen += ', style="rounded,filled,dashed", fontcolor="#777777"'
        return f"{indent}{_q(n.id)} [label={_q(short_label(n))}, fillcolor={_q(color + '33')}{pen}];"

    if level == "module":
        for n in g.nodes(NodeKind.MODULE):
            lines.append(node_line(n))
        if include_packages:
            for n in g.nodes(NodeKind.PACKAGE):
                lines.append(node_line(n))
        for e in g.edges(EdgeKind.IMPORTS):
            if e.attrs.get("implicit"):
                continue
            if not include_packages and g.node(e.dst).kind is NodeKind.PACKAGE:
                continue
            color = "red" if (e.src, e.dst) in cyc else EDGE_STYLES[EdgeKind.IMPORTS][0]
            lines.append(f"  {_q(e.src)} -> {_q(e.dst)} [color={_q(color)}{', penwidth=2' if color == 'red' else ''}];")
    else:
        for i, m in enumerate(g.nodes(NodeKind.MODULE)):
            lines.append(f"  subgraph cluster_{i} {{")
            lines.append(f"    label={_q(m.name)}; style=rounded; color=\"#BBBBBB\";")
            lines.append(node_line(m, "    "))
            for c in g.nodes():
                if c.module == m.id:
                    lines.append(node_line(c, "    "))
            lines.append("  }")
        if include_packages:
            for n in g.nodes(NodeKind.PACKAGE):
                lines.append(node_line(n))
        for e in g.edges((EdgeKind.CALLS, EdgeKind.REFERENCES, EdgeKind.INHERITS, EdgeKind.IMPORTS)):
            if e.attrs.get("implicit"):
                continue
            if not include_packages and g.node(e.dst).kind is NodeKind.PACKAGE:
                continue
            if e.kind is EdgeKind.IMPORTS and g.node(e.dst).kind is not NodeKind.PACKAGE:
                continue  # module imports are implied by symbol edges at this level
            color, style = EDGE_STYLES[e.kind]
            lines.append(f"  {_q(e.src)} -> {_q(e.dst)} [color={_q(color)}, style={style}];")
    lines.append("}")
    return "\n".join(lines) + "\n"
