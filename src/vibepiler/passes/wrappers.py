"""wrappers: functions whose whole body is one call to another function."""

from __future__ import annotations

from vibepiler.ir.model import CALLABLE_KINDS, EdgeKind, Node, NodeKind
from vibepiler.ir.program import Program
from vibepiler.passes.base import Pass
from vibepiler.passes.diagnostic import Diagnostic, Severity
from vibepiler.passes.entry_points import is_dunder


class WrappersPass(Pass):
    name = "wrappers"
    description = "Pass-through functions that only forward to another function, and chains of them."
    analogue = "function inlining"
    cost = 1

    def run(self, program: Program) -> list[Diagnostic]:
        g = program.graph
        diags: list[Diagnostic] = []
        wrapper_of: dict[str, str] = {}
        for n in g.nodes(CALLABLE_KINDS):
            target = self._wrapper_target(program, n)
            if target is None:
                continue
            tnode, passthrough = target
            wrapper_of[n.id] = tnode.id
            callers = {e.src for e in g.in_edges(n.id, EdgeKind.CALLS)}
            if not callers and not n.attrs.get("reachable"):
                continue  # dead anyway; dead-code reports it
            sev = Severity.WARNING if passthrough else Severity.INFO
            code = "pass-through-wrapper" if passthrough else "thin-wrapper"
            diags.append(
                self.diag(
                    code,
                    sev,
                    f"`{n.qualname}` only {'forwards its arguments to' if passthrough else 'calls'} `{tnode.qualname}`",
                    node=n,
                    suggestion=f"Inline it: call `{tnode.qualname}` directly from its {len(callers)} caller(s) and delete `{n.name}`.",
                    target=tnode.qualname,
                    target_id=tnode.id,
                    callers=len(callers),
                )
            )
        # chains of wrappers: a -> b -> c where a and b are both wrappers
        chains = 0
        for start in wrapper_of:
            if any(wrapper_of.get(src) == start for src in wrapper_of):
                continue  # not the head of a chain
            chain = [start]
            cur = start
            while cur in wrapper_of and wrapper_of[cur] not in chain:
                cur = wrapper_of[cur]
                chain.append(cur)
            if len(chain) >= 3:
                chains += 1
                diags.append(
                    self.diag(
                        "indirection-chain",
                        Severity.WARNING,
                        "indirection chain: " + " -> ".join(f"`{g.node(c).qualname}`" for c in chain),
                        node=g.node(start),
                        suggestion=f"Collapse the chain: callers of `{g.node(start).qualname}` can call `{g.node(chain[-1]).qualname}` directly.",
                        chain=[g.node(c).qualname for c in chain],
                    )
                )
        program.metrics["wrappers"] = {"count": len(wrapper_of), "chains": chains}
        return diags

    def _wrapper_target(self, program: Program, n: Node) -> tuple[Node, bool] | None:
        g = program.graph
        if n.attrs.get("is_root") or n.attrs.get("decorators") or n.attrs.get("recursive"):
            return None
        if is_dunder(n.name):
            return None
        parent = g.parent(n.id)
        if parent is not None and parent.kind is NodeKind.CLASS and self._overrides(program, parent, n.name):
            return None
        d = program.symbol_def(n)
        if d is None or d.node is None:
            return None
        spec = program.spec_for(n)
        sf = program.facts[n.module].file  # type: ignore[index]
        stmts = spec.body_statements(d.node, sf)
        if len(stmts) != 1:
            return None
        call = spec.statement_call(stmts[0], sf)
        if call is None:
            return None
        chain, args = spec.call_parts(call, sf)
        if chain is None:
            return None
        line = call.start_point[0] + 1
        targets = [e for e in g.out_edges(n.id, EdgeKind.CALLS) if e.line == line and g.node(e.dst).kind in CALLABLE_KINDS | {NodeKind.CLASS}]
        if len(targets) != 1:
            return None
        tnode = g.node(targets[0].dst)
        if tnode.id == n.id or tnode.kind is NodeKind.CLASS:
            return None
        params = [p for p in n.attrs.get("params", []) if p not in spec.self_names]
        passthrough = [a.split("=")[-1].strip() for a in args] == params
        return tnode, passthrough

    @staticmethod
    def _overrides(program: Program, cls: Node, method_name: str) -> bool:
        g = program.graph
        if cls.attrs.get("unresolved_bases"):
            return True
        for e in g.out_edges(cls.id, EdgeKind.INHERITS):
            base = g.node(e.dst)
            if base.kind is NodeKind.PACKAGE:
                return True
            if any(c.name == method_name for c in g.children(base.id)):
                return True
            if WrappersPass._overrides(program, base, method_name):
                return True
        return False
