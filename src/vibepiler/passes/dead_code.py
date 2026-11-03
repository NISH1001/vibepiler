"""dead-code: symbols nothing references and nothing can reach."""

from __future__ import annotations

from vibepiler.ir.model import EdgeKind, NodeKind
from vibepiler.ir.program import Program
from vibepiler.passes.base import Pass
from vibepiler.passes.diagnostic import Diagnostic, Severity
from vibepiler.passes.entry_points import is_dunder

INBOUND = (EdgeKind.CALLS, EdgeKind.REFERENCES, EdgeKind.INHERITS)


class DeadCodePass(Pass):
    name = "dead-code"
    description = "Functions, classes, methods and variables that are never called, referenced or reachable from an entry point."
    analogue = "dead-code elimination"
    cost = 1
    requires = ("entry-points",)

    def run(self, program: Program) -> list[Diagnostic]:
        g = program.graph
        used_names = g.meta.get("used_member_names", {})
        diags: list[Diagnostic] = []
        dead_classes: set[str] = set()
        total_loc = 0
        by_file: dict[str, int] = {}

        def inbound(nid: str) -> list[str]:
            return [e.src for e in g.in_edges(nid, INBOUND)]

        for n in sorted(g.nodes((NodeKind.CLASS, NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.VARIABLE)), key=lambda x: (x.file or "", x.start_line or 0)):
            if n.attrs.get("is_root") or n.attrs.get("reachable"):
                continue
            if is_dunder(n.name):
                continue
            parent = g.parent(n.id)
            if parent is not None and parent.id in dead_classes:
                continue  # reported with the class
            if n.kind is NodeKind.VARIABLE:
                mod = g.node(n.module) if n.module else None
                if mod and any(tag in (mod.file or "") for tag in ("settings", "config", "constants", "__init__")):
                    continue
            if n.kind is NodeKind.METHOD and n.name in used_names:
                continue
            if parent is not None and parent.kind is NodeKind.CLASS and g.has_external_base(parent.id):
                continue  # framework may call this override
            callers = inbound(n.id)
            live_callers = [c for c in callers if g.node(c).attrs.get("reachable")]
            if live_callers:
                continue
            if n.kind is NodeKind.CLASS:
                dead_classes.add(n.id)
            exported = n.attrs.get("exported", False) and n.language != "python"
            if callers:
                code, sev = "unreachable-symbol", Severity.INFO
                why = f"only used by dead code ({', '.join(g.node(c).qualname for c in callers[:3])})"
            elif exported:
                code, sev = "unused-export", Severity.INFO
                why = "exported but never imported anywhere in the project"
            else:
                code, sev = "dead-symbol", Severity.WARNING
                why = "never called or referenced"
            kind = n.kind.value
            diags.append(
                self.diag(
                    code,
                    sev,
                    f"{kind} `{n.qualname}` is dead: {why}",
                    node=n,
                    suggestion=f"Delete it ({n.loc} lines)." if n.loc else "Delete it.",
                    loc=n.loc,
                    callers=[g.node(c).qualname for c in callers],
                )
            )
            total_loc += n.loc
            by_file[n.file or ""] = by_file.get(n.file or "", 0) + n.loc

        for m in g.nodes(NodeKind.MODULE):
            if m.attrs.get("reachable") or m.attrs.get("is_root"):
                continue
            diags.append(
                self.diag(
                    "unreachable-module",
                    Severity.INFO,
                    f"module `{m.name}` is never imported or executed from any entry point",
                    node=m,
                    suggestion="Delete the file, or wire it in if it is meant to be an entry point (see `entry_patterns`).",
                    loc=m.attrs.get("loc", 0),
                )
            )

        program.metrics["dead_code"] = {
            "symbols": sum(1 for d in diags if d.code in ("dead-symbol", "unused-export", "unreachable-symbol")),
            "loc": total_loc,
            "by_file": dict(sorted(by_file.items(), key=lambda kv: -kv[1])),
            "unreachable_modules": sum(1 for d in diags if d.code == "unreachable-module"),
        }
        return diags

