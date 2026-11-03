"""Python language adapter (tree-sitter-python grammar)."""

from __future__ import annotations

from pathlib import Path

from tree_sitter import Node

from vibepiler.frontend.facts import CallSite, EntryHint, FileFacts, ImportDef, RefSite, SymbolDef
from vibepiler.frontend.languages.base import ImportOption, LanguageSpec
from vibepiler.frontend.loader import SourceFile
from vibepiler.ir.model import NodeKind

ROUTE_DECORATORS = {
    "get", "post", "put", "delete", "patch", "options", "head", "route", "api_route",
    "websocket", "on_event", "middleware", "exception_handler", "before_request",
    "after_request", "errorhandler", "teardown_request", "lifespan",
}
COMMAND_DECORATORS = {"command", "group", "callback", "subcommand"}
TASK_DECORATORS = {"task", "shared_task", "periodic_task", "job", "cron", "schedule", "scheduled"}
HOOK_DECORATORS = {
    "fixture", "hookimpl", "receiver", "listens_for", "register", "subscribe",
    "event", "on", "tool", "agent", "handler", "signal", "validator", "field_validator",
    "model_validator", "root_validator", "property", "setter", "cached_property",
    "staticmethod", "classmethod", "override", "overload", "app",
}
ENTRY_FUNCTION_NAMES = {"main", "handler", "lambda_handler", "run", "serve", "cli", "app", "application"}

# identifiers that should never be treated as references
_SKIP_IDENT_PARENTS = {
    "keyword_argument": "name",
    "function_definition": "name",
    "class_definition": "name",
    "default_parameter": "name",
    "typed_parameter": None,
    "typed_default_parameter": "name",
    "parameters": None,
    "lambda_parameters": None,
    "dotted_name": None,
    "aliased_import": None,
    "import_statement": None,
    "import_from_statement": None,
    "relative_import": None,
    "as_pattern_target": None,
    "global_statement": None,
    "nonlocal_statement": None,
    "except_clause": None,  # `except E as e` -> `e`; the E is an identifier child too but we handle below
}


