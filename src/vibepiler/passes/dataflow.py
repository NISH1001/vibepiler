"""Backward liveness analysis over a function body, the compiler way.

    in[s]  = use[s] ∪ (out[s] − def[s])
    out[s] = ⋃ in[s′] over every successor s′ of s

solved to a fixpoint over the structured control flow the adapters describe (see frontend/flow.py). A store to `v`
at `s` is dead when `v ∉ out[s]`. When in doubt, a name is live: names read by nested functions are live everywhere,
every statement in a `try` may jump to its handlers and its `finally`, and functions that touch their own scope
dynamically (`locals()`, `eval`) are skipped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tree_sitter import Node

from vibepiler.frontend.flow import GLOBAL, Branch, Def, Flow, Jump, Loop, Plain, Try, With
from vibepiler.frontend.languages.base import LanguageSpec
from vibepiler.frontend.loader import SourceFile

HOISTED = frozenset({"function_declaration", "generator_function_declaration", "type_alias_declaration", "interface_declaration"})  # JS/TS: in scope before any statement runs
MAX_ROUNDS = 50  # a loop's fixpoint settles in two or three; this only guards against a bug


@dataclass(slots=True)
class Store:
    d: Def
    live: bool
    overwritten_at: int | None = None  # line of the next store to the same name, when this one is dead and one exists


@dataclass(slots=True)
class FunctionLiveness:
    params: list[tuple[str, str, bool]] = field(default_factory=list)  # (name, kind, read)
    stores: list[Store] = field(default_factory=list)
    unreachable: list[tuple[Node, Node]] = field(default_factory=list)  # (first dead statement, last), per block
    dead_branches: list[tuple[Node, Node]] = field(default_factory=list)  # (clause, last statement of the dead arm)
    skipped: bool = False


def analyze(def_node: Node, spec: LanguageSpec, sf: SourceFile) -> FunctionLiveness:
    w = _Walker(spec, sf, spec.captured_names(def_node, sf))
    entry = w.block(spec.body_statements(def_node, sf), set())
    result = FunctionLiveness(unreachable=list(w.unreachable.values()), dead_branches=list(w.dead_branches.values()))
    if spec.has_dynamic_scope(def_node, sf):
        result.skipped = True
        return result
    result.params = [(name, kind, name in entry or name in w.captured) for name, kind in spec.function_params(def_node, sf)]
    result.stores = w.stores()
    return result


class _Walker:
    def __init__(self, spec: LanguageSpec, sf: SourceFile, captured: set[str]) -> None:
        self.spec, self.sf, self.captured = spec, sf, captured
        self.records: dict[tuple, tuple[Def, bool]] = {}
        self.unreachable: dict[int, tuple[Node, Node]] = {}
        self.dead_branches: dict[int, tuple[Node, Node]] = {}
        self.globals: set[str] = set()
        self.loops: list[tuple[str | None, set[str], set[str]]] = []  # (label, live at break, live at continue)
        self.handlers: list[set[str]] = []  # live on entry to the enclosing try's handlers and finally
        self._flows: dict[tuple, Flow] = {}

    def flow(self, s: Node) -> Flow:
        key = (s.start_byte, s.end_byte, s.type)
        if key not in self._flows:
            self._flows[key] = self.spec.classify(s, self.sf)
        return self._flows[key]

    def record(self, d: Def, live: bool) -> None:
        """Live if any walk over this definition found it live: loop rounds and `finally` are walked more than once."""
        key = (d.node.start_byte, d.node.type, d.name, d.kind, d.group)
        self.records[key] = (d, live or (key in self.records and self.records[key][1]))

    # ----- blocks ------------------------------------------------------------------------------
    def block(self, stmts: list[Node], out: set[str]) -> set[str]:
        for i, s in enumerate(stmts[:-1]):
            if self.terminates(s):
                run: list[Node] = []
                for t in [*stmts[i + 1 :], None]:  # report each run of dead statements between hoisted declarations
                    if t is not None and t.type not in HOISTED:
                        run.append(t)
                    elif run:
                        self.unreachable[run[0].start_byte] = (run[0], run[-1])
                        run = []
                stmts = stmts[: i + 1]
                break
        live = out
        for s in reversed(stmts):
            live = self.stmt(s, live)
        return live

    def terminates(self, s: Node) -> bool:
        f = self.flow(s)
        if isinstance(f, Jump):
            return True
        if isinstance(f, Branch) and f.node.type != "switch_statement":  # a switch may be left by a break we do not model
            arms = f.arms[:1] if f.constant is True else f.arms[1:] if f.constant is False else f.arms
            covered = f.has_else or f.constant is True
            return covered and bool(arms) and all(a and self.terminates(a[-1]) for _, a in arms)
        return False

    # ----- statements --------------------------------------------------------------------------
    def stmt(self, s: Node, out: set[str]) -> set[str]:
        extra = self.captured.union(*self.handlers)
        out = out | extra
        f = self.flow(s)
        if isinstance(f, Plain):
            live = self.plain(f, out)
        elif isinstance(f, Branch):
            live = self.branch(f, out)
        elif isinstance(f, Loop):
            live = self.loop(f, out)
        elif isinstance(f, Try):
            live = self.try_(f, out)
        elif isinstance(f, With):
            live = self.with_(f, out)
        else:
            live = self.jump(f)
        return live | extra

    def plain(self, f: Plain, out: set[str]) -> set[str]:
        kills = set()
        for d in f.defs:
            if d.name.startswith(GLOBAL):
                self.globals.add(d.name.removeprefix(GLOBAL))
            elif d.kind == "attribute":
                self.record(d, True)  # an attribute's liveness is the project's question, not this function's
            else:
                self.record(d, d.name in out)
                kills.add(d.name)
        return f.uses | (out - kills)

    def branch(self, f: Branch, out: set[str]) -> set[str]:
        arms, fallthrough = f.arms, not f.has_else
        if f.constant is True:
            arms, fallthrough = f.arms[:1], False
        elif f.constant is False:
            arms = f.arms[1:]
        for clause, body in f.arms:
            if not any(clause is a for a, _ in arms):
                self.dead_branches[clause.start_byte] = (clause, body[-1] if body else clause)
        live = set(f.uses)
        for _, body in arms:
            live |= self.block(body, out)
        return live | out if fallthrough else live

    def loop(self, f: Loop, out: set[str]) -> set[str]:
        else_in = self.block(f.else_body, out) if f.else_body else out
        if f.constant is False:
            if f.body:
                self.dead_branches[f.node.start_byte] = (f.node, f.body[-1])
            return f.uses | else_in
        exit_ = set() if f.constant is True else else_in  # `while True` is left only by a break
        targets = {d.name for d in f.defs if d.kind != "attribute"}
        head = f.uses | exit_
        body_in: set[str] = set()
        for _ in range(MAX_ROUNDS):
            self.loops.append((f.label, out, head))
            body_in = self.block(f.body, head)
            self.loops.pop()
            new = f.uses | exit_ | (body_in - targets) | head
            if new == head:
                break
            head = new
        for d in f.defs:
            self.record(d, d.kind == "attribute" or d.name in body_in or d.name in f.uses)
        return head

    def try_(self, f: Try, out: set[str]) -> set[str]:
        final_in = self.block(f.final, out) if f.final else out
        caught: set[str] = set()
        for defs, body in f.handlers:
            h_in = self.block(body, final_in)
            for d in defs:
                self.record(d, d.name in h_in)
            caught |= h_in - {d.name for d in defs}
        if f.final:
            caught |= self.block(f.final, set())  # an exception nobody catches still runs finally
        else_in = self.block(f.else_body, final_in) if f.else_body else final_in
        self.handlers.append(caught)
        body_in = self.block(f.body, else_in)
        self.handlers.pop()
        return body_in | caught

    def with_(self, f: With, out: set[str]) -> set[str]:
        self.handlers.append(out)  # `suppress(...)` and friends: any statement in the body may end it early
        body_in = self.block(f.body, out)
        self.handlers.pop()
        for d in f.defs:
            self.record(d, d.kind == "attribute" or d.name in body_in)
        return f.uses | (body_in - {d.name for d in f.defs})

    def jump(self, f: Jump) -> set[str]:
        if f.kind in ("return", "raise"):
            return set(f.uses)
        target = next((lp for lp in reversed(self.loops) if f.label is None or lp[0] == f.label), None)
        if target is None:
            return set(f.uses)
        return f.uses | (target[1] if f.kind == "break" else target[2])

    # ----- the answer --------------------------------------------------------------------------
    def stores(self) -> list[Store]:
        found = [(d, live) for d, live in self.records.values() if d.name not in self.globals]
        found.sort(key=lambda x: x[0].node.start_byte)
        out = []
        for i, (d, live) in enumerate(found):
            nxt = next((e for e, _ in found[i + 1 :] if e.name == d.name and e.kind != "attribute" and e.node.start_byte > d.node.start_byte), None)
            out.append(Store(d, live, nxt.node.start_point[0] + 1 if not live and nxt is not None else None))
        return out
