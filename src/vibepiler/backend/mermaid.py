"""Mermaid flowchart of the module graph (pasteable into a README or GitHub issue)."""

from __future__ import annotations

import re

from vibepiler.backend.common import cycle_edges
from vibepiler.ir.model import EdgeKind, NodeKind
from vibepiler.ir.program import Program


def _mid(s: str) -> str:
    return "n_" + re.sub(r"[^A-Za-z0-9_]", "_", s)


def emit_mermaid(program: Program, include_packages: bool = False, max_nodes: int = 150) -> str:
    g = program.graph
    cyc = cycle_edges(program)
    lines = ["flowchart LR"]
    modules = g.nodes(NodeKind.MODULE)[:max_nodes]
    ids = {m.id for m in modules}
    for m in modules:
        dead = m.attrs.get("reachable") is False
        lines.append(f'  {_mid(m.id)}["{m.name}"]' + (":::dead" if dead else ""))
    if include_packages:
        for p in g.nodes(NodeKind.PACKAGE):
            ids.add(p.id)
            lines.append(f'  {_mid(p.id)}(["{p.name}"]):::pkg')
    link_styles = []
    k = 0
    for e in g.edges(EdgeKind.IMPORTS):
        if e.attrs.get("implicit") or e.src not in ids or e.dst not in ids:
            continue
        lines.append(f"  {_mid(e.src)} --> {_mid(e.dst)}")
        if (e.src, e.dst) in cyc:
            link_styles.append(f"  linkStyle {k} stroke:#d00,stroke-width:2px")
        k += 1
    lines += link_styles
    lines.append("  classDef dead fill:#eee,stroke:#999,color:#777,stroke-dasharray: 4 2")
    lines.append("  classDef pkg fill:#f4f4f4,stroke:#bbb")
    return "\n".join(lines) + "\n"