class PythonSpec(LanguageSpec):
    name = "python"
    branch_types = frozenset({
        "if_statement", "elif_clause", "for_statement", "while_statement", "except_clause",
        "boolean_operator", "conditional_expression", "for_in_clause", "case_clause",
        "match_statement", "with_statement", "assert_statement",
    })
    nesting_types = frozenset({
        "if_statement", "for_statement", "while_statement", "try_statement", "with_statement",
        "function_definition", "class_definition", "match_statement",
    })
    leaf_types = frozenset({"identifier", "string", "string_content", "integer", "float", "true", "false", "none"})
    function_types = frozenset({"function_definition"})

    # ------------------------------------------------------------------ naming
    def module_name(self, sf: SourceFile) -> tuple[str, list[str]]:
        rel = Path(sf.rel)
        stem = rel.stem
        dirs = list(rel.parent.parts)
        # walk up while the directory is a regular package
        pkg_dirs: list[str] = []
        d = sf.path.parent
        while d != sf.root.parent and (d / "__init__.py").exists():
            pkg_dirs.insert(0, d.name)
            if d == sf.root:
                break
            d = d.parent
        parts = pkg_dirs + ([] if stem == "__init__" else [stem])
        canonical = ".".join(parts) if parts else stem
        aliases: set[str] = set()
        # every suffix of the directory chain is a plausible import path (src.app.core, app.core, ...)
        for i in range(len(dirs)):
            suffix = dirs[i:] + ([] if stem == "__init__" else [stem])
            if suffix:
                aliases.add(".".join(suffix))
        if stem != "__init__":
            aliases.add(stem)
        aliases.discard(canonical)
        return canonical, sorted(aliases)

    def is_test_file(self, rel: str) -> bool:
        p = Path(rel)
        name = p.name
        return (
            name.startswith("test_")
            or name.endswith("_test.py")
            or name == "conftest.py"
            or any(part in {"tests", "test"} for part in p.parts[:-1])
        )

    # ---------------------------------------------------------------- imports
    builtins = frozenset(dir(__builtins__) if isinstance(__builtins__, dict) is False else __builtins__.keys()) | frozenset(
        {"print", "len", "range", "str", "int", "float", "list", "dict", "set", "tuple", "open", "super", "isinstance", "type", "Exception"}
    )
    self_names = frozenset({"self", "cls"})

    def _absolute_base(self, imp: ImportDef, facts: FileFacts) -> str:
        if imp.level == 0:
            return imp.module
        base_parts = facts.module_name.split(".") if facts.module_name else []
        if not facts.rel.endswith("__init__.py") and base_parts:
            base_parts = base_parts[:-1]
        up = imp.level - 1
        if up:
            base_parts = base_parts[: max(0, len(base_parts) - up)]
        return ".".join(base_parts + ([imp.module] if imp.module else []))

    def import_targets(self, imp: ImportDef, facts: FileFacts) -> list[ImportOption]:
        base = self._absolute_base(imp, facts)
        if imp.kind == "wildcard":
            return [ImportOption([base], None)]
        if imp.name is None:  # `import a.b.c [as x]`
            parts = base.split(".")
            candidates = [".".join(parts[:i]) for i in range(len(parts), 0, -1)]
            return [ImportOption(candidates, None, binding_path=base if imp.alias else parts[0])]
        full = f"{base}.{imp.name}" if base else imp.name
        return [ImportOption([full], None), ImportOption([base], imp.name)]

    def package_target(self, imp: ImportDef) -> tuple[str, tuple[str, ...]] | None:
        if imp.level > 0 or not imp.module:
            return None
        parts = imp.module.split(".")
        if imp.name is None and not imp.alias:
            return parts[0], ()
        prefix = tuple(parts[1:]) + ((imp.name,) if imp.name else ())
        return parts[0], prefix

    def implicit_imports(self, module_name: str) -> list[str]:
        parts = module_name.split(".")
        return [".".join(parts[:i]) for i in range(len(parts) - 1, 0, -1)]

    def is_entry_module(self, rel: str) -> bool:
        name = Path(rel).name
        return name in {"__init__.py", "__main__.py", "conftest.py", "setup.py", "manage.py", "wsgi.py", "asgi.py", "settings.py", "urls.py", "app.py", "main.py", "cli.py", "server.py"}

    # ---------------------------------------------------------------- AST hooks
    def body_statements(self, def_node: Node, sf: SourceFile) -> list[Node]:
        body = def_node.child_by_field_name("body")
        if body is None:
            return []
        stmts = [c for c in body.named_children if c.type != "comment"]
        if stmts and stmts[0].type == "expression_statement" and stmts[0].named_child_count == 1 and stmts[0].named_children[0].type == "string":
            stmts = stmts[1:]
        return stmts

    def statement_call(self, stmt: Node, sf: SourceFile) -> Node | None:
        node = stmt
        if node.type == "return_statement":
            if node.named_child_count != 1:
                return None
            node = node.named_children[0]
        elif node.type == "expression_statement":
            if node.named_child_count != 1:
                return None
            node = node.named_children[0]
        else:
            return None
        if node.type == "await":
            node = node.named_children[0] if node.named_child_count else node
        return node if node.type == "call" else None

    def call_parts(self, call: Node, sf: SourceFile) -> tuple[tuple[str, ...] | None, list[str]]:
        ex = _Extractor(self, sf, FileFacts(file=sf, module_name=""))
        fn = call.child_by_field_name("function")
        args = call.child_by_field_name("arguments")
        chain = ex.loose_chain_of(fn) if fn is not None else None
        texts: list[str] = []
        if args is not None:
            for a in args.named_children:
                if a.type == "keyword_argument":
                    v = a.child_by_field_name("value")
                    texts.append(sf.text(v) if v is not None else "")
                elif a.type in ("list_splat", "dictionary_splat"):
                    texts.append(sf.text(a).lstrip("*"))
                else:
                    texts.append(sf.text(a))
        return chain, texts

    def except_clauses(self, def_node: Node, sf: SourceFile) -> list[tuple[Node, list[Node], bool]]:
        from vibepiler.frontend.parser import named_descendants

        out = []
        for n in named_descendants(def_node):
            if n.type != "except_clause":
                continue
            if any(a.type in self.function_types and a != def_node for a in _ancestors(n, 50)):
                continue
            block = next((c for c in n.named_children if c.type == "block"), None)
            stmts = [c for c in block.named_children if c.type != "comment"] if block is not None else []
            typ = next((c for c in n.named_children if c.type != "block"), None)
            txt = sf.text(typ) if typ is not None else ""
            broad = typ is None or txt.split(" as ")[0].strip() in {"Exception", "BaseException", "object"}
            out.append((n, stmts, broad))
        return out

    def is_stub_body(self, stmts: list[Node], sf: SourceFile) -> bool:
        if not stmts or len(stmts) > 1:
            return False
        s = stmts[0]
        if s.type == "pass_statement":
            return True
        if s.type == "expression_statement" and s.named_child_count == 1 and s.named_children[0].type == "ellipsis":
            return True
        if s.type == "raise_statement" and "NotImplementedError" in sf.text(s):
            return True
        if s.type == "return_statement" and s.named_child_count == 0:
            return True
        return False

    # ---------------------------------------------------------------- extract
    def extractor(self, sf: SourceFile, facts: FileFacts) -> "_Extractor":
        return _Extractor(self, sf, facts)


