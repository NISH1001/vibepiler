"""cycles: strongly connected components in the module import graph."""

from __future__ import annotations

import networkx as nx

from vibepiler.ir.model import EdgeKind, NodeKind
from vibepiler.ir.program import Program
from vibepiler.passes.base import Pass
from vibepiler.passes.diagnostic import Diagnostic, Severity


class CyclesPass(Pass):
    name = "cycles"
    description = "Circular imports between modules (strongly connected components of the import graph)."
    analogue = "dependency-graph analysis"
    cost = 1

    def run(self, program: Program) -> list[Diagnostic]:
        g = program.graph
        view = nx.DiGraph()
        for n in g.nodes(NodeKind.MODULE):
            view.add_node(n.id)
        for e in g.edges(EdgeKind.IMPORTS):
            if e.attrs.get("implicit") or e.attrs.get("deferred") or e.src not in view or e.dst not in view:
                continue
            view.add_edge(e.src, e.dst, names=e.attrs.get("names", []), line=e.line, count=e.count)
        diags: list[Diagnostic] = []
        cycles = []
        for comp in nx.strongly_connected_components(view):
            if len(comp) < 2:
                continue
            members = sorted(comp, key=lambda m: g.node(m).file or "")
            files = [g.node(m).file for m in members]
            internal = [(u, v, d) for u, v, d in view.edges(data=True) if u in comp and v in comp]
            weakest = min(internal, key=lambda t: (len(t[2]["names"]), t[2]["count"]))
            u, v, d = weakest
            cycles.append({"modules": files, "size": len(comp)})
            diags.append(
                self.diag(
                    "import-cycle",
                    Severity.WARNING if len(comp) < 6 else Severity.ERROR,
                    f"import cycle between {len(comp)} modules: " + " -> ".join(g.node(m).name for m in members),
                    file=g.node(u).file,
                    line=d["line"],
                    suggestion=(
                        f"Weakest link: `{g.node(u).name}` imports {', '.join(d['names']) or 'module'} from `{g.node(v).name}`. "
                        "Move that import inside the function that needs it (or under `if TYPE_CHECKING:` if only annotations use it), or extract the shared piece into a new module both can import."
                    ),
                    modules=files,
                    edges=[{"from": g.node(a).file, "to": g.node(b).file, "names": dd["names"]} for a, b, dd in internal],
                    qualname=" <-> ".join(g.node(m).name for m in members),
                )
            )
        program.metrics["cycles"] = {"count": len(cycles), "largest": max((c["size"] for c in cycles), default=0), "cycles": cycles}
        return diags
