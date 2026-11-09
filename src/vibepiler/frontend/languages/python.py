"""Python language adapter (tree-sitter-python grammar)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from tree_sitter import Node

from vibepiler.frontend.facts import CallSite, EntryHint, FileFacts, ImportDef, RefSite, SymbolDef, TypeHint
from vibepiler.frontend.languages.base import DecoratorWalk, ImportOption, LanguageSpec
from vibepiler.frontend.languages.python_flow import PythonFlow
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
MIGRATION_DIRS = {"migrations", "alembic"}


def is_migration_file(rel: str) -> bool:
    """Alembic and Django migrations: the framework loads them by path, so nothing imports them and none of them is dead."""
    return bool(MIGRATION_DIRS & set(Path(rel).parts[:-1]))  # alembic/env.py and alembic/versions/*, */migrations/*


def is_framework_loaded(rel: str) -> bool:
    """Django loads `templatetags/*.py` for `{% load name %}` and `management/commands/*.py` for `manage.py name`."""
    dirs = Path(rel).parts[:-1]
    return "templatetags" in dirs or any(a == "management" and b == "commands" for a, b in zip(dirs, dirs[1:]))


DYNAMIC_IMPORTS = {"import_module", "__import__", "importorskip"}  # importlib.import_module("a.b"), __import__("a.b"), pytest.importorskip("a")

ENTRY_FUNCTION_NAMES = {"main", "handler", "lambda_handler", "run", "serve", "cli", "app", "application"}

# identifiers that should never be treated as references
# a string annotation is a type expression the parser saw as a string: lex its dotted names, nested quotes left out
_ANNOTATION_NAME = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
_NESTED_STRING = re.compile(r"'[^']*'|\"[^\"]*\"")
_UNWRAP = {"Optional", "Annotated", "Final", "ClassVar"}  # `Optional[X]`, `Annotated[X, ...]`: still an X (or None)

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
}  # `except E as e`: the `e` sits under as_pattern_target, skipped above; the `E` is a reference


class PythonSpec(PythonFlow, LanguageSpec):
    name = "python"
    branch_types = frozenset({
        "if_statement", "elif_clause", "for_statement", "while_statement", "except_clause",
        "boolean_operator", "conditional_expression", "for_in_clause", "if_clause", "case_clause", "assert_statement",
    })  # radon's rules: `with` and `match` add nothing, each `case` does
    loop_else_parents = frozenset({"for_statement", "while_statement", "try_statement"})
    opaque_branch_types = frozenset({"assert_statement"})  # one check, whatever its condition holds
    cog_structures = frozenset({"if_statement", "for_statement", "while_statement", "except_clause", "conditional_expression", "match_statement", "for_in_clause"})
    cog_flat = frozenset({"elif_clause", "if_clause"})
    lambda_types = frozenset({"lambda"})
    comprehension_types = frozenset({"list_comprehension", "set_comprehension", "dictionary_comprehension", "generator_expression"})
    call_types = frozenset({"call"})
    nesting_types = frozenset({
        "if_statement", "for_statement", "while_statement", "try_statement", "with_statement",
        "function_definition", "class_definition", "match_statement",
    })
    leaf_types = frozenset({"identifier", "string", "string_content", "integer", "float", "true", "false", "none"})
    function_types = frozenset({"function_definition"})

    def is_branch(self, node: Node, sf: SourceFile) -> bool:
        """A loop's or a try's `else` is a path of its own; an `if`'s `else` and a `case _:` are not."""
        if node.type == "else_clause":
            return node.parent is not None and node.parent.type in self.loop_else_parents
        if node.type == "if_clause" and node.parent is not None and node.parent.type == "case_clause":
            return False  # a case guard: radon counts the case, not its guard
        if node.type == "case_clause":
            pattern = next((c for c in node.children if c.type == "case_pattern"), None)
            return not (pattern is not None and [c.type for c in pattern.children] == ["_"])  # `case _:` is the else
        return node.type in self.branch_types

    def bool_op(self, node: Node, sf: SourceFile) -> str | None:
        op = node.child_by_field_name("operator") if node.type == "boolean_operator" else None
        return sf.text(op) if op is not None else None

    def annotation_slots(self, def_node: Node, sf: SourceFile) -> tuple[int, int]:
        params = def_node.child_by_field_name("parameters")
        typed = slots = 0
        for i, p in enumerate(params.named_children if params is not None else []):
            if p.type in ("keyword_separator", "positional_separator", "comment"):
                continue
            if i == 0 and p.type == "identifier" and sf.text(p) in self.self_names:
                continue
            slots += 1
            typed += p.type in ("typed_parameter", "typed_default_parameter")
        name = def_node.child_by_field_name("name")
        if name is None or sf.text(name) != "__init__":
            slots += 1
            typed += def_node.child_by_field_name("return_type") is not None
        return typed, slots

    def documented(self, def_node: Node, sf: SourceFile) -> bool:
        return _starts_with_string(def_node.child_by_field_name("body"))

    def module_documented(self, sf: SourceFile) -> bool:
        return _starts_with_string(sf.tree.root_node)

    # ------------------------------------------------------------------ naming
    def module_name(self, sf: SourceFile) -> tuple[str, list[str]]:
        rel = Path(sf.rel)
        stem = rel.stem
        dirs = list(rel.parent.parts)
        # walk up while the directory is a regular package
        pkg_dirs: list[str] = []
        d = sf.path.parent
        while d != sf.root.parent and (d / "__init__.py").exists():
            if d == sf.root and not d.name.isidentifier():
                break  # `my-repo/` or a clone's cache folder cannot be imported as a package, whatever its __init__.py says
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
    stdlib_modules = frozenset(sys.stdlib_module_names)

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
        return is_migration_file(rel) or is_framework_loaded(rel) or name in {"__init__.py", "__main__.py", "conftest.py", "setup.py", "manage.py", "wsgi.py", "asgi.py", "settings.py", "urls.py", "app.py", "main.py", "cli.py", "server.py"}

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
        if s.type in ("pass_statement", "ellipsis"):  # this grammar puts a bare `...` straight in the block
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