class _Extractor:
    def __init__(self, spec: PythonSpec, sf: SourceFile, facts: FileFacts) -> None:
        self.spec = spec
        self.sf = sf
        self.facts = facts
        self.all_names: list[str] | None = None

    def text(self, node: Node) -> str:
        return self.sf.text(node)

    # --- chains ---------------------------------------------------------------
    def chain_of(self, node: Node) -> tuple[str, ...] | None:
        """identifier / attribute chain -> ('a','b','c'); None if not a pure chain."""
        parts: list[str] = []
        cur = node
        while True:
            if cur.type == "identifier":
                parts.append(self.text(cur))
                break
            if cur.type == "attribute":
                attr = cur.child_by_field_name("attribute")
                obj = cur.child_by_field_name("object")
                if attr is None or obj is None:
                    return None
                parts.append(self.text(attr))
                cur = obj
                continue
            return None
        parts.reverse()
        return tuple(parts)

    def loose_chain_of(self, node: Node) -> tuple[str, ...] | None:
        """Like chain_of but tolerates an unknown receiver: foo().bar -> ('*','bar')."""
        c = self.chain_of(node)
        if c is not None:
            return c
        if node.type == "attribute":
            attr = node.child_by_field_name("attribute")
            obj = node.child_by_field_name("object")
            if attr is not None and obj is not None:
                inner = self.loose_chain_of(obj)
                if inner is not None:
                    return inner + (self.text(attr),)
                return ("*", self.text(attr))
        return None

    # --- visiting -------------------------------------------------------------
    def visit(self, node: Node, scope: str | None, class_scope: str | None, decorators: list[Node] | None = None) -> None:
        t = node.type
        if t == "decorated_definition":
            decs = [c for c in node.children if c.type == "decorator"]
            for d in decs:
                self.visit_decorator(d, scope)
            definition = node.child_by_field_name("definition")
            if definition is not None:
                self.visit(definition, scope, class_scope, decorators=decs)
            return
        if t == "function_definition":
            self.visit_function(node, scope, class_scope, decorators or [])
            return
        if t == "class_definition":
            self.visit_class(node, scope, decorators or [])
            return
        if t == "import_statement":
            self.visit_import(node, scope)
            return
        if t == "import_from_statement":
            self.visit_import_from(node, scope)
            return
        if t == "call":
            self.visit_call(node, scope, class_scope)
            return
        if t == "attribute":
            chain = self.chain_of(node)
            if chain is not None:
                self.facts.refs.append(RefSite(chain=chain, line=self.spec.line(node), scope=scope))
                return
            obj = node.child_by_field_name("object")
            if obj is not None:
                self.visit(obj, scope, class_scope)
            return
        if t == "identifier":
            self.visit_identifier(node, scope)
            return
        if t == "if_statement" and scope is None and self.is_main_guard(node):
            self.facts.has_main_guard = True
            self.facts.entry_hints.append(EntryHint(kind="main_guard", qualname=None, line=self.spec.line(node)))
        if t in ("assignment", "augmented_assignment") and scope is None:
            self.visit_module_assignment(node)
            right = node.child_by_field_name("right")
            if right is not None:
                self.visit(right, scope, class_scope)
            typ = node.child_by_field_name("type")
            if typ is not None:
                self.visit(typ, scope, class_scope)
            return
        for child in node.children:
            if child.is_named or child.type in ("else", "elif"):
                self.visit(child, scope, class_scope)

    def visit_identifier(self, node: Node, scope: str | None) -> None:
        parent = node.parent
        if parent is None:
            return
        rule = _SKIP_IDENT_PARENTS.get(parent.type, "__absent__")
        if rule is None:
            return
        if rule != "__absent__" and parent.child_by_field_name(rule) == node:
            return
        if parent.type == "assignment" and parent.child_by_field_name("left") == node:
            return
        if parent.type in ("pattern_list", "tuple_pattern", "list_pattern") or (
            parent.type == "for_statement" and parent.child_by_field_name("left") == node
        ):
            return
        name = self.text(node)
        ctx = "type" if any(a.type == "type" for a in _ancestors(node, 3)) else "name"
        self.facts.refs.append(RefSite(chain=(name,), line=self.spec.line(node), scope=scope, context=ctx))

    def visit_decorator(self, node: Node, scope: str | None) -> None:
        expr = next((c for c in node.children if c.is_named), None)
        if expr is None:
            return
        target = expr.child_by_field_name("function") if expr.type == "call" else expr
        chain = self.loose_chain_of(target) if target is not None else None
        if chain is not None:
            self.facts.refs.append(RefSite(chain=chain, line=self.spec.line(node), scope=scope, context="decorator"))
        if expr.type == "call":
            args = expr.child_by_field_name("arguments")
            if args is not None:
                self.visit(args, scope, None)

    def visit_function(self, node: Node, scope: str | None, class_scope: str | None, decorators: list[Node]) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.text(name_node)
        qualname = f"{scope}.{name}" if scope else name
        kind = NodeKind.METHOD if (class_scope is not None and scope == class_scope) else NodeKind.FUNCTION
        params_node = node.child_by_field_name("parameters")
        params = self.param_names(params_node) if params_node is not None else []
        dec_names = [self.decorator_name(d) for d in decorators]
        is_async = any(c.type == "async" for c in node.children)
        d = SymbolDef(
            kind=kind,
            name=name,
            qualname=qualname,
            start_line=self.spec.line(decorators[0] if decorators else node),
            end_line=self.spec.end_line(node),
            node=node,
            parent_qualname=scope,
            decorators=dec_names,
            params=params,
            exported=not name.startswith("_") and scope is None,
            is_async=is_async,
        )
        self.facts.defs.append(d)
        self.add_entry_hints(d)
        # defaults & annotations in params are references (only the parameter's own name is not)
        if params_node is not None:
            for p in params_node.named_children:
                own = p.child_by_field_name("name")
                for c in p.named_children:
                    if c is not own:
                        self.visit(c, scope, class_scope)
        ret = node.child_by_field_name("return_type")
        if ret is not None:
            self.visit(ret, scope, class_scope)
        body = node.child_by_field_name("body")
        if body is not None:
            self.visit(body, qualname, class_scope)

    def visit_class(self, node: Node, scope: str | None, decorators: list[Node]) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.text(name_node)
        qualname = f"{scope}.{name}" if scope else name
        bases: list[str] = []
        sup = node.child_by_field_name("superclasses")
        if sup is not None:
            for arg in sup.named_children:
                if arg.type == "keyword_argument":
                    continue  # metaclass=...
                chain = self.loose_chain_of(arg)
                if chain is not None:
                    bases.append(".".join(chain))
                    self.facts.refs.append(RefSite(chain=chain, line=self.spec.line(arg), scope=scope, context="base"))
        d = SymbolDef(
            kind=NodeKind.CLASS,
            name=name,
            qualname=qualname,
            start_line=self.spec.line(decorators[0] if decorators else node),
            end_line=self.spec.end_line(node),
            node=node,
            parent_qualname=scope,
            decorators=[self.decorator_name(x) for x in decorators],
            bases=bases,
            exported=not name.startswith("_") and scope is None,
        )
        self.facts.defs.append(d)
        self.add_entry_hints(d)
        body = node.child_by_field_name("body")
        if body is not None:
            self.visit(body, qualname, qualname)

    def visit_module_assignment(self, node: Node) -> None:
        left = node.child_by_field_name("left")
        if left is None:
            return
        targets = [left] if left.type == "identifier" else [c for c in left.named_children if c.type == "identifier"] if left.type in ("pattern_list", "tuple_pattern") else []
        for tnode in targets:
            name = self.text(tnode)
            if name == "__all__":
                self.all_names = self.string_list(node.child_by_field_name("right"))
            if any(d.qualname == name for d in self.facts.defs):
                continue
            self.facts.defs.append(
                SymbolDef(
                    kind=NodeKind.VARIABLE,
                    name=name,
                    qualname=name,
                    start_line=self.spec.line(node),
                    end_line=self.spec.end_line(node),
                    node=node,
                    exported=not name.startswith("_"),
                )
            )

    def visit_import(self, node: Node, scope: str | None) -> None:
        for c in node.named_children:
            if c.type == "dotted_name":
                self.facts.imports.append(ImportDef(module=self.text(c), line=self.spec.line(node), kind="module", scope=scope))
            elif c.type == "aliased_import":
                nm = c.child_by_field_name("name")
                al = c.child_by_field_name("alias")
                if nm is not None:
                    self.facts.imports.append(
                        ImportDef(module=self.text(nm), alias=self.text(al) if al else None, line=self.spec.line(node), kind="module", scope=scope)
                    )

    def visit_import_from(self, node: Node, scope: str | None) -> None:
        mod_node = node.child_by_field_name("module_name")
        level = 0
        module = ""
        if mod_node is not None:
            if mod_node.type == "relative_import":
                for c in mod_node.children:
                    if c.type == "import_prefix":
                        level = self.text(c).count(".")
                    elif c.type == "dotted_name":
                        module = self.text(c)
            else:
                module = self.text(mod_node)
        line = self.spec.line(node)
        names = node.children_by_field_name("name")
        if not names:
            for c in node.named_children:
                if c.type == "wildcard_import":
                    self.facts.imports.append(ImportDef(module=module, level=level, line=line, kind="wildcard", scope=scope))
            return
        for n in names:
            if n.type == "dotted_name":
                self.facts.imports.append(ImportDef(module=module, name=self.text(n), level=level, line=line, kind="member", scope=scope))
            elif n.type == "aliased_import":
                nm = n.child_by_field_name("name")
                al = n.child_by_field_name("alias")
                if nm is not None:
                    self.facts.imports.append(
                        ImportDef(module=module, name=self.text(nm), alias=self.text(al) if al else None, level=level, line=line, kind="member", scope=scope)
                    )

    def visit_call(self, node: Node, scope: str | None, class_scope: str | None) -> None:
        fn = node.child_by_field_name("function")
        args = node.child_by_field_name("arguments")
        nargs = len(args.named_children) if args is not None else 0
        if fn is not None:
            chain = self.loose_chain_of(fn)
            if chain is not None:
                self.facts.calls.append(CallSite(chain=chain, line=self.spec.line(node), scope=scope, args=nargs))
                # nested receivers like foo().bar(): visit the inner expression too
                if chain[0] == "*":
                    inner = fn
                    while inner is not None and inner.type == "attribute":
                        inner = inner.child_by_field_name("object")
                    if inner is not None:
                        self.visit(inner, scope, class_scope)
            else:
                self.visit(fn, scope, class_scope)
        if args is not None:
            self.visit(args, scope, class_scope)
        # generator/comprehension arguments etc. are handled by the generic visit above

    # --- helpers --------------------------------------------------------------
    def is_main_guard(self, node: Node) -> bool:
        cond = node.child_by_field_name("condition")
        if cond is None:
            return False
        txt = self.text(cond).replace(" ", "").replace('"', "'")
        return txt in ("__name__=='__main__'", "'__main__'==__name__")

    def param_names(self, params: Node) -> list[str]:
        out: list[str] = []
        for p in params.named_children:
            if p.type == "identifier":
                out.append(self.text(p))
            elif p.type in ("default_parameter", "typed_parameter", "typed_default_parameter", "list_splat_pattern", "dictionary_splat_pattern"):
                nm = p.child_by_field_name("name") or next((c for c in p.named_children if c.type == "identifier"), None)
                if nm is not None:
                    out.append(self.text(nm))
        return out

    def decorator_name(self, dec: Node) -> str:
        expr = next((c for c in dec.children if c.is_named), None)
        if expr is None:
            return ""
        target = expr.child_by_field_name("function") if expr.type == "call" else expr
        chain = self.loose_chain_of(target) if target is not None else None
        return ".".join(chain) if chain else self.text(expr)

    def string_list(self, node: Node | None) -> list[str] | None:
        if node is None or node.type not in ("list", "tuple"):
            return None
        out = []
        for c in node.named_children:
            if c.type == "string":
                out.append(self.text(c).strip("\"'"))
        return out

    def add_entry_hints(self, d: SymbolDef) -> None:
        line = d.start_line
        for dec in d.decorators:
            last = dec.split(".")[-1]
            if last in ROUTE_DECORATORS:
                self.facts.entry_hints.append(EntryHint("route", d.qualname, line, dec))
            elif last in COMMAND_DECORATORS:
                self.facts.entry_hints.append(EntryHint("command", d.qualname, line, dec))
            elif last in TASK_DECORATORS:
                self.facts.entry_hints.append(EntryHint("task", d.qualname, line, dec))
            elif last in HOOK_DECORATORS:
                self.facts.entry_hints.append(EntryHint("hook", d.qualname, line, dec))
        if self.facts.is_test and (d.name.startswith("test") or d.name.startswith("Test")):
            self.facts.entry_hints.append(EntryHint("test", d.qualname, line))
        if d.parent_qualname is None and d.name in ENTRY_FUNCTION_NAMES and d.kind is NodeKind.FUNCTION:
            self.facts.entry_hints.append(EntryHint("main", d.qualname, line))
        if d.name.startswith("__") and d.name.endswith("__"):
            self.facts.entry_hints.append(EntryHint("dunder", d.qualname, line))

    def finish(self) -> None:
        if self.all_names is not None:
            self.facts.attrs["__all__"] = self.all_names
            allowed = set(self.all_names)
            for d in self.facts.defs:
                if d.parent_qualname is None:
                    d.exported = d.name in allowed or d.name == "__all__"


def _ancestors(node: Node, depth: int):
    cur = node.parent
    for _ in range(depth):
        if cur is None:
            return
        yield cur
        cur = cur.parent
