"""entry-points: find program roots and compute reachability from them."""

from __future__ import annotations

import fnmatch

from vibepiler.ir.model import EdgeKind, NodeKind
from vibepiler.ir.program import Program
from vibepiler.passes.base import Pass
from vibepiler.passes.diagnostic import Diagnostic

FLOW_EDGES = (EdgeKind.CALLS, EdgeKind.REFERENCES, EdgeKind.IMPORTS, EdgeKind.INHERITS)


def is_dunder(name: str) -> bool:
    """Python dunders and the JS constructor: lifecycle methods the runtime calls, never 'dead'."""
    return (name.startswith("__") and name.endswith("__")) or name == "constructor"


class EntryPointsPass(Pass):
    name = "entry-points"
    description = "Detect program roots (main guards, CLI commands, routes, tests, framework hooks, exports) and mark everything reachable from them."
    analogue = "program entry / reachability analysis"
    cost = 1

    def run(self, program: Program) -> list[Diagnostic]:
        g = program.graph
        cfg = program.config
        roots: set[str] = set()
        for n in g.nodes():
            kinds = self._root_kinds(g, cfg, n)
            n.attrs["entry"] = kinds
            n.attrs["is_root"] = bool(kinds)
            if kinds:
                roots.add(n.id)

        used_names = g.meta.get("used_member_names", {})
        reachable = self._reach(g, roots, used_names)
        for n in g.nodes():
            n.attrs["reachable"] = n.id in reachable

        symbols = [n for n in g.nodes() if n.kind not in (NodeKind.MODULE, NodeKind.PACKAGE)]
        # alive only by name: no resolved edge reaches them; an unresolved call somewhere just used the same name.
        # Dead-code honours that (conservative), the bloat score reports it as the unverified band.
        kept = self._alive_by_name(g, symbols, reachable, used_names)
        program.metrics["entry_points"] = {
            "count": len(roots),
            "kept_by_name": {"symbols": len(kept), "loc": sum(n.loc for n in kept)},
            "roots": [
                {"id": r, "kinds": g.node(r).attrs["entry"], "file": g.node(r).file, "line": g.node(r).start_line, "qualname": g.node(r).qualname}
                for r in sorted(roots)
            ],
            "reachable_symbols": sum(1 for n in symbols if n.id in reachable),
            "total_symbols": len(symbols),
            "unreachable_modules": sorted(n.file for n in g.nodes(NodeKind.MODULE) if n.id not in reachable and n.file),
        }
        return []

    @staticmethod
    def _root_kinds(g, cfg, n) -> list[str]:  # noqa: ANN001
        """Why a node is an entry point: main guard, test module, entry module, export, or configured pattern."""
        kinds = list(n.attrs.get("entry", []))
        if n.kind is NodeKind.MODULE:
            if n.attrs.get("has_main_guard"):
                kinds.append("main_guard")
            if n.attrs.get("is_test"):
                kinds.append("test_module")
            if n.attrs.get("is_entry_module"):
                kinds.append("entry_module")
        if n.kind is not NodeKind.MODULE and n.kind is not NodeKind.PACKAGE:
            mod = g.node(n.module) if n.module else None
            exports_are_roots = cfg.treat_exports_as_roots
            if exports_are_roots is None:
                exports_are_roots = bool(mod and mod.attrs.get("is_entry_module") and mod.language != "python")
            if exports_are_roots and n.attrs.get("exported") and (g.parent(n.id) or mod) is mod:
                kinds.append("export")
        for pat in cfg.entry_patterns:
            if fnmatch.fnmatch(n.qualname, pat) or (n.file and fnmatch.fnmatch(n.file, pat)):
                kinds.append("configured")
        kinds = list(dict.fromkeys(kinds))
        return kinds

    @staticmethod
    def _reach(g, roots: set[str], used_names) -> set[str]:  # noqa: ANN001
        """Everything reachable from the roots over flow edges; a live class also keeps its by-name-called methods."""
        reachable: set[str] = set()
        frontier = list(roots)
        while frontier:
            nid = frontier.pop()
            if nid in reachable:
                continue
            reachable.add(nid)
            for e in g.out_edges(nid, FLOW_EDGES):
                if e.dst not in reachable:
                    frontier.append(e.dst)
            node = g.node(nid)
            if node.kind is NodeKind.CLASS:
                # a live class keeps alive the methods that are called by name anywhere
                for child in g.children(nid):
                    if child.id in reachable:
                        continue
                    if is_dunder(child.name) or child.name in used_names or child.attrs.get("is_root"):
                        frontier.append(child.id)
                # ... and its base classes' members are looked up through it
            if node.kind is NodeKind.MODULE and node.attrs.get("is_test"):
                for child in g.children(nid):
                    frontier.append(child.id)
        return reachable

    @staticmethod
    def _alive_by_name(g, symbols, reachable: set[str], used_names) -> list:  # noqa: ANN001
        """Alive only by name: no resolved edge reaches them; an unresolved call somewhere just used the same name.
        Dead-code honours that (conservative); the bloat score reports it as the unverified band."""
        kept = [
            n
            for n in symbols
            if n.id in reachable and not n.attrs.get("is_root") and not is_dunder(n.name) and n.name in used_names
            and not any(e.src in reachable for e in g.in_edges(n.id, FLOW_EDGES))
            and not (n.module and g.node(n.module).attrs.get("is_test"))
        ]
        for n in kept:
            n.attrs["alive_by_name"] = True
        return kept
