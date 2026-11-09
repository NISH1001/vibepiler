"""JavaScript / TypeScript adapter (tree-sitter javascript, typescript and tsx grammars).

Covers ESM (`import`/`export`), CommonJS (`require`/`module.exports`), classes,
arrow functions assigned to consts, JSX element references, and TypeScript
declarations (interfaces, abstract classes, enums, type aliases).
"""

from __future__ import annotations

import posixpath
from functools import cache
from pathlib import Path

from tree_sitter import Node

from vibepiler.frontend.facts import (
    CallSite,
    EntryHint,
    FileFacts,
    ImportDef,
    RefSite,
    SymbolDef,
)
from vibepiler.frontend.languages.base import (
    DecoratorWalk,
    ImportOption,
    LanguageSpec,
)
from vibepiler.frontend.languages.javascript_flow import JavaScriptFlow
from vibepiler.frontend.loader import SourceFile
from vibepiler.ir.model import NodeKind
from vibepiler.passes.manifests import js_package_for_specifier

SOURCE_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
ENTRY_FILE_STEMS = {"index", "main", "server", "app", "cli", "worker", "entry"}
NEXT_ROUTE_STEMS = {"page", "layout", "route", "loading", "error", "template", "default", "not-found", "middleware", "instrumentation", "global-error"}
ENTRY_DIRS = {"pages", "app", "api", "routes"}
TEST_DIRS = {"__tests__", "__mocks__", "test", "tests", "cypress", "e2e"}
PROJECT_FILES = ("package.json", "tsconfig.json", "jsconfig.json")


@cache
def project_dir(root: Path, rel_dir: str) -> str:
    """The folder `@/` and bare paths are anchored at: the nearest one above the file with a package.json or tsconfig.
    A monorepo keeps its frontend in a subfolder; the repo root is the fallback ("")."""
    d = rel_dir
    while d not in ("", "."):
        if any((root / d / f).is_file() for f in PROJECT_FILES):
            return d
        d = posixpath.dirname(d)
    return ""
