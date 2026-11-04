"""Python statements as flow shapes, for the liveness dataflow. Mixed into PythonSpec."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, ClassVar

from tree_sitter import Node

from vibepiler.frontend.flow import GLOBAL, Branch, Def, Jump, Loop, Plain, Try, With, contains_any, statements

if TYPE_CHECKING:
    from vibepiler.frontend.loader import SourceFile

SEQUENCES = frozenset({"pattern_list", "tuple_pattern", "list_pattern", "tuple", "list", "parenthesized_expression"})
SPLATS = frozenset({"list_splat_pattern", "list_splat"})
SIDE_EFFECTS = frozenset({"call", "await", "yield"})
DYNAMIC = frozenset({"locals", "vars", "globals", "eval", "exec"})
NESTED = frozenset({"function_definition", "lambda"})
NAME_LOOKUPS = frozenset({"getattr", "hasattr", "attrgetter", "methodcaller"})  # a string argument names an attribute


class PythonFlow:
    self_names: frozenset[str]

    # ----- names -------------------------------------------------------------------------------
    def names_in(self, node: Node | None, sf: SourceFile) -> set[str]:
        """Identifiers read under `node`: not an attribute's member name, not a keyword argument's name."""
        out: set[str] = set()
        stack = [node] if node is not None else []
        while stack:
            n = stack.pop()
            if n.type == "identifier":
                p = n.parent
                if p is not None and ((p.type == "attribute" and p.child_by_field_name("attribute") == n) or (p.type == "keyword_argument" and p.child_by_field_name("name") == n)):
                    continue
                out.add(sf.text(n))
                continue
            stack.extend(n.children)
        return out

    def function_params(self, def_node: Node, sf: SourceFile) -> list[tuple[str, str]]:
        params = def_node.child_by_field_name("parameters")
        out: list[tuple[str, str]] = []
        for i, p in enumerate(params.named_children if params is not None else []):
            inner = p.named_children[0] if p.type == "typed_parameter" and p.named_child_count else p
            if inner.type in ("list_splat_pattern", "dictionary_splat_pattern"):
                ident = next((c for c in inner.named_children if c.type == "identifier"), None)
                if ident is not None:
                    out.append((sf.text(ident), "rest"))
            elif inner.type == "identifier":
                name = sf.text(inner)
                out.append((name, "self" if i == 0 and name in self.self_names else "plain"))
            elif inner.type in ("default_parameter", "typed_default_parameter"):
                nm = inner.child_by_field_name("name")
                if nm is not None:
                    out.append((sf.text(nm), "plain"))
        return out

    def captured_names(self, def_node: Node, sf: SourceFile) -> set[str]:
        out: set[str] = set()
        stack = list(def_node.child_by_field_name("body").children) if def_node.child_by_field_name("body") is not None else []
        while stack:
            n = stack.pop()
            if n.type in NESTED:
                own = {name for name, _ in self.function_params(n, sf)}
                out |= self.names_in(n.child_by_field_name("body"), sf) - own
                continue
            stack.extend(n.children)
        return out

    def has_dynamic_scope(self, def_node: Node, sf: SourceFile) -> bool:
        stack = [def_node.child_by_field_name("body")]
        while stack:
            n = stack.pop()
            if n is None:
                continue
            if n.type == "call":
                fn = n.child_by_field_name("function")
                if fn is not None and fn.type == "identifier" and sf.text(fn) in DYNAMIC:
                    return True
            stack.extend(n.children)
        return False

    def attribute_reads(self, sf: SourceFile) -> list[str]:
        out: list[str] = []
        stack = [sf.tree.root_node]
        while stack:
            n = stack.pop()
            stack.extend(n.children)
            if n.type == "attribute":
                p = n.parent
                if not (p is not None and p.type == "assignment" and p.child_by_field_name("left") == n):
                    out.append(sf.text(n.child_by_field_name("attribute")))
            elif n.type == "call" and (fn := n.child_by_field_name("function")) is not None and sf.text(fn).split(".")[-1] in NAME_LOOKUPS:
                args = n.child_by_field_name("arguments")
                out += [sf.text(c) for a in (args.named_children if args is not None else []) if a.type == "string" for c in a.named_children if c.type == "string_content"]
        return out

    # ----- shapes ------------------------------------------------------------------------------
    def _assignment(self, stmt: Node, n: Node, sf: SourceFile) -> Plain:
        targets, right = [], n
        while right is not None and right.type in ("assignment", "augmented_assignment"):
            targets.append(right.child_by_field_name("left"))
            right = right.child_by_field_name("right")
        if right is None:  # `x: int`, an annotation without a value
            return Plain(stmt, [], self.names_in(n.child_by_field_name("type"), sf))
        has_call = contains_any(right, SIDE_EFFECTS)
        defs: list[Def] = []
        uses = self.names_in(right, sf) | self.names_in(n.child_by_field_name("type"), sf)  # `v: T = 3` reads T
        if n.type == "augmented_assignment":  # `acc |= x` may mutate in place: a read, never a store we could drop
            return Plain(stmt, [], uses | self.names_in(targets[0], sf))
        for t in targets:
            self._targets(t, stmt, sf, defs, uses, has_call)
        return Plain(stmt, defs, uses)

    def _targets(self, t: Node, stmt: Node, sf: SourceFile, defs: list[Def], uses: set[str], has_call: bool, group: int = 0) -> None:
        if t.type == "identifier":
            defs.append(Def(sf.text(t), stmt, "destructure" if group else "local", group, has_call))
        elif t.type in SEQUENCES:
            g = group or t.start_byte + 1
            for c in t.named_children:
                self._targets(c, stmt, sf, defs, uses, has_call, g)
        elif t.type in SPLATS:
            for c in t.named_children:
                self._targets(c, stmt, sf, defs, uses, has_call, group)
        elif t.type == "attribute" and (obj := t.child_by_field_name("object")) is not None and obj.type == "identifier" and sf.text(obj) in self.self_names:
            defs.append(Def(sf.text(t.child_by_field_name("attribute")), stmt, "attribute", 0, has_call, attr_of=sf.text(obj)))
            uses.add(sf.text(obj))
        else:  # obj.attr = ..., a[i] = ...: reads of the container, not definitions
            uses |= self.names_in(t, sf)

    def _if(self, stmt: Node, n: Node, sf: SourceFile) -> Branch:
        cond = n.child_by_field_name("condition")
        arms = [(n, statements(n.child_by_field_name("consequence")))]
        uses, has_else = self.names_in(cond, sf), False
        for alt in n.children_by_field_name("alternative"):
            if alt.type == "elif_clause":
                arms.append((alt, statements(alt.child_by_field_name("consequence"))))
                uses |= self.names_in(alt.child_by_field_name("condition"), sf)
            else:
                arms.append((alt, statements(alt.child_by_field_name("body"))))
                has_else = True
        return Branch(stmt, uses, arms, has_else, constant(cond, sf))

    def _match(self, stmt: Node, n: Node, sf: SourceFile) -> Branch:
        uses = self.names_in(n.child_by_field_name("subject"), sf)
        arms = []
        for case in (c for c in (n.child_by_field_name("body").named_children if n.child_by_field_name("body") is not None else []) if c.type == "case_clause"):
            body = case.child_by_field_name("consequence")
            uses |= {x for c in case.named_children if c != body for x in self.names_in(c, sf)}
            arms.append((case, statements(body)))
        return Branch(stmt, uses, arms, False)

    def _for(self, stmt: Node, n: Node, sf: SourceFile) -> Loop:
        defs: list[Def] = []
        uses = self.names_in(n.child_by_field_name("right"), sf)
        self._targets(n.child_by_field_name("left"), stmt, sf, defs, uses, False)
        for d in defs:
            if d.kind != "attribute":
                d.kind = "loop"
        alt = n.child_by_field_name("alternative")
        return Loop(stmt, defs, uses, statements(n.child_by_field_name("body")), statements(alt.child_by_field_name("body")) if alt is not None else [])

    def _while(self, stmt: Node, n: Node, sf: SourceFile) -> Loop:
        cond = n.child_by_field_name("condition")
        alt = n.child_by_field_name("alternative")
        return Loop(stmt, [], self.names_in(cond, sf), statements(n.child_by_field_name("body")), statements(alt.child_by_field_name("body")) if alt is not None else [], constant(cond, sf))

    def _try(self, stmt: Node, n: Node, sf: SourceFile) -> Try:
        t = Try(stmt, statements(n.child_by_field_name("body")), [])
        for c in n.named_children:
            block = next((b for b in c.named_children if b.type == "block"), None)
            if c.type in ("except_clause", "except_group_clause"):
                defs = []
                value = c.child_by_field_name("value") or next((x for x in c.named_children if x.type == "as_pattern"), None)
                target = next((x for x in value.named_children if x.type == "as_pattern_target"), None) if value is not None and value.type == "as_pattern" else None
                ident = next((x for x in target.named_children if x.type == "identifier"), None) if target is not None else None
                if ident is not None:
                    defs.append(Def(sf.text(ident), c, "except"))
                t.handlers.append((defs, statements(block)))
            elif c.type == "else_clause":
                t.else_body = statements(c.child_by_field_name("body") or block)
            elif c.type == "finally_clause":
                t.final = statements(block)
        return t

    def _with(self, stmt: Node, n: Node, sf: SourceFile) -> With:
        defs: list[Def] = []
        uses: set[str] = set()
        clause = next((c for c in n.named_children if c.type == "with_clause"), None)
        for item in clause.named_children if clause is not None else []:
            value = item.child_by_field_name("value")
            if value is not None and value.type == "as_pattern":
                target = next((x for x in value.named_children if x.type == "as_pattern_target"), None)
                uses |= self.names_in(value.named_children[0], sf)
                for t in target.named_children if target is not None else []:
                    self._targets(t, stmt, sf, defs, uses, False)
            else:
                uses |= self.names_in(value, sf)
        for d in defs:
            if d.kind != "attribute":
                d.kind = "with"
        return With(stmt, defs, uses, statements(n.child_by_field_name("body")))

    def _jump(self, stmt: Node, n: Node, sf: SourceFile) -> Jump:
        return Jump(stmt, n.type.removesuffix("_statement"), self.names_in(n, sf))  # type: ignore[arg-type]

    def _global(self, stmt: Node, n: Node, sf: SourceFile) -> Plain:
        return Plain(stmt, [Def(GLOBAL + sf.text(c), stmt, "local") for c in n.named_children if c.type == "identifier"], set())

    # node type -> the method that builds its flow shape; real references, so the linker sees the dispatch
    flow_shapes: ClassVar[dict[str, Callable]] = {
        "assignment": _assignment,
        "augmented_assignment": _assignment,
        "if_statement": _if,
        "match_statement": _match,
        "for_statement": _for,
        "while_statement": _while,
        "try_statement": _try,
        "with_statement": _with,
        "return_statement": _jump,
        "raise_statement": _jump,
        "break_statement": _jump,
        "continue_statement": _jump,
        "global_statement": _global,
        "nonlocal_statement": _global,
    }


def constant(cond: Node | None, sf: SourceFile) -> bool | None:
    """The truth value of a literal condition; None for anything that is not a literal (TYPE_CHECKING, __debug__, names)."""
    if cond is None:
        return None
    if cond.type == "parenthesized_expression" and cond.named_child_count == 1:
        return constant(cond.named_children[0], sf)
    if cond.type in ("true", "false", "none"):
        return cond.type == "true"
    if cond.type == "integer":
        try:
            return bool(int(sf.text(cond), 0))
        except ValueError:
            return None
    if cond.type == "not_operator":
        inner = constant(cond.child_by_field_name("argument"), sf)
        return None if inner is None else not inner
    return None
