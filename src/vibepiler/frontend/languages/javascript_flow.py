"""JavaScript and TypeScript statements as flow shapes, for the liveness dataflow. Mixed into JavaScriptSpec."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tree_sitter import Node

from vibepiler.frontend.flow import Branch, Def, Flow, Jump, Loop, Plain, Try, statements

if TYPE_CHECKING:
    from vibepiler.frontend.loader import SourceFile

READS = frozenset({"identifier", "shorthand_property_identifier"})
SIDE_EFFECTS = frozenset({"call_expression", "await_expression", "new_expression", "yield_expression"})
PATTERNS = frozenset({"object_pattern", "array_pattern"})
LOOPS = frozenset({"for_statement", "for_in_statement", "while_statement", "do_statement"})
BREAKABLE = LOOPS | {"switch_statement"}


class JavaScriptFlow:
    self_names: frozenset[str]
    function_types: frozenset[str]

    def classify(self, stmt: Node, sf: SourceFile) -> Flow:
        n = stmt.named_children[0] if stmt.type == "expression_statement" and stmt.named_child_count == 1 else stmt
        shape = _SHAPES.get(n.type)
        return shape(self, stmt, n, sf) if shape else Plain(stmt, [], self.names_in(n, sf))

    # ----- names -------------------------------------------------------------------------------
    def names_in(self, node: Node | None, sf: SourceFile) -> set[str]:
        out: set[str] = set()
        stack = [node] if node is not None else []
        while stack:
            n = stack.pop()
            if n.type in READS:
                out.add(sf.text(n))
                continue
            stack.extend(n.children)
        return out

    def function_params(self, def_node: Node, sf: SourceFile) -> list[tuple[str, str]]:
        single = def_node.child_by_field_name("parameter")  # x => ...
        if single is not None:
            return [(sf.text(single), "plain")] if single.type == "identifier" else []
        params = def_node.child_by_field_name("parameters")
        out: list[tuple[str, str]] = []
        for p in params.named_children if params is not None else []:
            if p.type in ("required_parameter", "optional_parameter"):  # TypeScript wraps every parameter
                p = p.child_by_field_name("pattern") or p
            if p.type == "identifier":
                out.append((sf.text(p), "plain"))
            elif p.type == "assignment_pattern" and (left := p.child_by_field_name("left")) is not None and left.type == "identifier":
                out.append((sf.text(left), "plain"))
            elif p.type == "rest_pattern":
                ident = next((c for c in p.named_children if c.type == "identifier"), None)
                out.append((sf.text(ident) if ident is not None else sf.text(p), "rest"))
            elif p.type in PATTERNS or p.type == "assignment_pattern":
                out.append((sf.text(p), "rest"))  # destructured: no single name to drop
        return out

    def captured_names(self, def_node: Node, sf: SourceFile) -> set[str]:
        out: set[str] = set()
        body = def_node.child_by_field_name("body")
        stack = list(body.children) if body is not None else []
        while stack:
            n = stack.pop()
            if n.type in self.function_types:
                own = {name for name, _ in self.function_params(n, sf)}
                out |= self.names_in(n.child_by_field_name("body"), sf) - own
                continue
            stack.extend(n.children)
        return out

    def has_dynamic_scope(self, def_node: Node, sf: SourceFile) -> bool:
        body = def_node.child_by_field_name("body")
        stack = list(body.children) if body is not None else []
        while stack:
            n = stack.pop()
            if n.type in self.function_types and n.type != "arrow_function":  # arrows share `arguments` with their parent
                continue
            if n.type == "with_statement" or (n.type == "identifier" and sf.text(n) in ("eval", "arguments")):
                return True
            stack.extend(n.children)
        return False

    # ----- targets -----------------------------------------------------------------------------
    def _targets(self, t: Node | None, stmt: Node, sf: SourceFile, defs: list[Def], uses: set[str], has_call: bool, group: int = 0) -> None:
        if t is None:
            return
        if t.type in ("identifier", "shorthand_property_identifier_pattern"):
            defs.append(Def(sf.text(t), stmt, "destructure" if group else "local", group, has_call))
        elif t.type in PATTERNS:
            g = group or t.start_byte + 1
            for c in t.named_children:
                self._targets(c, stmt, sf, defs, uses, has_call, g)
        elif t.type == "pair_pattern":
            self._targets(t.child_by_field_name("value"), stmt, sf, defs, uses, has_call, group)
        elif t.type in ("rest_pattern", "object_assignment_pattern", "assignment_pattern"):
            left = t.child_by_field_name("left") or (t.named_children[0] if t.named_child_count else None)
            right = t.child_by_field_name("right")
            uses |= self.names_in(right, sf)
            self._targets(left, stmt, sf, defs, uses, has_call, group)
        elif t.type == "member_expression" and (obj := t.child_by_field_name("object")) is not None and obj.type == "this":
            defs.append(Def(sf.text(t.child_by_field_name("property")), stmt, "attribute", 0, has_call, attr_of="this"))
        else:
            uses |= self.names_in(t, sf)

    # ----- shapes ------------------------------------------------------------------------------
    def _declaration(self, stmt: Node, n: Node, sf: SourceFile) -> Plain:
        defs: list[Def] = []
        uses: set[str] = set()
        for decl in (c for c in n.named_children if c.type == "variable_declarator"):
            value = decl.child_by_field_name("value")
            if value is None:  # `let x;` declares, it does not store
                continue
            uses |= self.names_in(value, sf)
            self._targets(decl.child_by_field_name("name"), stmt, sf, defs, uses, _has(value, SIDE_EFFECTS))
        return Plain(stmt, defs, uses)

    def _assignment(self, stmt: Node, n: Node, sf: SourceFile) -> Plain:
        right = n.child_by_field_name("right")
        defs: list[Def] = []
        uses = self.names_in(right, sf)
        self._targets(n.child_by_field_name("left"), stmt, sf, defs, uses, _has(right, SIDE_EFFECTS) if right is not None else False)
        if n.type == "augmented_assignment_expression":
            uses |= {d.name for d in defs if d.kind != "attribute"}
        return Plain(stmt, defs, uses)

    def _if(self, stmt: Node, n: Node, sf: SourceFile) -> Branch:
        cond = n.child_by_field_name("condition")
        br = Branch(stmt, set(), [], False, constant(cond, sf))
        cur: Node | None = n
        while cur is not None:
            br.uses |= self.names_in(cur.child_by_field_name("condition"), sf)
            br.arms.append((cur, _block(cur.child_by_field_name("consequence"))))
            alt = cur.child_by_field_name("alternative")
            inner = alt.named_children[0] if alt is not None and alt.named_child_count else None
            if inner is not None and inner.type == "if_statement":
                cur = inner
                continue
            if inner is not None:
                br.arms.append((alt, _block(inner)))
                br.has_else = True
            cur = None
        return br

    def _switch(self, stmt: Node, n: Node, sf: SourceFile) -> Branch:
        body = n.child_by_field_name("body")
        cases = [c for c in body.named_children if c.type in ("switch_case", "switch_default")] if body is not None else []
        uses = self.names_in(n.child_by_field_name("value"), sf)
        own = []
        for c in cases:
            value = c.child_by_field_name("value")
            uses |= self.names_in(value, sf)
            own.append([s for s in c.named_children if s != value and s.type != "comment"])
        # fallthrough: an arm may run into every case after it
        arms = [(c, [s for rest in own[i:] for s in rest]) for i, c in enumerate(cases)]
        return Branch(stmt, uses, arms, any(c.type == "switch_default" for c in cases))

    def _for(self, stmt: Node, n: Node, sf: SourceFile, label: str | None = None) -> Loop:
        defs: list[Def] = []
        uses: set[str] = set()
        if n.type == "for_in_statement":
            uses |= self.names_in(n.child_by_field_name("right"), sf)
            self._targets(n.child_by_field_name("left"), stmt, sf, defs, uses, False)
        else:  # for (init; cond; step): the init runs once, cond and step every iteration
            init = n.child_by_field_name("initializer")
            if init is not None and init.type in ("lexical_declaration", "variable_declaration"):
                p = self._declaration(stmt, init, sf)
                defs, uses = p.defs, p.uses
            else:
                uses |= self.names_in(init, sf)
            for f in ("condition", "increment"):
                for c in n.children_by_field_name(f):
                    uses |= self.names_in(c, sf)
        for d in defs:
            if d.kind != "attribute":
                d.kind = "loop"
        return Loop(stmt, defs, uses, _block(n.child_by_field_name("body")), label=label)

    def _while(self, stmt: Node, n: Node, sf: SourceFile, label: str | None = None) -> Loop:
        cond = n.child_by_field_name("condition")
        return Loop(stmt, [], self.names_in(cond, sf), _block(n.child_by_field_name("body")), constant=constant(cond, sf) if n.type == "while_statement" else None, label=label)

    def _labeled(self, stmt: Node, n: Node, sf: SourceFile) -> Flow:
        inner = n.child_by_field_name("body")
        label = sf.text(n.child_by_field_name("label"))
        if inner is not None and inner.type in ("for_statement", "for_in_statement"):
            return self._for(stmt, inner, sf, label)
        if inner is not None and inner.type in ("while_statement", "do_statement"):
            return self._while(stmt, inner, sf, label)
        return Plain(stmt, [], self.names_in(n, sf))

    def _try(self, stmt: Node, n: Node, sf: SourceFile) -> Try:
        t = Try(stmt, _block(n.child_by_field_name("body")), [])
        handler = n.child_by_field_name("handler")
        if handler is not None:
            param = handler.child_by_field_name("parameter")
            defs = [Def(sf.text(param), handler, "except")] if param is not None and param.type == "identifier" else []
            t.handlers.append((defs, _block(handler.child_by_field_name("body"))))
        final = n.child_by_field_name("finalizer")
        if final is not None:
            t.final = _block(final.child_by_field_name("body"))
        return t

    def _jump(self, stmt: Node, n: Node, sf: SourceFile) -> Flow:
        kind = {"return_statement": "return", "throw_statement": "raise", "break_statement": "break", "continue_statement": "continue"}[n.type]
        label = n.child_by_field_name("label")
        if kind == "break" and label is None and _nearest_breakable(n) == "switch_statement":
            return Plain(stmt, [], set())  # leaves the switch, not a loop: falls through as far as the analysis cares
        return Jump(stmt, kind, self.names_in(n, sf) if kind in ("return", "raise") else set(), sf.text(label) if label is not None else None)  # type: ignore[arg-type]


def constant(cond: Node | None, sf: SourceFile) -> bool | None:
    if cond is None:
        return None
    if cond.type == "parenthesized_expression" and cond.named_child_count == 1:
        return constant(cond.named_children[0], sf)
    if cond.type in ("true", "false", "null", "undefined"):
        return cond.type == "true"
    if cond.type == "number" and sf.text(cond) in ("0", "1"):
        return sf.text(cond) == "1"
    if cond.type == "unary_expression" and sf.text(cond).startswith("!"):
        inner = constant(cond.child_by_field_name("argument"), sf)
        return None if inner is None else not inner
    return None


def _block(node: Node | None) -> list[Node]:
    if node is None:
        return []
    return statements(node) if node.type == "statement_block" else [node]


def _nearest_breakable(n: Node) -> str | None:
    p = n.parent
    while p is not None:
        if p.type in BREAKABLE:
            return p.type
        p = p.parent
    return None


def _has(node: Node, types: frozenset[str]) -> bool:
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type in types:
            return True
        stack.extend(n.children)
    return False


_SHAPES = {
    "lexical_declaration": JavaScriptFlow._declaration,
    "variable_declaration": JavaScriptFlow._declaration,
    "assignment_expression": JavaScriptFlow._assignment,
    "augmented_assignment_expression": JavaScriptFlow._assignment,
    "if_statement": JavaScriptFlow._if,
    "switch_statement": JavaScriptFlow._switch,
    "for_statement": JavaScriptFlow._for,
    "for_in_statement": JavaScriptFlow._for,
    "while_statement": JavaScriptFlow._while,
    "do_statement": JavaScriptFlow._while,
    "labeled_statement": JavaScriptFlow._labeled,
    "try_statement": JavaScriptFlow._try,
    "return_statement": JavaScriptFlow._jump,
    "throw_statement": JavaScriptFlow._jump,
    "break_statement": JavaScriptFlow._jump,
    "continue_statement": JavaScriptFlow._jump,
}
