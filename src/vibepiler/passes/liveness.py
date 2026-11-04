"""liveness: what a body defines that nothing reads. Liveness analysis feeding dead-store elimination.

Each function's body goes through the backward dataflow in `dataflow.py`; this pass turns its answers into findings
(stores no read reaches, trailing parameters never read, statements after a terminator, branches on a constant) and
adds one the body alone cannot answer: an instance attribute is dead when no attribute access anywhere in the project
reads that name. Every exemption below is a rule; when in doubt a definition is live.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from tree_sitter import Node as TSNode

from vibepiler.frontend.flow import Def
from vibepiler.ir.graph import ProgramGraph
from vibepiler.ir.model import EdgeKind, Node, NodeKind
from vibepiler.ir.program import Program
from vibepiler.passes.abstraction import AbstractionPass
from vibepiler.passes.base import Pass
from vibepiler.passes.dataflow import FunctionLiveness, Store, analyze
from vibepiler.passes.diagnostic import Diagnostic, Severity

ATTRIBUTE_HOOKS = ("__getattr__", "__getattribute__", "__slots__")
COMPOUND_TARGETS = ("loop", "with", "except")  # the statement is bigger than the binding: drop the name, keep the code


def is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def span(first: TSNode, last: TSNode) -> list[int]:
    return [first.start_point[0] + 1, last.end_point[0] + 1]


@dataclass(slots=True)
class Analysed:
    node: Node
    result: FunctionLiveness
    reportable_params: list[tuple[str, bool]]  # (name, read) after the exemptions
    dead_stores: list[Store]  # after the exemptions
    counted_stores: list[Store]  # every store the metric counts (locals; attributes are counted on the class)


class LivenessPass(Pass):
    name = "liveness"
    description = "Stores no read reaches, parameters never read, instance attributes never read, statements after a terminator, branches on a constant."
    analogue = "liveness analysis / dead-store elimination"
    cost = 1
    requires = ("entry-points", "dead-code")

    def run(self, program: Program) -> list[Diagnostic]:
        self._functions: dict[str, Analysed] = {}
        self._attributes: dict[str, list[tuple[str, list[Store], bool]]] = {}  # class id -> (name, stores, live)
        self._attr_stores: dict[str, list[Store]] = {}  # class id -> every attribute store its methods make
        diags: list[Diagnostic] = []
        for fn, sd, spec, sf in self._bodies(program):
            a = self._analyse(program, fn, analyze(sd.node, spec, sf))
            self._functions[fn.id] = a
            diags += self._store_findings(fn, a)
            diags += self._param_findings(fn, a)
            diags += [self.diag("unreachable-code", Severity.WARNING, f"`{fn.qualname}`: statements after a return, raise, break or continue can never run", node=fn, line=span(f, l)[0], suggestion="Delete them.", span=span(f, l), loc=span(f, l)[1] - span(f, l)[0] + 1, action="delete", confidence="proven") for f, l in a.result.unreachable]
            diags += [self.diag("dead-branch", Severity.WARNING, f"`{fn.qualname}`: a branch on a constant condition that can never run", node=fn, line=span(f, l)[0], suggestion="Delete the branch.", span=span(f, l), loc=span(f, l)[1] - span(f, l)[0] + 1, action="delete", confidence="proven") for f, l in a.result.dead_branches]
        diags += self._attribute_findings(program)
        counts = defaultdict(int, {d.code: 0 for d in diags})
        for d in diags:
            counts[d.code] += 1
        program.metrics["liveness"] = {
            "dead_stores": counts["dead-store"],
            "unused_params": counts["unused-param"],
            "unused_attributes": counts["unused-attribute"],
            "unreachable": counts["unreachable-code"],
            "dead_branches": counts["dead-branch"],
            "skipped_functions": sum(1 for a in self._functions.values() if a.result.skipped),
        }
        return sorted(diags, key=lambda d: (d.file or "", d.line or 0, d.code))

    # ----- 1. every function with a body ---------------------------------------------------------
    @staticmethod
    def _bodies(program: Program):  # noqa: ANN205
        for fn in sorted(program.graph.nodes((NodeKind.FUNCTION, NodeKind.METHOD)), key=lambda n: (n.file or "", n.start_line or 0)):
            sd = program.symbol_def(fn)
            if sd is None or sd.node is None or fn.module not in program.facts:
                continue
            yield fn, sd, program.spec_for(fn), program.facts[fn.module].file

    def _analyse(self, program: Program, fn: Node, r: FunctionLiveness) -> Analysed:
        locals_ = [s for s in r.stores if s.d.kind != "attribute"]
        counted = [s for s in locals_ if not s.d.name.startswith("_")]
        dead = self._dead_stores(locals_)
        params = [] if self._signature_imposed(program, fn) else [(n, read) for n, kind, read in r.params if kind == "plain" and not n.startswith("_")]
        cls = program.graph.parent(fn.id)
        if cls is not None and cls.kind is NodeKind.CLASS:
            for s in r.stores:
                if s.d.kind == "attribute":
                    self._attr_stores.setdefault(cls.id, []).append(s)
        return Analysed(fn, r, params, dead, counted)

    @staticmethod
    def _dead_stores(stores: list[Store]) -> list[Store]:
        """Dead stores after the exemptions: underscore names, and every member of a group with a live sibling."""
        live_groups = {(s.d.node.start_byte, s.d.group) for s in stores if s.d.group and s.live}
        return [s for s in stores if not s.live and not s.d.name.startswith("_") and not (s.d.group and (s.d.node.start_byte, s.d.group) in live_groups)]

    @staticmethod
    def _signature_imposed(program: Program, fn: Node) -> bool:
        """Someone else owns this signature: a framework, a protocol, a base class, or nobody yet (a stub)."""
        if fn.attrs.get("decorators") or fn.attrs.get("entry") or is_dunder(fn.name):
            return True
        sd = program.symbol_def(fn)
        spec = program.spec_for(fn)
        sf = program.facts[fn.module].file
        if spec.is_stub_body(spec.body_statements(sd.node, sf), sf):
            return True
        cls = program.graph.parent(fn.id)
        return cls is not None and cls.kind is NodeKind.CLASS and (program.graph.has_external_base(cls.id) or _overrides(program.graph, cls.id, fn.name))

    # ----- 2. findings ---------------------------------------------------------------------------
    def _store_findings(self, fn: Node, a: Analysed) -> list[Diagnostic]:
        by_stmt: dict[int, list[Store]] = defaultdict(list)
        for s in a.dead_stores:
            by_stmt[s.d.node.start_byte].append(s)
        all_defs: dict[int, list[Store]] = defaultdict(list)
        for s in a.result.stores:
            all_defs[s.d.node.start_byte].append(s)
        diags = []
        for key, stores in by_stmt.items():
            d0: Def = stores[0].d
            names = list(dict.fromkeys(s.d.name for s in stores))
            whole = len(stores) == len(all_defs[key]) and d0.kind not in COMPOUND_TARGETS and not any(s.d.rhs_has_call for s in stores)
            sp = span(d0.node, d0.node)
            over = next((s.overwritten_at for s in stores if s.overwritten_at), None)
            what = " and ".join(f"`{n}`" for n in names)
            why = f"overwritten at line {over} before it is read" if over else "never read"
            if whole:
                fix, action, loc = "Delete the assignment.", "delete", sp[1] - sp[0] + 1
            elif d0.kind in COMPOUND_TARGETS:
                fix, action, loc = "Drop the name (or rename it `_`).", "simplify", 0
            else:
                fix, action, loc = "Drop the assignment; keep the call if it has side effects.", "simplify", 0
            diags.append(self.diag("dead-store", Severity.WARNING, f"`{fn.qualname}`: {what} assigned at line {sp[0]} is {why}", node=fn, line=sp[0], suggestion=fix, names=names, span=sp, loc=loc, overwritten_at=over, action=action, confidence="proven"))
        return diags

    def _param_findings(self, fn: Node, a: Analysed) -> list[Diagnostic]:
        last_read = max((i for i, (_, _, read) in enumerate(a.result.params) if read), default=-1)
        tail = {n for n, _, _ in a.result.params[last_read + 1 :]}
        names = [n for n, read in a.reportable_params if not read and n in tail]
        if not names:
            return []
        what = ", ".join(f"`{n}`" for n in names)
        return [self.diag("unused-param", Severity.INFO, f"`{fn.qualname}` never reads {what}", node=fn, suggestion="Drop the parameter(s) and update the callers.", names=names, loc=0, action="simplify", confidence="proven")]

    def _attribute_findings(self, program: Program) -> list[Diagnostic]:
        g = program.graph
        reads = g.meta.get("read_member_names", {})
        diags = []
        for cid, stores in sorted(self._attr_stores.items()):
            cls = g.node(cid)
            exempt = self._attributes_exempt(program, cls)
            by_name: dict[str, list[Store]] = defaultdict(list)
            for s in stores:
                if not is_dunder(s.d.name):
                    by_name[s.d.name].append(s)
            self._attributes[cid] = []
            for name, ss in by_name.items():
                live = exempt or name in reads
                self._attributes[cid].append((name, ss, live))
                if live:
                    continue
                ls = sorted({s.d.node.start_point[0] + 1 for s in ss})
                loc = sum(span(s.d.node, s.d.node)[1] - span(s.d.node, s.d.node)[0] + 1 for s in {s.d.node.start_byte: s for s in ss}.values() if not s.d.rhs_has_call)
                diags.append(self.diag("unused-attribute", Severity.WARNING, f"`{cls.qualname}.{name}` is written and never read anywhere in the project", node=cls, line=ls[0], suggestion="Delete the attribute and its assignments.", names=[name], span=[ls[0], ls[0]], lines=ls, loc=loc, action="delete", confidence="inferred"))
        return diags

    @staticmethod
    def _attributes_exempt(program: Program, cls: Node) -> bool:
        """A class whose attributes may be read in ways no name lookup sees."""
        if program.graph.has_external_base(cls.id) or AbstractionPass._is_data_class(cls):
            return True
        if any(c.name in ATTRIBUTE_HOOKS for c in program.graph.children(cls.id)):
            return True
        sd = program.symbol_def(cls)
        return sd is not None and sd.node is not None and "__slots__" in program.facts[cls.module].file.text(sd.node)



def _overrides(g: ProgramGraph, cls_id: str, name: str, depth: int = 0) -> bool:
    """True when a resolved base class, at any depth, defines a member called `name`."""
    if depth > 10:
        return False
    for e in g.out_edges(cls_id, EdgeKind.INHERITS):
        base = g.node(e.dst)
        if base.kind is NodeKind.CLASS and (any(c.name == name for c in g.children(base.id)) or _overrides(g, base.id, name, depth + 1)):
            return True
    return False