class _Extractor(DecoratorWalk):
    call_type = "call"

    def __init__(self, spec: PythonSpec, sf: SourceFile, facts: FileFacts) -> None:
        self.spec = spec
        self.sf = sf
        self.facts = facts
        self.all_names: list[str] | None = None
        self.built: dict[tuple[str, str], tuple[str, ...]] = {}  # (scope, local) -> constructor chain it was last assigned from

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
        """One node: its own handler (see VISITORS), then its children unless the handler took the whole subtree."""
        handler = self.VISITORS.get(node.type)
        if handler is not None and not handler(self, node, scope, class_scope, decorators):
            return
        for child in node.children:
            if child.is_named or child.type in ("else", "elif"):
                self.visit(child, scope, class_scope)

    def on_decorated(self, node: Node, scope: str | None, class_scope: str | None, _decorators: list[Node] | None) -> None:
        decs = [c for c in node.children if c.type == "decorator"]
        for d in decs:
            self.visit_decorator(d, scope)
        if (definition := node.child_by_field_name("definition")) is not None:
            self.visit(definition, scope, class_scope, decorators=decs)

    def on_attribute(self, node: Node, scope: str | None, class_scope: str | None, _decorators: list[Node] | None) -> None:
        """`a.b.c` is one reference (a store when assigned to); `f().x` visits the call."""
        chain = self.chain_of(node)
        if chain is None:
            if (obj := node.child_by_field_name("object")) is not None:
                self.visit(obj, scope, class_scope)
            return
        p = node.parent
        store = p is not None and p.type == "assignment" and p.child_by_field_name("left") == node
        self.facts.refs.append(RefSite(chain=chain, line=self.spec.line(node), scope=scope, context="store" if store else "name"))

    def on_string(self, node: Node, scope: str | None, _class_scope: str | None, _decorators: list[Node] | None) -> bool:
        if self.in_annotation(node):
            self.visit_string_annotation(node, scope)
            return False
        if name := self.module_string(node):
            # `compiler_module = "db.mysql"`, INSTALLED_APPS: a module the program may load by its name (kept only if it is ours)
            self.facts.imports.append(ImportDef(module=name, line=self.spec.line(node), kind="by_name", scope=scope))
        return True

    def on_if(self, node: Node, scope: str | None, class_scope: str | None, _decorators: list[Node] | None) -> bool:
        if self.is_type_checking_guard(node):
            self.visit_type_checking(node, scope, class_scope)
            return False
        if scope is None and self.is_main_guard(node):
            self.facts.has_main_guard = True
            self.facts.entry_hints.append(EntryHint(kind="main_guard", qualname=None, line=self.spec.line(node)))
        return True

    def on_assignment(self, node: Node, scope: str | None, class_scope: str | None, _decorators: list[Node] | None) -> bool:
        """What the assigned name holds; at module level, the variable it defines (its target is not a reference)."""
        if node.type == "assignment":
            self.note_types(node, scope, class_scope)
            if scope is not None:
                self.note_built(node, scope)
        if scope is not None:
            return True
        self.visit_module_assignment(node)
        for part in (node.child_by_field_name("right"), node.child_by_field_name("type")):
            if part is not None:
                self.visit(part, scope, class_scope)
        return False

    def on_rebinding(self, node: Node, scope: str | None, class_scope: str | None, _decorators: list[Node] | None) -> bool:
        if scope is not None and scope != class_scope:
            self.note_rebound(node, scope)
        return True

    # node type -> handler(self, node, scope, class_scope, decorators); a true result means: visit the children too
    VISITORS = {
        "decorated_definition": on_decorated,
        "function_definition": lambda self, n, scope, cls, decs: self.visit_function(n, scope, cls, decs or []),
        "class_definition": lambda self, n, scope, cls, decs: self.visit_class(n, scope, decs or []),
        "import_statement": lambda self, n, scope, cls, decs: self.visit_import(n, scope),
        "import_from_statement": lambda self, n, scope, cls, decs: self.visit_import_from(n, scope),
        "call": lambda self, n, scope, cls, decs: self.visit_call(n, scope, cls),
        "attribute": on_attribute,
        "identifier": lambda self, n, scope, cls, decs: self.visit_identifier(n, scope),
        "string": on_string,
        "if_statement": on_if,
        "assignment": on_assignment,
        "augmented_assignment": on_assignment,
        "for_statement": on_rebinding,
        "with_statement": on_rebinding,
    }

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

    def visit_type_checking(self, node: Node, scope: str | None, class_scope: str | None) -> None:
        """`if TYPE_CHECKING:`: the body is for type checkers, so its imports bind names but never run; `else` runs."""
        self.visit(node.child_by_field_name("condition"), scope, class_scope)
        start = len(self.facts.imports)
        self.visit(node.child_by_field_name("consequence"), scope, class_scope)
        for imp in self.facts.imports[start:]:
            imp.type_only = True
        for alt in node.children_by_field_name("alternative"):
            self.visit(alt, scope, class_scope)

    @staticmethod
    def in_annotation(node: Node) -> bool:
        return any(a.type == "type" for a in _ancestors(node, 6))

    def visit_string_annotation(self, node: Node, scope: str | None) -> None:
        """`p: "Path"`, `-> "a.B | None"`: forward references read by type checkers, so their names are used."""
        content = "".join(self.text(c) for c in node.children if c.type == "string_content")
        for m in _ANNOTATION_NAME.finditer(_NESTED_STRING.sub("", content)):
            self.facts.refs.append(RefSite(chain=tuple(m.group().split(".")), line=self.spec.line(node), scope=scope, context="type"))

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
        ret = node.child_by_field_name("return_type")
        if ret is not None and (returns := self.type_chain(ret)) is not None:
            d.attrs["returns"] = ".".join(returns)
        self.facts.defs.append(d)
        self.add_entry_hints(d)
        if params_node is not None:
            self.note_param_types(params_node, qualname, kind)
        # defaults & annotations in params are references (only the parameter's own name is not)
        if params_node is not None:
            for p in params_node.named_children:
                own = p.child_by_field_name("name")
                for c in p.named_children:
                    if c is not own:
                        self.visit(c, scope, class_scope)
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
                if arg.type == "subscript":  # `Base[T]`: the base is the subscripted value, its arguments are references
                    for sub in arg.children_by_field_name("subscript"):
                        self.visit(sub, scope, None)
                    arg = arg.child_by_field_name("value") or arg
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
                if chain == ("getattr",) and (prefix := self.dispatch_prefix(args)):
                    self.facts.dispatches.append((scope, prefix))
                if chain[-1] in DYNAMIC_IMPORTS and (target := self.literal_module(args)):
                    # import_module("a.b") / __import__("a.b") with a literal name is an import the program makes at run time
                    self.facts.imports.append(ImportDef(module=target, line=self.spec.line(node), kind="side_effect", scope=scope))
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
            if scope is not None and fn is not None:
                self.note_escapes(fn, args, scope, node)
            self.visit(args, scope, class_scope)
        # generator/comprehension arguments etc. are handled by the generic visit above

    # --- types ----------------------------------------------------------------
    def type_chain(self, node: Node) -> tuple[str, ...] | None:
        """The one class an annotation names: `Foo`, `m.Foo`, `Foo | None`, `Optional[Foo]`, `"Foo"`. None for
        anything else (`list[Foo]`, `A | B`, `Callable[...]`): a container or a choice is not an instance of one class."""
        if node.type == "type":
            node = node.named_children[0] if node.named_children else node
        if node.type in ("identifier", "attribute"):
            return self.chain_of(node)
        if node.type == "generic_type":
            head = node.named_children[0] if node.named_children else None
            params = next((c for c in node.named_children if c.type == "type_parameter"), None)
            if head is None or params is None or self.text(head) not in _UNWRAP or not params.named_children:
                return None
            return self.type_chain(params.named_children[0])
        if node.type == "binary_operator":
            parts = [p for p in self._union_parts(node) if not (p.type == "none" or (p.type == "identifier" and self.text(p) == "None"))]
            return self.type_chain(parts[0]) if len(parts) == 1 else None
        if node.type == "string":
            return _string_type("".join(self.text(c) for c in node.children if c.type == "string_content"))
        return None

    def _union_parts(self, node: Node) -> list[Node]:
        if node.type == "binary_operator" and (op := node.child_by_field_name("operator")) is not None and self.text(op) == "|":
            return self._union_parts(node.child_by_field_name("left")) + self._union_parts(node.child_by_field_name("right"))
        return [node]

    def note_param_types(self, params: Node, qualname: str, kind: NodeKind) -> None:
        """`def f(db: Database)`: inside f, `db` holds a Database. A method's first parameter is `self`/`cls`, typed already."""
        for i, p in enumerate(params.named_children):
            if p.type not in ("typed_parameter", "typed_default_parameter"):
                continue
            name = p.child_by_field_name("name") or (p.named_children[0] if p.named_children else None)
            typ = p.child_by_field_name("type")
            if name is None or name.type != "identifier" or typ is None:
                continue  # `*args: T` is a tuple of T, `**kw: T` a dict
            if i == 0 and kind is NodeKind.METHOD and self.text(name) in self.spec.self_names:
                continue
            self.facts.types.append(TypeHint(scope=qualname, name=self.text(name), chain=self.type_chain(typ) or ()))

    def note_types(self, node: Node, scope: str | None, class_scope: str | None) -> None:
        """What an assignment tells about the name it binds: its annotation, else the call it holds the result of.
        A value we cannot type is recorded too (an empty chain), so the name is not trusted; `= None` says nothing."""
        left, typ, right = node.child_by_field_name("left"), node.child_by_field_name("type"), node.child_by_field_name("right")
        if left is None:
            return
        if right is not None and right.type == "await":
            right = right.named_children[0] if right.named_children else right
        if typ is not None:
            chain, source = self.type_chain(typ) or (), "annotation"
        elif right is not None and right.type == "none":
            return
        elif right is not None and right.type == "call" and (fn := self.chain_of(right.child_by_field_name("function"))) is not None:
            chain, source = fn, "call"
        elif right is not None and (copied := self.chain_of(right)) is not None:
            chain, source = copied, "name"  # `self.db = db`
        else:
            chain, source = (), "annotation"
        if left.type == "identifier":
            in_class_body = class_scope is not None and scope == class_scope
            self.facts.types.append(TypeHint(scope=scope, name=self.text(left), chain=chain, source=source, of_class=class_scope if in_class_body else None))
        elif left.type == "attribute" and class_scope is not None and scope != class_scope:
            obj, attr = left.child_by_field_name("object"), left.child_by_field_name("attribute")
            if obj is not None and attr is not None and obj.type == "identifier" and self.text(obj) in self.spec.self_names:
                self.facts.types.append(TypeHint(scope=scope, name=self.text(attr), chain=chain, source=source, of_class=class_scope))
        elif left.type in ("pattern_list", "tuple_pattern", "list_pattern") and scope is not None:
            for c in left.named_children:
                if c.type == "identifier":
                    self.facts.types.append(TypeHint(scope=scope, name=self.text(c), chain=()))

    def note_rebound(self, node: Node, scope: str) -> None:
        """`for x in ...` and `with ... as x` bind x to something we do not type: x holds no one known class."""
        if node.type == "for_statement":
            targets = [node.child_by_field_name("left")]
        else:
            targets = [n for n in _descendants(node, frozenset({"block"})) if n.type == "as_pattern_target"]
        for t in targets:
            if t is None:
                continue
            for ident in [t] if t.type == "identifier" else [c for c in _descendants(t) if c.type == "identifier"]:
                self.facts.types.append(TypeHint(scope=scope, name=self.text(ident), chain=()))

    # --- escape analysis ------------------------------------------------------
    def note_built(self, node: Node, scope: str) -> None:
        """`res = MockResponse(r)`: remember which constructor a local was built from, for the calls after it."""
        left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
        if left is not None and left.type == "identifier" and right is not None:
            ctor = self.chain_of(right.child_by_field_name("function")) if right.type == "call" else None
            if ctor is not None:
                self.built[(scope, self.text(left))] = ctor
            else:
                self.built.pop((scope, self.text(left)), None)

    def note_escapes(self, fn: Node, args: Node, scope: str, call: Node) -> None:
        """Objects a function builds and hands to a call: `jar.extract_cookies(res)` or `register(Handler())`. Recorded
        as (callee, receiver's constructor, constructor) chains; the linker decides which are our classes."""
        callee = None
        for a in args.named_children:
            a = a.child_by_field_name("value") if a.type == "keyword_argument" else a
            if a is None:
                continue
            ctor = self.built.get((scope, self.text(a))) if a.type == "identifier" else (
                self.chain_of(a.child_by_field_name("function")) if a.type == "call" else None)
            if ctor is None:
                continue
            callee = callee or self.loose_chain_of(fn)
            if callee is None:
                return
            receiver = self.built.get((scope, callee[0]), ()) if len(callee) > 1 else ()  # `g = Geometry(); g.transform(obj)`
            self.facts.escapes.append((scope, callee, receiver, ctor, self.spec.line(call)))

    # --- helpers --------------------------------------------------------------
    def dispatch_prefix(self, args: Node | None) -> str | None:
        """`getattr(self, f"visit_{t}")` or `getattr(self, "do_" + cmd)`: the literal prefix of a method name looked up
        on the instance itself; None for anything else."""
        named = [a for a in args.named_children if a.type != "comment"] if args is not None else []
        if len(named) < 2 or named[0].type != "identifier" or self.text(named[0]) not in self.spec.self_names:
            return None
        name = named[1]
        if name.type == "binary_operator" and name.child_by_field_name("operator") is not None and self.text(name.child_by_field_name("operator")) == "+":
            name = name.child_by_field_name("left")
            if name is None or name.type != "string" or any(c.type == "interpolation" for c in name.children):
                return None
            prefix = "".join(self.text(c) for c in name.children if c.type == "string_content")
        elif name.type == "string" and any(c.type == "interpolation" for c in name.children):
            first = next((c for c in name.children if c.type in ("string_content", "interpolation")), None)
            prefix = self.text(first) if first is not None and first.type == "string_content" else ""
        else:
            return None
        return prefix if len(prefix) >= 2 and prefix.isidentifier() else None

    def literal_module(self, args: Node | None) -> str | None:
        """The first argument when it is a plain absolute module name in a string literal (no f-string, no relative dots)."""
        first = args.named_children[0] if args is not None and args.named_children else None
        return self.module_literal(first) if first is not None else None

    def module_string(self, node: Node) -> str | None:
        """A dotted module name (`"a.b"`, at least one dot) as a whole string literal, not a docstring."""
        if node.parent is not None and node.parent.type in ("expression_statement", "module", "block"):
            return None  # a docstring, or a bare string statement
        name = self.module_literal(node)
        return name if name is not None and "." in name else None

    def module_literal(self, first: Node) -> str | None:
        if first.type != "string" or any(c.type == "interpolation" for c in first.children):
            return None
        text = self.text(first)
        if not text[:1] in ("'", '"'):
            return None  # a prefixed literal: f"", b"", r""
        name = text.strip("\"'")
        return name if re.fullmatch(r"[A-Za-z_][\w]*(\.[A-Za-z_][\w]*)*", name) else None

    def is_type_checking_guard(self, node: Node) -> bool:
        cond = node.child_by_field_name("condition")
        return cond is not None and self.text(cond) in ("TYPE_CHECKING", "typing.TYPE_CHECKING")

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

    def runs_code(self) -> bool:
        """Does importing or running this file call something at the top level (`print(...)`, `app = create_app()`)?
        A docstring, constants and definitions do not count; nor does anything under `if`, `__main__` guard included."""
        for stmt in self.sf.tree.root_node.named_children:
            # the grammar gives a top-level call or assignment bare or wrapped in an expression_statement
            if stmt.type in ("expression_statement", "call", "assignment", "await") and (stmt.type == "call" or any(n.type == "call" for n in _descendants(stmt))):
                return True
        return False

    def finish(self) -> None:
        if self.runs_code():
            self.facts.attrs["runs_code"] = True
        if is_framework_loaded(self.facts.rel):  # Django reads `register`, the tags it registers and `Command` by name
            for d in self.facts.defs:
                if d.parent_qualname is None and not d.name.startswith("_"):
                    self.facts.entry_hints.append(EntryHint("framework", d.qualname, d.start_line))
        if is_migration_file(self.facts.rel):
            self.facts.attrs["is_migration"] = True
            for d in self.facts.defs:
                if d.parent_qualname is None:
                    self.facts.entry_hints.append(EntryHint("migration", d.qualname, d.start_line))
        if self.all_names is not None:
            self.facts.attrs["__all__"] = self.all_names
            allowed = set(self.all_names)
            for d in self.facts.defs:
                if d.parent_qualname is None:
                    d.exported = d.name in allowed or d.name == "__all__"


