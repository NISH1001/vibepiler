"""hotspots: fan-in/fan-out, god modules and central functions."""

from __future__ import annotations

from vibepiler.ir.model import CALLABLE_KINDS, EdgeKind, NodeKind
from vibepiler.ir.program import Program
from vibepiler.passes.base import Pass
from vibepiler.passes.diagnostic import Diagnostic, Severity
from vibepiler.passes.graphalgo import pagerank


class HotspotsPass(Pass):
    name = "hotspots"
    description = "Modules and functions everything depends on: fan-in/fan-out, god modules, PageRank-central functions."
    analogue = "critical-path / hot-path analysis"
    cost = 1

    def run(self, program: Program) -> list[Diagnostic]:
        g = program.graph
        th = program.config.thresholds
        diags: list[Diagnostic] = []

        mod_rows = []
        for m in g.nodes(NodeKind.MODULE):
            importers = {e.src for e in g.in_edges(m.id, EdgeKind.IMPORTS) if not e.attrs.get("implicit")}
            imports = {e.dst for e in g.out_edges(m.id, EdgeKind.IMPORTS) if not e.attrs.get("implicit") and g.node(e.dst).kind is NodeKind.MODULE}
            external = {e.dst for e in g.out_edges(m.id, EdgeKind.IMPORTS) if g.node(e.dst).kind is NodeKind.PACKAGE}
            m.attrs["fan_in"] = len(importers)
            m.attrs["fan_out"] = len(imports)
            m.attrs["external_deps"] = len(external)
            mod_rows.append({"id": m.id, "file": m.file, "name": m.name, "fan_in": len(importers), "fan_out": len(imports), "external_deps": len(external), "loc": m.attrs.get("loc", 0)})
            if len(importers) > th.god_module_fan and len(imports) > th.god_module_fan:
                diags.append(
                    self.diag(
                        "god-module",
                        Severity.WARNING,
                        f"`{m.name}` is a god module: imported by {len(importers)} modules and imports {len(imports)}",
                        node=m,
                        suggestion="Split it: separate the widely-imported API from the glue that pulls in everything else.",
                        fan_in=len(importers),
                        fan_out=len(imports),
                    )
                )
        mod_rows.sort(key=lambda r: -(r["fan_in"] + r["fan_out"]))

        cg = g.call_graph()
        pr = pagerank(cg, weight="weight") if cg.number_of_edges() else {}
        fn_rows = []
        for n in g.nodes(CALLABLE_KINDS | {NodeKind.CLASS}):
            callers = cg.in_degree(n.id) if n.id in cg else 0
            callees = cg.out_degree(n.id) if n.id in cg else 0
            n.attrs["callers"] = callers
            n.attrs["callees"] = callees
            n.attrs["pagerank"] = round(pr.get(n.id, 0.0), 5)
            fn_rows.append({"id": n.id, "qualname": n.qualname, "file": n.file, "line": n.start_line, "callers": callers, "callees": callees, "pagerank": n.attrs["pagerank"]})
            if callers > th.god_module_fan and callees > th.god_module_fan:
                diags.append(
                    self.diag(
                        "hub-function",
                        Severity.INFO,
                        f"`{n.qualname}` is a hub: {callers} callers and {callees} callees",
                        node=n,
                        suggestion="A function that both everyone calls and that calls everyone is hard to change safely; consider splitting responsibilities.",
                        callers=callers,
                        callees=callees,
                    )
                )
        fn_rows.sort(key=lambda r: (-r["pagerank"], -r["callers"]))
        program.metrics["hotspots"] = {"modules": mod_rows[: th.hotspot_top_n], "functions": fn_rows[: th.hotspot_top_n]}
        return diags