ROUTE_METHODS = {"get", "post", "put", "delete", "patch", "all", "use", "ws", "head", "options", "route"}
ROUTE_RECEIVERS = {"app", "router", "server", "api", "fastify", "koa", "hono", "express", "r"}
TEST_CALLS = {"describe", "it", "test", "suite", "beforeEach", "afterEach", "beforeAll", "afterAll", "bench"}
ENTRY_FUNCTION_NAMES = {"main", "handler", "lambdaHandler"}
FRAMEWORK_EXPORTS = {"getServerSideProps", "getStaticProps", "getStaticPaths", "generateMetadata", "generateStaticParams", "loader", "action", "middleware", "config", "revalidate", "dynamic", "runtime", "metadata", "load", "handle", "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
ROUTE_DECORATORS = {"Get", "Post", "Put", "Delete", "Patch", "All", "Controller", "Query", "Mutation", "Subscription", "Resolver", "WebSocketGateway", "SubscribeMessage", "EventPattern", "MessagePattern", "Cron", "OnEvent", "Route", "Handler"}
HOOK_DECORATORS = {"Injectable", "Component", "Module", "Pipe", "Directive", "NgModule", "Entity", "Input", "Output", "Prop", "Watch", "Emit", "Provide", "Inject", "Global", "Catch", "UseGuards", "Guard", "Interceptor"}

_DECL_NAME_PARENTS = {
    "variable_declarator", "function_declaration", "function_expression", "generator_function_declaration", "generator_function",
    "class_declaration", "abstract_class_declaration", "interface_declaration", "type_alias_declaration", "enum_declaration",
    "required_parameter", "optional_parameter", "assignment_pattern", "rest_pattern", "array_pattern", "object_pattern", "pair_pattern",
    "object_assignment_pattern", "catch_clause", "labeled_statement", "import_specifier", "namespace_import", "import_clause",
    "export_specifier", "formal_parameters", "break_statement", "continue_statement", "shorthand_property_identifier_pattern",
    "enum_assignment", "type_parameter", "property_signature", "method_signature", "nested_identifier", "jsx_namespace_name",
    "namespace_export", "internal_module", "module", "ambient_declaration",
}
_UNWRAP = {"non_null_expression", "parenthesized_expression", "as_expression", "satisfies_expression", "type_assertion"}
_DECLARATION_WRAPPERS = frozenset({"export_statement", "variable_declarator", "lexical_declaration", "variable_declaration", "public_field_definition", "field_definition", "assignment_expression", "expression_statement"})
_TYPE_CONTEXT = {"type_annotation", "type_arguments", "generic_type", "union_type", "intersection_type", "array_type", "object_type", "type_predicate", "asserts", "lookup_type", "index_type_query", "conditional_type", "tuple_type", "readonly_type", "typeof_type", "type_query"}


class JavaScriptSpec(JavaScriptFlow, LanguageSpec):
    name = "javascript"
    branch_types = frozenset({"if_statement", "for_statement", "for_in_statement", "while_statement", "do_statement", "catch_clause", "switch_case", "ternary_expression"})
    nesting_types = frozenset({
        "if_statement", "for_statement", "for_in_statement", "while_statement", "do_statement", "try_statement", "switch_statement",
        "function_declaration", "function_expression", "arrow_function", "method_definition", "class_declaration",
    })
    function_types = frozenset({"function_declaration", "function_expression", "arrow_function", "method_definition", "generator_function_declaration", "generator_function"})
    cog_structures = frozenset({"if_statement", "for_statement", "for_in_statement", "while_statement", "do_statement", "catch_clause", "switch_statement", "ternary_expression"})
    call_types = frozenset({"call_expression"})
    leaf_types = frozenset({
        "identifier", "property_identifier", "shorthand_property_identifier", "shorthand_property_identifier_pattern", "private_property_identifier",
        "string", "string_fragment", "template_string", "number", "true", "false", "null", "undefined", "regex", "this",
        "type_identifier", "predefined_type",
    })
    builtins = frozenset({
        "console", "Math", "JSON", "Object", "Array", "Promise", "Map", "Set", "WeakMap", "WeakSet", "Number", "String", "Boolean", "Date", "Error",
        "TypeError", "RangeError", "SyntaxError", "Symbol", "RegExp", "Reflect", "Proxy", "BigInt", "Intl", "Buffer", "URL", "URLSearchParams",
        "TextEncoder", "TextDecoder", "AbortController", "Headers", "Request", "Response", "FormData", "Blob", "File", "Event", "CustomEvent",
        "parseInt", "parseFloat", "isNaN", "isFinite", "setTimeout", "setInterval", "clearTimeout", "clearInterval", "setImmediate", "queueMicrotask",
        "structuredClone", "fetch", "alert", "confirm", "prompt", "encodeURIComponent", "decodeURIComponent", "encodeURI", "decodeURI", "atob", "btoa",
        "document", "window", "globalThis", "navigator", "localStorage", "sessionStorage", "location", "history", "process", "require", "module",
        "exports", "__dirname", "__filename", "Int8Array", "Uint8Array", "Float32Array", "Float64Array", "ArrayBuffer", "DataView", "Function",
        "Infinity", "NaN", "undefined", "crypto", "performance", "requestAnimationFrame", "cancelAnimationFrame", "Image", "Audio", "Worker",
        "WebSocket", "XMLHttpRequest", "Node", "Element", "HTMLElement", "Iterator", "AggregateError", "Atomics", "SharedArrayBuffer", "eval",
    })
    self_names = frozenset({"this"})

    # ------------------------------------------------------------------ naming
    def module_name(self, sf: SourceFile) -> tuple[str, list[str]]:
        base = self._strip_ext(sf.rel)
        aliases: set[str] = set()
        is_index = Path(base).name == "index"
        variants = [base]
        if base.startswith("src/"):
            variants.append(base[4:])
        for v in variants:
            aliases.add(v)
            if is_index:
                parent = posixpath.dirname(v)
                if parent:
                    aliases.add(parent)
        aliases.discard(base)
        aliases.discard("")
        return base, sorted(aliases)

    @staticmethod
    def _strip_ext(path: str) -> str:
        for ext in SOURCE_EXTS:
            if path.endswith(ext):
                return path[: -len(ext)]
        return path

    def is_test_file(self, rel: str) -> bool:
        p = Path(rel)
        name = p.name
        return ".test." in name or ".spec." in name or any(part in TEST_DIRS for part in p.parts[:-1])

    def is_entry_module(self, rel: str) -> bool:
        p = Path(rel)
        name = p.name
        stem = name.split(".")[0]
        if stem in ENTRY_FILE_STEMS or stem in NEXT_ROUTE_STEMS:
            return True
        if ".config." in name or name.endswith(("rc.js", "rc.cjs", "rc.mjs")):
            return True
        return any(part in ENTRY_DIRS for part in p.parts[:-1])

    # ---------------------------------------------------------------- imports
    def _module_candidates(self, spec: str, facts: FileFacts) -> list[str]:
        if spec.startswith("."):
            base_dir = posixpath.dirname(facts.rel)
            path = posixpath.normpath(posixpath.join(base_dir, spec))
            roots = ["" if path == "." else path]
        elif spec.startswith(("@/", "~/")):
            roots = self._anchored(spec[2:], facts)
        elif spec.startswith("/"):
            roots = [spec.lstrip("/")]
        elif spec.startswith(("#", "node:")):
            return []
        else:
            roots = self._anchored(spec, facts)  # tsconfig baseUrl-style bare paths
        out: list[str] = []
        for r in roots:
            r = self._strip_ext(r)
            if r:
                out.append(r)
            out.append(f"{r}/index" if r else "index")
        return out

    @staticmethod
    def _anchored(path: str, facts: FileFacts) -> list[str]:
        base = project_dir(facts.file.root, posixpath.dirname(facts.rel)) if facts.file.root.is_dir() else ""
        roots = [f"src/{path}", path]
        return [posixpath.join(base, r) for r in roots] + (roots if base else [])

    def import_targets(self, imp: ImportDef, facts: FileFacts) -> list[ImportOption]:
        cands = self._module_candidates(imp.module, facts)
        if not cands:
            return []
        member = imp.name if imp.kind in ("member", "default") else None
        return [ImportOption(cands, member)]

    def package_target(self, imp: ImportDef) -> tuple[str, tuple[str, ...]] | None:
        spec = imp.module
        if spec.startswith((".", "/", "@/", "~/", "#")):
            return None
        pkg = js_package_for_specifier(spec)
        if pkg is None:
            core = spec[5:] if spec.startswith("node:") else spec
            parts = core.split("/")
            name, sub = "node:" + parts[0], parts[1:]
        else:
            parts = spec.split("/")
            n = 2 if spec.startswith("@") else 1
            name, sub = pkg, parts[n:]
        prefix = tuple(sub) + ((imp.name,) if imp.name and imp.name != "default" else ())
        return name, prefix

    # ---------------------------------------------------------------- AST hooks
    def is_branch(self, node: Node, sf: SourceFile) -> bool:
        if node.type in self.branch_types:
            return True
        if node.type == "binary_expression":
            op = node.child_by_field_name("operator")
            return op is not None and sf.text(op) in ("&&", "||", "??")
        return False

    def bool_op(self, node: Node, sf: SourceFile) -> str | None:
        op = node.child_by_field_name("operator") if node.type == "binary_expression" else None
        return t if op is not None and (t := sf.text(op)) in ("&&", "||", "??") else None

    def labeled_jump(self, node: Node) -> bool:
        return node.type in ("break_statement", "continue_statement") and any(c.type == "statement_identifier" for c in node.children)

    def documented(self, def_node: Node, sf: SourceFile) -> bool:
        """A `/** ... */` comment right above the declaration, or above the `export` or `const` that holds it."""
        n = def_node
        while n.parent is not None and n.parent.type in _DECLARATION_WRAPPERS:
            n = n.parent
        prev = n.prev_named_sibling
        while prev is not None and prev.type == "decorator":
            prev = prev.prev_named_sibling
        return prev is not None and prev.type == "comment" and sf.text(prev).startswith("/**")

    def body_statements(self, def_node: Node, sf: SourceFile) -> list[Node]:
        body = def_node.child_by_field_name("body")
        if body is None:
            return []
        if body.type == "statement_block":
            return [c for c in body.named_children if c.type != "comment"]
        return [body]  # expression-bodied arrow function

    def statement_call(self, stmt: Node, sf: SourceFile) -> Node | None:
        n = stmt
        if n.type in ("return_statement", "expression_statement"):
            if n.named_child_count != 1:
                return None
            n = n.named_children[0]
        while n.type in _UNWRAP and n.named_child_count:
            n = n.named_children[0]
        if n.type == "await_expression" and n.named_child_count:
            n = n.named_children[0]
        return n if n.type == "call_expression" else None

    def call_parts(self, call: Node, sf: SourceFile) -> tuple[tuple[str, ...] | None, list[str]]:
        ex = _Extractor(self, sf, FileFacts(file=sf, module_name=""))
        fn = call.child_by_field_name("function") or call.child_by_field_name("constructor")
        chain = ex.loose_chain_of(fn) if fn is not None else None
        args = call.child_by_field_name("arguments")
        texts: list[str] = []
        if args is not None:
            for a in args.named_children:
                if a.type == "comment":
                    continue
                t = sf.text(a)
                texts.append(t[3:] if a.type == "spread_element" else t)
        return chain, texts

    def except_clauses(self, def_node: Node, sf: SourceFile) -> list[tuple[Node, list[Node], bool]]:
        out = []
        for n in _walk_within_function(def_node, self.function_types):
            if n.type != "catch_clause":
                continue
            body = n.child_by_field_name("body")
            stmts = [c for c in body.named_children if c.type != "comment"] if body is not None else []
            out.append((n, stmts, True))
        return out

    def is_stub_body(self, stmts: list[Node], sf: SourceFile) -> bool:
        if not stmts:
            return True
        if len(stmts) != 1:
            return False
        s = stmts[0]
        if s.type == "return_statement" and s.named_child_count == 0:
            return True
        if s.type == "throw_statement":
            txt = sf.text(s).lower()
            return any(k in txt for k in ("not implemented", "notimplemented", "unimplemented", "todo", "not yet"))
        return False

    # ---------------------------------------------------------------- extract
    def extractor(self, sf: SourceFile, facts: FileFacts) -> "_Extractor":
        return _Extractor(self, sf, facts)


class TypeScriptSpec(JavaScriptSpec):
    name = "typescript"

    def annotation_slots(self, def_node: Node, sf: SourceFile) -> tuple[int, int]:
        params = def_node.child_by_field_name("parameters")
        typed = 0
        slots = int(def_node.child_by_field_name("parameter") is not None)  # `x => x`: one bare, untyped parameter
        for p in params.named_children if params is not None else []:
            if p.type not in ("required_parameter", "optional_parameter") or any(c.type == "this" for c in p.children):
                continue
            slots += 1
            typed += any(c.type == "type_annotation" for c in p.children)
        name = def_node.child_by_field_name("name")
        if name is None or sf.text(name) != "constructor":
            slots += 1
            typed += def_node.child_by_field_name("return_type") is not None
        return typed, slots


def _walk_within_function(root: Node, function_types: frozenset[str]):
    stack = list(reversed(root.children))
    while stack:
        n = stack.pop()
        if n.type in function_types:
            continue
        yield n
        stack.extend(reversed(n.children))


class _Extractor(DecoratorWalk):
    call_type = "call_expression"

    def __init__(self, spec: JavaScriptSpec, sf: SourceFile, facts: FileFacts) -> None:
        self.spec = spec
        self.sf = sf
        self.facts = facts
        self.export_names: set[str] = set()
        self.default_name: str | None = None

    def text(self, node: Node) -> str:
        return self.sf.text(node)

    def line(self, node: Node) -> int:
        return node.start_point[0] + 1

    # --- chains ---------------------------------------------------------------
    @staticmethod
    def _unwrap(node: Node) -> Node:
        while node.type in _UNWRAP and node.named_child_count:
            node = node.named_children[0]
        return node

    def chain_of(self, node: Node) -> tuple[str, ...] | None:
        parts: list[str] = []
        cur = node
        while True:
            cur = self._unwrap(cur)
            if cur.type in ("identifier", "this", "super"):
                parts.append(self.text(cur))
                break
            if cur.type == "member_expression":
                prop = cur.child_by_field_name("property")
                obj = cur.child_by_field_name("object")
                if prop is None or obj is None or prop.type not in ("property_identifier", "private_property_identifier"):
                    return None
                parts.append(self.text(prop))
                cur = obj
                continue
            return None
        parts.reverse()
        return tuple(parts)

    def loose_chain_of(self, node: Node) -> tuple[str, ...] | None:
        c = self.chain_of(node)
        if c is not None:
            return c
        node = self._unwrap(node)
        if node.type == "member_expression":
            prop = node.child_by_field_name("property")
            obj = node.child_by_field_name("object")
            if prop is not None and obj is not None:
                inner = self.loose_chain_of(obj)
                return (inner if inner is not None else ("*",)) + (self.text(prop),)
        return None

    def string_value(self, node: Node | None) -> str | None:
        if node is None:
            return None
        node = self._unwrap(node)
        if node.type == "string":
            return self.text(node)[1:-1]
        if node.type == "template_string" and node.named_child_count == 0:
            return self.text(node)[1:-1]
        return None

    def require_spec(self, node: Node | None) -> str | None:
        """`require('x')` / `import('x')` / `await import('x')` -> 'x'."""
        if node is None:
            return None
        node = self._unwrap(node)
        if node.type == "await_expression" and node.named_child_count:
            node = node.named_children[0]
        if node.type != "call_expression":
            return None
        fn = node.child_by_field_name("function")
        args = node.child_by_field_name("arguments")
        if fn is None or args is None or args.named_child_count != 1:
            return None
        if fn.type == "import" or (fn.type == "identifier" and self.text(fn) == "require"):
            return self.string_value(args.named_children[0])
        return None

    # --- visiting -------------------------------------------------------------
    def visit(self, node: Node, scope: str | None, class_scope: str | None, exported: bool = False, default: bool = False, decorators: list[Node] | None = None) -> None:
        """One node: its handler (see VISITORS) takes the whole subtree; any other node type is walked through."""
        handler = self.VISITORS.get(node.type)
        if handler is not None:
            handler(self, node, scope, class_scope, exported, default, decorators or [])
            return
        for child in node.named_children:
            self.visit(child, scope, class_scope)

    def on_type_alias(self, node: Node, scope: str | None, class_scope: str | None, exported: bool, _default: bool, _decorators: list[Node]) -> None:
        self.visit_type_decl(node, scope, exported, NodeKind.VARIABLE, {"type_alias": True})
        if (value := node.child_by_field_name("value")) is not None:
            self.visit(value, scope, class_scope)

    def on_member(self, node: Node, scope: str | None, class_scope: str | None, _exported: bool, _default: bool, _decorators: list[Node]) -> None:
        """`a.b.c` is one reference (bare `this` is not); `f().x` visits the call."""
        chain = self.chain_of(node)
        if chain is None:
            if (obj := node.child_by_field_name("object")) is not None:
                self.visit(obj, scope, class_scope)
        elif chain[0] not in ("this", "super") or len(chain) > 1:
            self.facts.refs.append(RefSite(chain=chain, line=self.line(node), scope=scope, context=self._ctx(node)))

    def on_type_identifier(self, node: Node, scope: str | None, _class_scope: str | None, _exported: bool, _default: bool, _decorators: list[Node]) -> None:
        parent = node.parent
        if parent is not None and parent.type in _DECL_NAME_PARENTS and parent.child_by_field_name("name") == node:
            return  # the name a type declaration defines
        self.facts.refs.append(RefSite(chain=(self.text(node),), line=self.line(node), scope=scope, context="type"))

    def on_anonymous(self, node: Node, scope: str | None, class_scope: str | None, _exported: bool, _default: bool, _decorators: list[Node]) -> None:
        """A callback or an object-literal method: what it does is attributed to the enclosing scope."""
        self.visit_function(node, scope, class_scope, False, False, [], anonymous=True)

    def on_pair(self, node: Node, scope: str | None, class_scope: str | None, _exported: bool, _default: bool, _decorators: list[Node]) -> None:
        if (value := node.child_by_field_name("value")) is not None:
            self.visit(value, scope, class_scope)  # the key is a name, not a reference

    # node type -> handler(self, node, scope, class_scope, exported, default, decorators)
    VISITORS = {
        "comment": lambda *_: None,
        "property_identifier": lambda *_: None,
        "jsx_closing_element": lambda *_: None,
        "export_statement": lambda self, n, scope, cls, exp, dflt, decs: self.visit_export(n, scope, cls),
        "import_statement": lambda self, n, scope, cls, exp, dflt, decs: self.visit_import(n, scope),
        "function_declaration": lambda self, n, scope, cls, exp, dflt, decs: self.visit_function(n, scope, cls, exp, dflt, decs),
        "generator_function_declaration": lambda self, n, scope, cls, exp, dflt, decs: self.visit_function(n, scope, cls, exp, dflt, decs),
        "class_declaration": lambda self, n, scope, cls, exp, dflt, decs: self.visit_class(n, scope, exp, dflt, decs),
        "abstract_class_declaration": lambda self, n, scope, cls, exp, dflt, decs: self.visit_class(n, scope, exp, dflt, decs),
        "interface_declaration": lambda self, n, scope, cls, exp, dflt, decs: self.visit_type_decl(n, scope, exp, NodeKind.CLASS, {"is_interface": True}),
        "enum_declaration": lambda self, n, scope, cls, exp, dflt, decs: self.visit_type_decl(n, scope, exp, NodeKind.CLASS, {"is_enum": True}),
        "type_alias_declaration": on_type_alias,
        "lexical_declaration": lambda self, n, scope, cls, exp, dflt, decs: self.visit_declaration(n, scope, cls, exp, dflt),
        "variable_declaration": lambda self, n, scope, cls, exp, dflt, decs: self.visit_declaration(n, scope, cls, exp, dflt),
        "call_expression": lambda self, n, scope, cls, exp, dflt, decs: self.visit_call(n, scope, cls),
        "new_expression": lambda self, n, scope, cls, exp, dflt, decs: self.visit_new(n, scope, cls),
        "member_expression": on_member,
        "identifier": lambda self, n, scope, cls, exp, dflt, decs: self.visit_identifier(n, scope),
        "shorthand_property_identifier": lambda self, n, scope, cls, exp, dflt, decs: self.facts.refs.append(RefSite(chain=(self.text(n),), line=self.line(n), scope=scope)),
        "type_identifier": on_type_identifier,
        "jsx_opening_element": lambda self, n, scope, cls, exp, dflt, decs: self.visit_jsx(n, scope, cls),
        "jsx_self_closing_element": lambda self, n, scope, cls, exp, dflt, decs: self.visit_jsx(n, scope, cls),
        "decorator": lambda self, n, scope, cls, exp, dflt, decs: self.visit_decorator(n, scope),
        "assignment_expression": lambda self, n, scope, cls, exp, dflt, decs: self.visit_assignment(n, scope, cls),
        "arrow_function": on_anonymous,
        "function_expression": on_anonymous,
        "generator_function": on_anonymous,
        "method_definition": on_anonymous,
        "pair": on_pair,
    }

    def _ctx(self, node: Node):  # noqa: ANN202
        cur = node.parent
        for _ in range(4):
            if cur is None:
                break
            if cur.type in _TYPE_CONTEXT:
                return "type"
            cur = cur.parent
        return "name"

    def visit_identifier(self, node: Node, scope: str | None) -> None:
        parent = node.parent
        if parent is None:
            return
        pt = parent.type
        if pt in _DECL_NAME_PARENTS:
            if pt == "variable_declarator" and parent.child_by_field_name("value") == node:
                pass  # `const a = b` -> b is a read
            else:
                return
        if pt in ("assignment_expression", "augmented_assignment_expression") and parent.child_by_field_name("left") == node:
            return
        if pt == "update_expression":
            return
        if pt == "for_in_statement" and parent.child_by_field_name("left") == node:
            return
        if pt == "export_statement":
            return
        self.facts.refs.append(RefSite(chain=(self.text(node),), line=self.line(node), scope=scope, context=self._ctx(node)))

    def visit_jsx(self, node: Node, scope: str | None, class_scope: str | None) -> None:
        name = node.child_by_field_name("name")
        if name is not None:
            chain = self.chain_of(name)
            if chain and (len(chain) > 1 or chain[0][:1].isupper()):
                self.facts.refs.append(RefSite(chain=chain, line=self.line(node), scope=scope, context="jsx"))
        for c in node.named_children:
            if name is None or c != name:
                self.visit(c, scope, class_scope)

    # --- declarations ---------------------------------------------------------
    def visit_export(self, node: Node, scope: str | None, class_scope: str | None) -> None:
        decorators = [c for c in node.children if c.type == "decorator"]
        for d in decorators:
            self.visit_decorator(d, scope)
        source = node.child_by_field_name("source")
        decl = node.child_by_field_name("declaration")
        value = node.child_by_field_name("value")
        line = self.line(node)
        clause = next((c for c in node.named_children if c.type == "export_clause"), None)
        if source is not None:
            self._reexport(node, clause, self.string_value(source) or "", line, scope)
        elif decl is not None:
            self.visit(decl, scope, class_scope, exported=True, default=any(c.type == "default" for c in node.children), decorators=decorators)
        elif value is not None:
            self._export_default(self._unwrap(value), line, scope, class_scope, decorators)
        elif clause is not None:
            for nm, _ in self._specifiers(clause):
                self._mark_export(self.text(nm), line, scope)
        else:
            for c in node.named_children:  # `export = x`
                if c.type == "identifier":
                    self._mark_export(self.text(c), line, scope)

    def _specifiers(self, clause: Node):  # noqa: ANN202 - yields (name, alias or None)
        for es in clause.named_children:
            nm = es.child_by_field_name("name") if es.type == "export_specifier" else None
            if nm is not None:
                yield nm, es.child_by_field_name("alias")

    def _mark_export(self, name: str, line: int, scope: str | None) -> None:
        self.export_names.add(name)
        self.facts.refs.append(RefSite(chain=(name,), line=line, scope=scope, context="export"))

    def _reexport(self, node: Node, clause: Node | None, spec: str, line: int, scope: str | None) -> None:
        """`export {a as b} from`, `export * as ns from`, `export * from`: imports that also export."""
        if clause is not None:
            for nm, al in self._specifiers(clause):
                name = self.text(nm)
                self.facts.imports.append(ImportDef(module=spec, name=name, alias=self.text(al) if al else name, line=line, kind="member", scope=scope))
            return
        ns = next((c for c in node.named_children if c.type == "namespace_export"), None)
        if ns is None:
            self.facts.imports.append(ImportDef(module=spec, line=line, kind="wildcard", scope=scope))
            return
        ident = next((c for c in ns.named_children if c.type == "identifier"), None)
        self.facts.imports.append(ImportDef(module=spec, alias=self.text(ident) if ident else None, line=line, kind="namespace", scope=scope))

    def _export_default(self, v: Node, line: int, scope: str | None, class_scope: str | None, decorators: list[Node]) -> None:
        """`export default <expression>`: a name, a function or class defined in place, or anything else."""
        if v.type == "identifier":
            self.default_name = self.text(v)
            self._mark_export(self.default_name, line, scope)
        elif v.type == "class":
            self.visit_class(v, scope, True, True, decorators)
        elif v.type in ("arrow_function", "function_expression", "function"):
            self.visit_function(v, scope, class_scope, True, True, decorators)
        else:
            self.visit(v, scope, class_scope)

    def visit_import(self, node: Node, scope: str | None) -> None:
        source = node.child_by_field_name("source")
        spec = self.string_value(source)
        if spec is None:
            return
        line = self.line(node)
        clause = next((c for c in node.named_children if c.type == "import_clause"), None)
        if clause is None:
            self.facts.imports.append(ImportDef(module=spec, line=line, kind="side_effect", scope=scope))
            return
        for c in clause.named_children:
            if c.type == "identifier":
                self.facts.imports.append(ImportDef(module=spec, name="default", alias=self.text(c), line=line, kind="default", scope=scope))
            elif c.type == "namespace_import":
                ident = next((x for x in c.named_children if x.type == "identifier"), None)
                self.facts.imports.append(ImportDef(module=spec, alias=self.text(ident) if ident else None, line=line, kind="namespace", scope=scope))
            elif c.type == "named_imports":
                for spec_node in c.named_children:
                    if spec_node.type != "import_specifier":
                        continue
                    nm = spec_node.child_by_field_name("name")
                    al = spec_node.child_by_field_name("alias")
                    if nm is None:
                        continue
                    self.facts.imports.append(ImportDef(module=spec, name=self.text(nm), alias=self.text(al) if al else None, line=line, kind="member", scope=scope))

    def visit_declaration(self, node: Node, scope: str | None, class_scope: str | None, exported: bool, default: bool) -> None:
        for decl in node.named_children:
            if decl.type != "variable_declarator":
                continue
            name = decl.child_by_field_name("name")
            value = decl.child_by_field_name("value")
            if name is None:
                continue
            spec = self.require_spec(value)
            if name.type == "identifier":
                nm = self.text(name)
                if value is not None and self._unwrap(value).type in ("arrow_function", "function_expression", "generator_function"):
                    self.visit_function(self._unwrap(value), scope, class_scope, exported, default, [], name_override=nm)
                    continue
                if spec is not None:
                    self.facts.imports.append(ImportDef(module=spec, alias=nm, line=self.line(decl), kind="module", scope=scope))
                    continue
                if scope is None:
                    self.facts.defs.append(
                        SymbolDef(kind=NodeKind.VARIABLE, name=nm, qualname=nm, start_line=self.line(decl), end_line=decl.end_point[0] + 1, node=decl, exported=exported)
                    )
                typ = decl.child_by_field_name("type")
                if typ is not None:
                    self.visit(typ, scope, class_scope)
                if value is not None:
                    self.visit(value, scope, class_scope)
                continue
            if name.type == "object_pattern" and spec is not None:
                for p in name.named_children:
                    if p.type == "shorthand_property_identifier_pattern":
                        self.facts.imports.append(ImportDef(module=spec, name=self.text(p), line=self.line(decl), kind="member", scope=scope))
                    elif p.type == "pair_pattern":
                        k = p.child_by_field_name("key")
                        v = p.child_by_field_name("value")
                        if k is not None and v is not None and v.type == "identifier":
                            self.facts.imports.append(ImportDef(module=spec, name=self.text(k), alias=self.text(v), line=self.line(decl), kind="member", scope=scope))
                continue
            # destructuring from an arbitrary expression: the pattern binds, the value is read
            self.visit(name, scope, class_scope)
            if value is not None:
                self.visit(value, scope, class_scope)

    def visit_function(
        self,
        node: Node,
        scope: str | None,
        class_scope: str | None,
        exported: bool,
        default: bool,
        decorators: list[Node],
        name_override: str | None = None,
        anonymous: bool = False,
        kind: NodeKind = NodeKind.FUNCTION,
    ) -> None:
        name_node = node.child_by_field_name("name")
        name = name_override or (self.text(name_node) if name_node is not None else None)
        if name is None and default:
            name = "default"
        if anonymous or name is None:
            self._visit_function_internals(node, scope, class_scope)
            return
        qualname = f"{scope}.{name}" if scope else name
        params_node = node.child_by_field_name("parameters")
        params = self.param_names(params_node) if params_node is not None else []
        dec_names = [self.decorator_name(d) for d in decorators]
        flags = {c.type for c in node.children if not c.is_named}
        if "static" in flags:
            dec_names.append("staticmethod")
        if "get" in flags:
            dec_names.append("property")
        if "set" in flags:
            dec_names.append("setter")
        attrs: dict = {}
        if default:
            attrs["default_export"] = True
        if node.type == "abstract_method_signature":
            dec_names.append("abstract")
            attrs["abstract"] = True
        d = SymbolDef(
            kind=kind,
            name=name,
            qualname=qualname,
            start_line=self.line(decorators[0] if decorators else node),
            end_line=node.end_point[0] + 1,
            node=node,
            parent_qualname=scope,
            decorators=dec_names,
            params=params,
            exported=(exported or default) if scope is None else False,
            is_async="async" in flags,
            attrs=attrs,
        )
        self.facts.defs.append(d)
        self.add_entry_hints(d)
        self._visit_function_internals(node, qualname, class_scope)

    def _visit_function_internals(self, node: Node, scope: str | None, class_scope: str | None) -> None:
        params_node = node.child_by_field_name("parameters")
        if params_node is not None:
            for p in params_node.named_children:
                for c in p.named_children:
                    if c.type == "identifier" and (p.child_by_field_name("pattern") == c or p.child_by_field_name("left") == c or p.type == "rest_pattern"):
                        continue
                    self.visit(c, scope, class_scope)
        ret = node.child_by_field_name("return_type")
        if ret is not None:
            self.visit(ret, scope, class_scope)
        body = node.child_by_field_name("body")
        if body is not None:
            self.visit(body, scope, class_scope)

    def visit_class(self, node: Node, scope: str | None, exported: bool, default: bool, decorators: list[Node]) -> None:
        name_node = node.child_by_field_name("name")
        name = self.text(name_node) if name_node is not None else ("default" if default else None)
        body = node.child_by_field_name("body")
        if name is None:
            if body is not None:
                self.visit(body, scope, scope)
            return
        qualname = f"{scope}.{name}" if scope else name
        attrs: dict = {}
        if node.type == "abstract_class_declaration":
            attrs["is_abstract"] = True
        if default:
            attrs["default_export"] = True
        d = SymbolDef(
            kind=NodeKind.CLASS,
            name=name,
            qualname=qualname,
            start_line=self.line(decorators[0] if decorators else node),
            end_line=node.end_point[0] + 1,
            node=node,
            parent_qualname=scope,
            decorators=[self.decorator_name(x) for x in decorators],
            bases=self._bases(node, scope),
            exported=(exported or default) if scope is None else False,
            attrs=attrs,
        )
        self.facts.defs.append(d)
        self.add_entry_hints(d)
        if body is not None:
            self._class_body(body, qualname)

    def _bases(self, node: Node, scope: str | None) -> list[str]:
        """What a class extends, each one a `base` reference; `implements` names no runtime base."""
        bases: list[str] = []
        heritage = next((c for c in node.named_children if c.type == "class_heritage"), None)
        for c in heritage.named_children if heritage is not None else []:
            target = None if c.type == "implements_clause" else c.child_by_field_name("value") if c.type == "extends_clause" else c
            if target is None:
                continue
            chain = self.loose_chain_of(target)
            if chain is None:
                self.visit(target, scope, None)
                continue
            bases.append(".".join(chain))
            self.facts.refs.append(RefSite(chain=chain, line=self.line(c), scope=scope, context="base"))
        return bases

    def _class_body(self, body: Node, qualname: str) -> None:
        """Members in order; a decorator on its own line belongs to the member after it."""
        pending: list[Node] = []
        for member in body.named_children:
            if member.type == "decorator":
                pending.append(member)
                self.visit_decorator(member, qualname)
                continue
            self._member(member, qualname, pending + [c for c in member.children if c.type == "decorator"])
            pending = []

    def _member(self, member: Node, qualname: str, decorators: list[Node]) -> None:
        """A method, a field (a method when it holds a function), or anything else in a class body."""
        if member.type in ("method_definition", "abstract_method_signature"):
            mname = member.child_by_field_name("name")
            self.visit_function(member, qualname, qualname, False, False, decorators, name_override=self.text(mname) if mname is not None else None, kind=NodeKind.METHOD)
        elif member.type in ("public_field_definition", "field_definition", "property_definition"):
            fname = member.child_by_field_name("name")
            value = member.child_by_field_name("value")
            if value is not None and fname is not None and self._unwrap(value).type in ("arrow_function", "function_expression"):
                self.visit_function(self._unwrap(value), qualname, qualname, False, False, decorators, name_override=self.text(fname), kind=NodeKind.METHOD)
                return
            for part in (member.child_by_field_name("type"), value):
                if part is not None:
                    self.visit(part, qualname, qualname)
        elif member.type not in ("method_signature", "property_signature", "index_signature", "comment"):
            self.visit(member, qualname, qualname)

    def visit_type_decl(self, node: Node, scope: str | None, exported: bool, kind: NodeKind, attrs: dict) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.text(name_node)
        qualname = f"{scope}.{name}" if scope else name
        self.facts.defs.append(
            SymbolDef(kind=kind, name=name, qualname=qualname, start_line=self.line(node), end_line=node.end_point[0] + 1, node=node, parent_qualname=scope, exported=exported if scope is None else False, attrs=dict(attrs))
        )
        body = node.child_by_field_name("body")
        if body is not None and kind is NodeKind.CLASS and attrs.get("is_interface"):
            # types referenced by the interface's members
            for sig in body.named_children:
                for c in sig.named_children:
                    if c.type in ("type_annotation", "formal_parameters"):
                        self.visit(c, qualname, None)

    def visit_assignment(self, node: Node, scope: str | None, class_scope: str | None) -> None:
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if left is not None:
            lchain = self.chain_of(left)
            if _is_commonjs_export(lchain):
                if right is not None:
                    self._commonjs_export(node, lchain, right, scope, class_scope)
                return
            if left.type == "member_expression":
                obj = left.child_by_field_name("object")
                if obj is not None:
                    self.visit(obj, scope, class_scope)
            elif left.type != "identifier":
                self.visit(left, scope, class_scope)
        if right is not None:
            self.visit(right, scope, class_scope)

    def _commonjs_export(self, node: Node, lchain: tuple[str, ...], right: Node, scope: str | None, class_scope: str | None) -> None:
        """`module.exports = x`, `= {a, b: c}`, `exports.name = function () {}`."""
        named = len(lchain) > (1 if lchain[0] == "exports" else 2)  # exports.name / module.exports.name
        r = self._unwrap(right)
        if r.type == "identifier":
            self._mark_export(self.text(r), self.line(node), scope)
        elif r.type == "object" and not named:
            for c in r.named_children:
                self._export_property(c, scope, class_scope)
        elif named and r.type in ("arrow_function", "function_expression"):
            self.visit_function(r, scope, class_scope, True, False, [], name_override=lchain[-1])
        else:
            self.visit(right, scope, class_scope)

    def _export_property(self, c: Node, scope: str | None, class_scope: str | None) -> None:
        if c.type == "shorthand_property_identifier":
            self._mark_export(self.text(c), self.line(c), scope)
            return
        v = c.child_by_field_name("value") if c.type == "pair" else None
        if v is None:
            return
        if self._unwrap(v).type == "identifier":
            self._mark_export(self.text(self._unwrap(v)), self.line(c), scope)
        else:
            self.visit(v, scope, class_scope)

    # --- calls ----------------------------------------------------------------
    def visit_call(self, node: Node, scope: str | None, class_scope: str | None) -> None:
        fn = node.child_by_field_name("function")
        args = node.child_by_field_name("arguments")
        nargs = len(args.named_children) if args is not None else 0
        if fn is None:
            return
        spec = self.require_spec(node)
        if spec is not None:
            self.facts.imports.append(ImportDef(module=spec, line=self.line(node), kind="side_effect", scope=scope))
            return
        chain = self.loose_chain_of(fn)
        if chain is not None:
            self.facts.calls.append(CallSite(chain=chain, line=self.line(node), scope=scope, args=nargs))
            if scope is None:
                self._module_call_hints(chain, args, node)
            if chain[0] == "*":
                inner = self._unwrap(fn)
                while inner.type == "member_expression":
                    obj = inner.child_by_field_name("object")
                    if obj is None:
                        break
                    inner = self._unwrap(obj)
                if inner.type != "member_expression":
                    self.visit(inner, scope, class_scope)
        else:
            self.visit(fn, scope, class_scope)
        type_args = node.child_by_field_name("type_arguments")
        if type_args is not None:
            self.visit(type_args, scope, class_scope)
        if args is not None:
            self.visit(args, scope, class_scope)

    def visit_new(self, node: Node, scope: str | None, class_scope: str | None) -> None:
        ctor = node.child_by_field_name("constructor")
        args = node.child_by_field_name("arguments")
        nargs = len(args.named_children) if args is not None else 0
        if ctor is not None:
            chain = self.loose_chain_of(ctor)
            if chain is not None:
                self.facts.calls.append(CallSite(chain=chain, line=self.line(node), scope=scope, is_new=True, args=nargs))
            else:
                self.visit(ctor, scope, class_scope)
        type_args = node.child_by_field_name("type_arguments")
        if type_args is not None:
            self.visit(type_args, scope, class_scope)
        if args is not None:
            self.visit(args, scope, class_scope)

    def _module_call_hints(self, chain: tuple[str, ...], args: Node | None, node: Node) -> None:
        line = self.line(node)
        last = chain[-1]
        has_fn_arg = args is not None and any(a.type in ("arrow_function", "function_expression") for a in args.named_children)
        if len(chain) == 1 and last in TEST_CALLS and has_fn_arg:
            self.facts.entry_hints.append(EntryHint("test", None, line, last))
        elif last == "listen" and len(chain) >= 2:
            self.facts.entry_hints.append(EntryHint("main", None, line, ".".join(chain)))
        elif len(chain) >= 2 and last in ROUTE_METHODS and has_fn_arg and (chain[0] in ROUTE_RECEIVERS or chain[0] == "*"):
            self.facts.entry_hints.append(EntryHint("route", None, line, ".".join(chain)))

    # --- helpers --------------------------------------------------------------
    def param_names(self, params: Node) -> list[str]:
        out: list[str] = []
        for p in params.named_children:
            if p.type == "identifier":
                out.append(self.text(p))
            elif p.type in ("required_parameter", "optional_parameter"):
                pat = p.child_by_field_name("pattern")
                if pat is None:
                    continue
                out.append(self.text(pat) if pat.type != "rest_pattern" else self.text(pat).lstrip("."))
            elif p.type == "assignment_pattern":
                left = p.child_by_field_name("left")
                if left is not None:
                    out.append(self.text(left))
            elif p.type == "rest_pattern":
                out.append(self.text(p).lstrip("."))
            elif p.type in ("object_pattern", "array_pattern"):
                out.append(self.text(p))
        return out

    def add_entry_hints(self, d: SymbolDef) -> None:
        line = d.start_line
        for dec in d.decorators:
            last = dec.split(".")[-1]
            if last in ROUTE_DECORATORS:
                self.facts.entry_hints.append(EntryHint("route", d.qualname, line, dec))
            elif last in HOOK_DECORATORS:
                self.facts.entry_hints.append(EntryHint("hook", d.qualname, line, dec))
        if d.parent_qualname is None and d.kind is NodeKind.FUNCTION and d.name in ENTRY_FUNCTION_NAMES:
            self.facts.entry_hints.append(EntryHint("main", d.qualname, line))
        if d.parent_qualname is None and d.exported and d.name in FRAMEWORK_EXPORTS:
            self.facts.entry_hints.append(EntryHint("hook", d.qualname, line, "framework export"))

    def finish(self) -> None:
        for d in self.facts.defs:
            if d.parent_qualname is not None:
                continue
            if d.name in self.export_names:
                d.exported = True
            if self.default_name is not None and d.name == self.default_name:
                d.exported = True
                d.attrs["default_export"] = True


def _is_commonjs_export(chain: tuple[str, ...]) -> bool:
    """`exports...` or `module.exports...` on the left of an assignment."""
    return bool(chain) and (chain[0] == "exports" or (chain[0] == "module" and len(chain) > 1 and chain[1] == "exports"))