def _string_type(text: str) -> tuple[str, ...] | None:
    """A string annotation's one class: `"Foo"`, `"m.Foo | None"`, `"Optional[Foo]"`."""
    parts = [p.strip() for p in text.split("|") if p.strip() != "None"]
    if len(parts) != 1:
        return None
    t = parts[0]
    if (m := re.fullmatch(r"(?:typing\.)?(?:Optional|Annotated|Final)\[([^,\]]+)[^\]]*\]", t)) is not None:
        t = m.group(1).strip()
    return tuple(t.split(".")) if _ANNOTATION_NAME.fullmatch(t) else None


def _ancestors(node: Node, depth: int):
    cur = node.parent
    for _ in range(depth):
        if cur is None:
            return
        yield cur
        cur = cur.parent


def _descendants(node: Node, stop: frozenset[str] = frozenset()):
    """Every named node below `node`, not entering nodes of the `stop` types (nested defs and classes)."""
    stack = list(node.named_children)
    while stack:
        n = stack.pop()
        yield n
        if n.type not in stop:
            stack.extend(n.named_children)


def _starts_with_string(block: Node | None) -> bool:
    first = next((c for c in block.named_children if c.type != "comment"), None) if block is not None else None
    if first is not None and first.type == "expression_statement" and first.named_child_count == 1:
        first = first.named_children[0]
    return first is not None and first.type == "string"  # the grammar gives a docstring bare or wrapped
