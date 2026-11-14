"""IR builder: the semantic-analysis + linking stage.

Takes per-file facts from the language adapters and produces one
`ProgramGraph` for the whole project:

1. every file becomes a module node; every definition a symbol node (contains edges);
2. imports are resolved to internal modules or external packages (imports edges)
   and produce a *binding table* per module (local name -> what it refers to);
3. class bases are resolved (inherits edges);
4. every call site / reference is resolved through the binding table, the
   enclosing class, and module/class member lookup (calls / references edges);
   a receiver whose class the source states (`db: Database`, `db = Database()`,
   `self.db: Database`, `db = connect()` with `def connect() -> Database`) is
   typed, so `db.query()` links to `Database.query`.

Resolution is conservative: when a receiver's type is unknown (e.g. `obj.run()`
where nothing says what `obj` holds), the call is not guessed; the member name
is recorded in `graph.meta["used_member_names"]` so later passes can stay
conservative too.
"""

from __future__ import annotations

import builtins as _py_builtins
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Union

from vibepiler.config import Config
from vibepiler.frontend.facts import CallSite, FileFacts, ImportDef, RefSite, TypeHint
from vibepiler.frontend.languages import get_language
from vibepiler.frontend.languages.base import LanguageSpec
from vibepiler.frontend.loader import STRING_ROOT, SourceFile, check_includes, discover, in_scope
from vibepiler.ir.graph import ProgramGraph
from vibepiler.ir.model import CALLABLE_KINDS, Edge, EdgeKind, Node, NodeKind
from vibepiler.ir.program import Program
from vibepiler.progress import log, timed

PY_BUILTINS = frozenset(dir(_py_builtins))
# what an object handed to these is never asked to do: they store, copy or serialise it, they call none of its methods
INERT_PACKAGES = frozenset({"pickle", "copy", "weakref", "json", "marshal", "shelve", "logging", "typing", "dataclasses", "collections", "itertools", "functools", "operator"})
CONTAINER_METHODS = frozenset({"append", "add", "extend", "insert", "appendleft", "put", "put_nowait", "setdefault", "update", "remove", "discard", "index", "count", "push"})


# --------------------------------------------------------------------------- bindings
@dataclass(slots=True)
class ModuleBinding:
    path: str  # dotted/slashed project module path; may be a prefix of a real module


@dataclass(slots=True)
class MemberBinding:
    module_path: str
    member: str


@dataclass(slots=True)
class DefBinding:
    symbol_id: str


@dataclass(slots=True)
class PackageBinding:
    package_id: str
    prefix: tuple[str, ...]


Binding = Union[ModuleBinding, MemberBinding, DefBinding, PackageBinding]


@dataclass(slots=True)
class Resolution:
    node_id: str
    rest: tuple[str, ...] = ()
    via: str | None = None  # the class a member was looked up on when an ancestor defines it: `Sub.as_view` uses Sub
    receiver: str | None = None  # the class of the instance a member was looked up on: `db.query()` with `db: Database`

    @property
    def exact(self) -> bool:
        return not self.rest


# --------------------------------------------------------------------------- builder
def module_id(rel: str) -> str:
    return f"mod:{rel}"


def symbol_id(rel: str, qualname: str) -> str:
    return f"sym:{rel}::{qualname}"


def package_id(name: str) -> str:
    return f"pkg:{name}"


def read_sources(root: Path, cfg: Config, languages: list[str] | None = None) -> list[SourceFile]:
    """The files a compile reads. Named paths are always checked; isolated, only they are read, otherwise the whole
    source is, so calls and entry points outside them are still seen (they are reported on, not read alone)."""
    check_includes(root, cfg.include)
    return discover(root, include=cfg.include if cfg.isolate else None, exclude=cfg.exclude, languages=languages or cfg.languages, use_gitignore=cfg.use_gitignore)


def build_program(root: str | Path, config: Config | None = None, files: list[SourceFile] | None = None, scope: str = "directory") -> Program:
    """`scope` is what the caller handed over: "directory", "file" or "string"; passes that judge a whole project read it from `graph.meta`."""
    root = Path(root)
    root = root if root == STRING_ROOT else root.resolve()  # a string program has no place on disk
    cfg = config or Config.load(root)
    if files is None:
        files = read_sources(root, cfg)
    builder = _Builder(root, files, cfg, scope)
    return builder.build()


class _Builder:
    def __init__(self, root: Path, files: list[SourceFile], cfg: Config, scope: str = "directory") -> None:
        self.root = root
        self.scope = scope
        self.files = files
        self.cfg = cfg
        self.graph = ProgramGraph()
        self.facts: dict[str, FileFacts] = {}
        self.specs: dict[str, LanguageSpec] = {}
        self.module_index: dict[str, str] = {}  # module name / alias -> module id
        self.canonical_index: dict[str, str] = {}  # module name -> module id, aliases left out
        self.fallbacks: set[tuple[str, str]] = set()  # (module id, name) both defined and imported at top level
        self.symbols: dict[str, dict[str, str]] = {}  # module id -> qualname -> symbol id
        self.bindings: dict[str, dict[str, Binding]] = {}  # module id -> local name -> binding
        self.scoped_bindings: dict[tuple[str, str], dict[str, Binding]] = {}
        self.import_records: dict[str, list[dict]] = {}
        self.used_member_names: Counter[str] = Counter()
        self.read_member_names: Counter[str] = Counter()  # attribute names read anywhere, never counting a store
        self.stats: Counter[str] = Counter()
        self.hints: dict[tuple[str, str, str | None, str], list[tuple[str, TypeHint]]] = {}  # ("local", module id, scope, name) / ("field", class id, None, name) -> (module id, hint)
        self.classes: dict[tuple, str | None] = {}  # the class a name, field or return type holds, once worked out
        self.parse_errors: list[str] = []

    # ------------------------------------------------------------------ driver
    def build(self) -> Program:
        with timed(f"parse: {len(self.files)} files"):
            self._extract()
        with timed("link: modules indexed, symbols created"):
            self._index_modules()
            self._create_symbols()
        with timed("link: imports and bases resolved"):
            self._resolve_imports()
            self._resolve_inheritance()
            self._index_types()
            self._mark_registered()
        with timed("link: calls and references resolved"):
            self._resolve_calls_and_refs()
        self._finalize_meta()
        st = self.stats
        log.debug("link: {} call sites: {} to project code ({} through a typed receiver), {} to packages, {} to builtins, {} unresolved or partial; {} parse errors", st["calls_total"], st["calls_resolved"], st["calls_typed"], st["calls_external"], st["calls_builtin"], st["calls_total"] - st["calls_resolved"] - st["calls_external"] - st["calls_builtin"], len(self.parse_errors))
        return Program(root=self.root, graph=self.graph, files=self.files, facts=self.facts, config=self.cfg)

    # ------------------------------------------------------------------ phase 1
    def _extract(self) -> None:
        slow: list[tuple[float, str]] = []
        for sf in self.files:
            spec = get_language(sf.language)
            t = time.perf_counter()
            try:
                facts = spec.extract(sf)
                slow.append((time.perf_counter() - t, sf.rel))
            except Exception as exc:  # a broken file must not kill the whole build
                self.parse_errors.append(f"{sf.rel}: {type(exc).__name__}: {exc}")
                continue
            mid = module_id(sf.rel)
            self.facts[mid] = facts
            self.specs[mid] = spec
            node = Node(
                id=mid,
                kind=NodeKind.MODULE,
                name=facts.module_name,
                qualname=facts.module_name,
                file=sf.rel,
                language=sf.language,
                start_line=1,
                end_line=max(1, sf.lines),
                attrs={
                    "loc": sf.lines,
                    "is_test": facts.is_test,
                    "has_main_guard": facts.has_main_guard,
                    "aliases": facts.aliases,
                    "has_errors": bool(sf.tree.root_node.has_error),
                    "is_entry_module": spec.is_entry_module(sf.rel),
                    "in_scope": in_scope(sf.rel, self.cfg.include),
                    **facts.attrs,
                },
            )
            self.graph.add_node(node)
        slow.sort(reverse=True)
        if slow and slow[0][0] >= 0.05:  # only worth a line when some file is slow
            log.debug("parse: slowest {}", ", ".join(f"{rel} {secs:.2f} s" for secs, rel in slow[:3]))

    def _index_modules(self) -> None:
        # canonical names first so aliases never shadow them
        for mid, facts in self.facts.items():
            self.module_index.setdefault(facts.module_name, mid)
            self.canonical_index.setdefault(facts.module_name, mid)
        for mid, facts in self.facts.items():
            for alias in facts.aliases:
                self.module_index.setdefault(alias, mid)
        self._mark_project_entries()

    def _mark_project_entries(self) -> None:
        """Modules the project's config names: what a package exposes (`main`, `exports`, its `./*` subpaths), whose
        exports are then its API, and the files `bin`, `scripts` and CI run. The first candidate we have is the one."""
        specs = {id(s): s for s in self.specs.values()}.values()
        for spec in specs:
            for kind, cands, prefix in spec.project_entries(self.root):
                if prefix:
                    cand = next((c for c in cands if any(f.module_name.startswith(f"{c}/") for f in self.facts.values())), None)
                    hits = [mid for mid, f in self.facts.items() if cand is not None and f.module_name.startswith(f"{cand}/") and not f.is_test]
                else:
                    hits = [self.canonical_index[c] for c in cands if c in self.canonical_index][:1]
                for mid in hits:
                    node = self.graph.node(mid)
                    if kind == "package_entry":
                        node.attrs["is_entry_module"] = True
                    entry = node.attrs.setdefault("entry", [])
                    if kind not in entry:
                        entry.append(kind)

    def _create_symbols(self) -> None:
        for mid, facts in self.facts.items():
            rel = facts.rel
            table: dict[str, str] = {}
            self.symbols[mid] = table
            for d in facts.defs:
                sid = symbol_id(rel, d.qualname)
                if sid in table.values():
                    continue  # redefinition (e.g. overloads) - keep the first
                attrs = {
                    "decorators": d.decorators,
                    "params": d.params,
                    "exported": d.exported,
                    "is_async": d.is_async,
                    "bases": d.bases,
                    **d.attrs,
                }
                node = Node(
                    id=sid,
                    kind=d.kind,
                    name=d.name,
                    qualname=d.qualname,
                    module=mid,
                    file=rel,
                    language=facts.language,
                    start_line=d.start_line,
                    end_line=d.end_line,
                    attrs=attrs,
                )
                self.graph.add_node(node)
                table[d.qualname] = sid
            for d in facts.defs:
                sid = table[d.qualname]
                parent = table.get(d.parent_qualname) if d.parent_qualname else None
                self.graph.add_edge(Edge(src=parent or mid, dst=sid, kind=EdgeKind.CONTAINS))
            # entry hints
            for h in facts.entry_hints:
                target = table.get(h.qualname) if h.qualname else mid
                if target is None:
                    continue
                entry = self.graph.node(target).attrs.setdefault("entry", [])
                if h.kind not in entry:
                    entry.append(h.kind)

    # ------------------------------------------------------------------ phase 2
    def _ensure_package(self, name: str) -> str:
        pid = package_id(name)
        if not self.graph.has_node(pid):
            self.graph.add_node(Node(id=pid, kind=NodeKind.PACKAGE, name=name, qualname=name))
        return pid

    def _resolve_imports(self) -> None:
        for mid, facts in self.facts.items():
            spec = self.specs[mid]
            table = self.bindings.setdefault(mid, {})
            for qual, sid in self.symbols[mid].items():  # local definitions bind first
                if "." not in qual:
                    table[qual] = DefBinding(sid)
            records: list[dict] = []
            self.import_records[mid] = records
            for imp in facts.imports:
                binding, target = self._resolve_one_import(imp, facts, spec)
                if imp.kind == "by_name":
                    self._link_named_symbol(mid, imp)
                    if target is None:
                        continue  # a dotted string that names none of our modules is just text
                records.append({"local_name": imp.local_name, "module": imp.module, "name": imp.name, "alias": imp.alias, "line": imp.line, "kind": imp.kind, "scope": imp.scope, "resolved": target, "used": False})
                if target is not None:
                    self._link_import(mid, spec, imp, target)
                if binding is not None:
                    self._bind_import(mid, table, imp, binding, target)
        for mid, facts in self.facts.items():
            self._mark_public_api(mid, facts)
            self._mark_reexported_api(mid)
            self._mark_reexports(mid)
        self._mark_loaded_by_name()

    def _mark_loaded_by_name(self) -> None:
        """A module only ever named in a string (`import_module(self.compiler_module)`) is loaded to look names up in it
        (`getattr(module, name)`): its public top-level names are entry points. One that is also imported is linked as usual."""
        for mid in self.facts:
            into = self.graph.in_edges(mid, EdgeKind.IMPORTS)
            if not into or not all(e.attrs.get("by_name") for e in into):
                continue
            for qual, sid in self.symbols[mid].items():
                if "." not in qual and not qual.startswith("_"):
                    entry = self.graph.node(sid).attrs.setdefault("entry", [])
                    if "loaded_by_name" not in entry:
                        entry.append("loaded_by_name")

    def _link_named_symbol(self, mid: str, imp: ImportDef) -> None:
        """`"django.template.context_processors.debug"` in settings: a dotted string ending in a top-level function or
        class of ours names it for a framework that imports it by that name, so it is a use."""
        module, _, name = imp.module.rpartition(".")
        target = self.module_index.get(module)
        sid = self.symbols.get(target, {}).get(name) if target is not None else None
        if sid is None:
            return
        e = self.graph.add_edge(Edge(src=self._scope_node(mid, imp.scope), dst=sid, kind=EdgeKind.REFERENCES, line=imp.line))
        if "string" not in (ctxs := e.attrs.setdefault("contexts", [])):
            ctxs.append("string")

    def _mark_registered(self) -> None:
        """`@app.template_filter("x")`, `@handlers.append`: a decorator reached through an object (an instance, a
        parameter, a local) hands the function to it, which keeps it to call later. One reached through a module
        (`@functools.cache`, `@utils.timed`) is taken to wrap it, as before."""
        for mid, facts in self.facts.items():
            if facts.language != "python":
                continue
            for d in facts.defs:
                for dec in d.decorators:
                    chain = tuple(dec.split("."))
                    if len(chain) < 2 or not all(part.isidentifier() for part in chain):
                        continue
                    head = self._resolve_chain(mid, d.parent_qualname, chain[:1])
                    if head is not None and self.graph.node(head.node_id).kind in (NodeKind.MODULE, NodeKind.PACKAGE):
                        continue
                    entry = self.graph.node(self.symbols[mid][d.qualname]).attrs.setdefault("entry", [])
                    if "registered" not in entry:
                        entry.append("registered")
                    break

    def _mark_public_api(self, mid: str, facts: FileFacts) -> None:
        """What a top-level Python package re-exports (`pkg/__init__.py`, narrowed by `__all__`, through any chain of
        imports and `*`) is its public API: `import pkg; pkg.name` reaches it from outside the project, so it is an entry point."""
        if facts.file.language != "python" or "." in facts.module_name or Path(facts.rel).name != "__init__.py":
            return
        public = facts.attrs.get("__all__")
        for name, b in self.bindings[mid].items():
            if isinstance(b, DefBinding) or name.startswith("_") or (public is not None and name not in public):
                continue
            res = self._resolve_binding(b, (), 0)
            if res is None or not res.exact or self.graph.node(res.node_id).kind in (NodeKind.MODULE, NodeKind.PACKAGE):
                continue
            entry = self.graph.node(res.node_id).attrs.setdefault("entry", [])
            if "public_api" not in entry:
                entry.append("public_api")

    def _mark_reexported_api(self, mid: str) -> None:
        """`export * from './react'`, `export { combine } from './middleware/combine'` in the module a package exposes
        (its `main` or `exports`): what it passes on is the package's API too, through any chain of re-exports. A
        barrel inside an app (`utils/index.ts`) is not: a name it passes on that nobody imports is still unused."""
        node = self.graph.node(mid)
        if node.language == "python" or "package_entry" not in node.attrs.get("entry", ()) or self.cfg.treat_exports_as_roots is False:
            return
        for sid in self._reexported(mid, set()):
            entry = self.graph.node(sid).attrs.setdefault("entry", [])
            if "public_api" not in entry:
                entry.append("public_api")

    def _reexported(self, mid: str, seen: set[str]) -> list[str]:
        seen.add(mid)
        out: list[str] = []
        facts = self.facts[mid]
        for imp in facts.imports:
            if not imp.reexport or imp.scope is not None:
                continue
            _, target = self._resolve_one_import(imp, facts, self.specs[mid], count=False)
            if target not in self.facts:
                continue
            if imp.kind == "member" and imp.name:
                res = self._resolve_binding(MemberBinding(self.facts[target].module_name, imp.name), (), 0)
                if res is not None and res.exact and self.graph.node(res.node_id).kind not in (NodeKind.MODULE, NodeKind.PACKAGE):
                    out.append(res.node_id)
            elif target not in seen:  # `export *` and `export * as ns`: everything the target exports, its own re-exports too
                out += [sid for q, sid in self.symbols[target].items() if "." not in q and self.graph.node(sid).attrs.get("exported")]
                out += self._reexported(target, seen)
        return out

    def _mark_reexports(self, mid: str) -> None:
        """`from .compat import quote` uses compat's own import of `quote`: compat passes the name on, so it is not unused."""
        for rec in self.import_records[mid]:
            target, name = rec["resolved"], rec["name"]
            if name is None or target not in self.facts or target == mid or name in self.symbols[target]:
                continue
            self._mark_used(target, name, None)

    def _link_import(self, mid: str, spec: LanguageSpec, imp: ImportDef, target: str) -> None:
        """The imports edge, labelled with what it imports, plus the packages Python runs on the way (`a/__init__`).
        An edge is `deferred` while every import behind it runs late or never (inside a function, under TYPE_CHECKING)."""
        fresh = not self.graph.has_edge_kind(mid, target, EdgeKind.IMPORTS)
        edge = self.graph.add_edge(Edge(src=mid, dst=target, kind=EdgeKind.IMPORTS, line=imp.line))
        late = imp.scope is not None or imp.type_only or imp.kind == "by_name"
        if late and (fresh or edge.attrs.get("deferred")):
            edge.attrs["deferred"] = True
        else:
            edge.attrs.pop("deferred", None)
        if imp.kind == "by_name" and (fresh or edge.attrs.get("by_name")):
            edge.attrs["by_name"] = True
        else:
            edge.attrs.pop("by_name", None)
        if (pkg := spec.sibling_package(self.root, imp.module, self.facts[mid].rel)) is not None and pkg not in edge.attrs.setdefault("packages", []):
            edge.attrs["packages"].append(pkg)  # `@scope/shared` from a sibling: our source, and still a declared dependency
        names = edge.attrs.setdefault("names", [])
        label = imp.name or imp.module
        if label and label not in names:
            names.append(label)
        if target not in self.facts:
            return
        for parent in spec.implicit_imports(self.facts[target].module_name):
            pid = self.module_index.get(parent)
            if pid is not None and pid != mid and not self.graph.has_edge_kind(mid, pid, EdgeKind.IMPORTS):
                self.graph.add_edge(Edge(src=mid, dst=pid, kind=EdgeKind.IMPORTS, line=imp.line, attrs={"implicit": True}))

    def _bind_import(self, mid: str, table: dict, imp: ImportDef, binding: Binding, target: str | None) -> None:
        """The names an import puts in scope: every exported top-level name for `*`, else its local name."""
        if imp.kind == "wildcard":
            if target in self.facts and self.facts[target].language == "python":
                for name in self._star_names(target, set()):
                    table.setdefault(name, MemberBinding(self.facts[target].module_name, name))
                return
            for qual, sid in self.symbols.get(target, {}).items() if target is not None else ():
                if "." not in qual and self.graph.node(sid).attrs.get("exported", True):
                    table.setdefault(qual, MemberBinding(self.facts[target].module_name, qual))
        elif imp.scope is None and isinstance(table.get(imp.local_name), DefBinding):
            # `try: from fast import f / except ImportError: def f`: the def is the fallback, both are live; keep the def,
            # which is the one we can see into, and count the import used whenever the name is
            self.fallbacks.add((mid, imp.local_name))
        elif imp.local_name is not None:
            scope = self.scoped_bindings.setdefault((mid, imp.scope), {}) if imp.scope else table
            scope[imp.local_name] = binding

    def _star_names(self, mid: str, seen: set[str]) -> list[str]:
        """The names `from m import *` binds: m's `__all__`, else its public top-level names, its imports and what its own
        `*` imports bind, followed down the chain."""
        facts = self.facts[mid]
        if (public := facts.attrs.get("__all__")) is not None:
            return list(public)
        seen.add(mid)
        names = [q for q, sid in self.symbols[mid].items() if "." not in q and self.graph.node(sid).attrs.get("exported", True)]
        for imp in facts.imports:
            if imp.scope is not None:
                continue
            if imp.kind == "wildcard":
                _, sub = self._resolve_one_import(imp, facts, self.specs[mid], count=False)
                if sub in self.facts and sub not in seen:
                    names += self._star_names(sub, seen)
            elif imp.local_name and not imp.local_name.startswith("_") and imp.kind != "side_effect":
                names.append(imp.local_name)
        return names

    def _resolve_one_import(self, imp: ImportDef, facts: FileFacts, spec: LanguageSpec, count: bool = True) -> tuple[Binding | None, str | None]:
        own = module_id(facts.rel)
        # `import json` means the standard library unless a project module is named exactly `json`: not `pkg/json/` by
        # an alias, and never the importing module itself
        stdlib = imp.level == 0 and imp.module.split(".")[0] in spec.stdlib_modules
        index = self.canonical_index if stdlib else self.module_index
        for option in spec.import_targets(imp, facts):
            for cand in option.candidates:
                target = index.get(cand)
                if target is None or (stdlib and target == own):
                    continue
                if option.member is None:
                    return ModuleBinding(option.binding_path or cand), target
                return MemberBinding(cand, option.member), target
        pkg = spec.package_target(imp) if imp.kind != "by_name" else None
        if pkg is None and imp.kind == "by_name":
            return None, None
        if pkg is None:
            self.stats["imports_unresolved"] += count
            return None, None
        name, prefix = pkg
        pid = self._ensure_package(name)
        return PackageBinding(pid, prefix), pid

    # ------------------------------------------------------------------ phase 3
    def _resolve_inheritance(self) -> None:
        for mid, facts in self.facts.items():
            for d in facts.defs:
                if d.kind is not NodeKind.CLASS or not d.bases:
                    continue
                sid = self.symbols[mid][d.qualname]
                for base in d.bases:
                    chain = tuple(base.split("."))
                    res = self._resolve_chain(mid, d.parent_qualname, chain)
                    if res is None:
                        self.graph.node(sid).attrs.setdefault("unresolved_bases", []).append(base)
                        continue
                    target = self.graph.node(res.node_id)
                    if target.kind is NodeKind.PACKAGE:
                        e = self.graph.add_edge(Edge(src=sid, dst=res.node_id, kind=EdgeKind.INHERITS, line=d.start_line))
                        e.attrs.setdefault("members", [])
                        if res.rest and ".".join(res.rest) not in e.attrs["members"]:
                            e.attrs["members"].append(".".join(res.rest))
                    elif res.exact and target.kind is NodeKind.CLASS:
                        self.graph.add_edge(Edge(src=sid, dst=res.node_id, kind=EdgeKind.INHERITS, line=d.start_line))
                    else:
                        self.graph.node(sid).attrs.setdefault("unresolved_bases", []).append(base)

    # ------------------------------------------------------------------ types
    def _index_types(self) -> None:
        for mid, facts in self.facts.items():
            for h in facts.types:
                if h.of_class is None:
                    self.hints.setdefault(("local", mid, h.scope, h.name), []).append((mid, h))
                elif (cls := self.symbols[mid].get(h.of_class)) is not None:
                    self.hints.setdefault(("field", cls, None, h.name), []).append((mid, h))

    def _class_of(self, key: tuple) -> str | None:
        """The one class every hint for a name agrees on; None when they disagree, or one says nothing we can type."""
        if key in self.classes:
            return self.classes[key]
        self.classes[key] = None  # a hint that leads back to its own name types nothing
        found = {self._hint_class(mid, h) for mid, h in self.hints[key]}
        cls = found.pop() if len(found) == 1 else None
        self.classes[key] = cls
        return cls

    def _hint_class(self, mid: str, h: TypeHint) -> str | None:
        if not h.chain:
            return None
        if h.source == "name":
            return self._value_class(mid, h.scope, h.chain)
        res = self._resolve_chain(mid, h.scope, h.chain)
        if res is None or not res.exact:
            return None
        node = self.graph.node(res.node_id)
        if node.kind is NodeKind.CLASS:
            return node.id  # `x: Foo`, or `x = Foo()`
        return self._returns(node) if h.source == "call" and node.kind in CALLABLE_KINDS else None

    def _returns(self, fn: Node) -> str | None:
        """The class a function's return annotation names: `-> Database`, `-> "Database | None"`, `-> Self`."""
        ret = fn.attrs.get("returns")
        if not ret or fn.module is None:
            return None
        key = ("returns", fn.id)
        if key not in self.classes:
            parent = self.graph.parent(fn.id)
            if ret.split(".")[-1] == "Self":
                self.classes[key] = parent.id if parent is not None and parent.kind is NodeKind.CLASS else None
            else:
                self.classes[key] = None
                res = self._resolve_chain(fn.module, fn.qualname.rpartition(".")[0] or None, tuple(ret.split(".")))
                self.classes[key] = res.node_id if res is not None and res.exact and self.graph.node(res.node_id).kind is NodeKind.CLASS else None
        return self.classes[key]

    def _local_key(self, mid: str, scope: str | None, head: str) -> tuple | None:
        """The local `head` names in `scope`, or in an enclosing def (a closure sees its function's names)."""
        return next((key for name in self._scopes(mid, scope) if (key := ("local", mid, name, head)) in self.hints), None)

    def _value_class(self, mid: str, scope: str | None, chain: tuple[str, ...]) -> str | None:
        """The class of the object `x`, `self` or `self.db.conn` holds."""
        head, rest = chain[0], chain[1:]
        if head in self.specs[mid].self_names:
            cls = self._enclosing_class(mid, scope)
        elif (key := self._local_key(mid, scope, head)) is not None:
            cls = self._class_of(key)
        else:
            res = self._resolve_chain(mid, scope, (head,))
            cls = self._variable_class(res.node_id) if res is not None and res.exact else None
        for name in rest:
            if cls is None:
                return None
            cls = self._field_class(cls, name)
        return cls

    def _variable_class(self, node_id: str) -> str | None:
        """`client = Client()` at module level: what the variable holds."""
        node = self.graph.node(node_id)
        if node.kind is not NodeKind.VARIABLE or node.module is None or "." in node.qualname:
            return None
        key = ("local", node.module, None, node.name)
        return self._class_of(key) if key in self.hints else None

    def _field_class(self, cls: str, name: str) -> str | None:
        """What `obj.name` holds on an instance of `cls`: a field set on it or an ancestor, or a property's return type."""
        for c in [cls, *self._ancestors(cls)]:
            if ("field", c, None, name) in self.hints:
                return self._class_of(("field", c, None, name))
            for child in self.graph.children(c):
                if child.name == name:
                    return self._returns(child) if child.kind in CALLABLE_KINDS and _is_property(child) else None
        return None

    def _ancestors(self, cls: str) -> list[str]:
        out, stack = [], [cls]
        while stack:
            for e in self.graph.out_edges(stack.pop(), EdgeKind.INHERITS):
                if e.dst not in out and self.graph.node(e.dst).kind is NodeKind.CLASS:
                    out.append(e.dst)
                    stack.append(e.dst)
        return out

    def _instance_member(self, cls: str, rest: tuple[str, ...], depth: int) -> Resolution | None:
        """`rest` looked up on an instance of `cls`: its methods and its ancestors', then its typed fields."""
        if len(rest) > 1 and (field := self._field_class(cls, rest[0])) is not None and not self._defines(cls, rest[0]):
            return self._instance_member(field, rest[1:], depth + 1)
        res = self._resolve_member(cls, rest, depth)
        if res is not None and res.exact and len(rest) == 1:
            return Resolution(res.node_id, receiver=cls)
        return res

    def _defines(self, cls: str, name: str) -> bool:
        """Does the class or an ancestor have a method or nested class called `name` (a property counts as a field)?"""
        return any(c.name == name and not _is_property(c) for k in [cls, *self._ancestors(cls)] for c in self.graph.children(k))

    # ------------------------------------------------------------------ phase 4
    def _resolve_calls_and_refs(self) -> None:
        for mid, facts in self.facts.items():
            spec = self.specs[mid]
            self.read_member_names.update(spec.attribute_reads(facts.file))
            for call in facts.calls:
                self._link_call(mid, spec, call)
            for scope, prefix in facts.dispatches:
                self._link_dispatch(mid, scope, prefix)
            for scope, callee, receiver, ctor, line in facts.escapes:
                self._link_escape(mid, spec, scope, callee, receiver, ctor, line)
            for ref in facts.refs:
                if ref.context != "base":  # covered by inherits edges
                    self._link_ref(mid, ref)

    def _link_call(self, mid: str, spec: LanguageSpec, call: CallSite) -> None:
        """One call site: an edge to what it calls, or a count of why there is none."""
        self.stats["calls_total"] += 1
        src = self._scope_node(mid, call.scope)
        res = self._resolve_chain(mid, call.scope, call.chain)
        if res is None:
            self._unresolved_call(spec, call.chain)
            return
        self._reference_via(mid, call.scope, src, res, call.line)
        target = self.graph.node(res.node_id)
        if target.kind is NodeKind.PACKAGE:
            self.stats["calls_external"] += 1
            e = self.graph.add_edge(Edge(src=src, dst=res.node_id, kind=EdgeKind.CALLS, line=call.line))
            _add_member(e, ".".join(res.rest))
        elif res.exact and target.kind in CALLABLE_KINDS | {NodeKind.CLASS}:
            self.stats["calls_resolved"] += 1
            self.stats["calls_typed"] += self._typed_use(mid, call.chain, res, target)
            if res.node_id == src:
                self.graph.node(src).attrs["recursive"] = True
            else:
                self.graph.add_edge(Edge(src=src, dst=res.node_id, kind=EdgeKind.CALLS, line=call.line))
        else:  # partially resolved: module.CONST(...) or SomeClass.attr(...) or variable(...)
            self.stats["calls_partial"] += 1
            if res.rest:
                self.used_member_names[res.rest[-1]] += 1
            if res.node_id != src and not self._is_own_class(mid, call.scope, call.chain, res.node_id):
                self.graph.add_edge(Edge(src=src, dst=res.node_id, kind=EdgeKind.REFERENCES, line=call.line))

    def _typed_use(self, mid: str, chain: tuple[str, ...], res: Resolution, target: Node) -> bool:
        """Was a member found through what a receiver was typed as (not plain `self.m()`)? Through a class with a base
        we cannot see (a Protocol, an ABC, a framework class), code that never inherits from it may stand in for it, so
        the name still counts as used, as for an untyped receiver."""
        if res.receiver is None or (len(chain) == 2 and chain[0] in self.specs[mid].self_names):
            return False
        if self.graph.has_external_base(res.receiver):
            self.used_member_names[target.name] += 1
        return True

    def _link_dispatch(self, mid: str, scope: str | None, prefix: str) -> None:
        """`getattr(self, f"visit_{t}")()` calls, by name, every method starting with `visit_` on the class and below it."""
        cls = self._enclosing_class(mid, scope)
        if cls is None:
            return
        src = self._scope_node(mid, scope)
        for owner in [cls, *self.graph.subclasses(cls)]:
            for m in self.graph.children(owner):
                if m.kind is NodeKind.METHOD and m.name.startswith(prefix) and m.id != src:
                    self.graph.add_edge(Edge(src=src, dst=m.id, kind=EdgeKind.CALLS, attrs={"dispatch": prefix}))

    def _link_escape(self, mid: str, spec: LanguageSpec, scope: str, callee: tuple[str, ...], receiver: tuple[str, ...], ctor: tuple[str, ...], line: int) -> None:
        """Escape analysis: an instance of our class handed to code we cannot see (a package, or a receiver we cannot
        resolve) may have its public methods called there, as http.cookiejar calls `MockResponse.info()`."""
        cls = self._resolve_chain(mid, scope, ctor)
        if cls is None or not cls.exact or self.graph.node(cls.node_id).kind is not NodeKind.CLASS:
            return
        if callee[-1] in CONTAINER_METHODS:
            return  # `items.append(obj)`: almost always a list, set or dict
        if receiver and (own := self._resolve_chain(mid, scope, receiver)) is not None and self.graph.node(own.node_id).kind is NodeKind.CLASS:
            return  # handed to another object of ours: whatever it does with it is code we see
        res = self._resolve_chain(mid, scope, callee)
        if res is None:
            if callee[0] in spec.builtins or (spec.name == "python" and callee[0] in PY_BUILTINS):
                return
            if self.facts[mid].is_test:
                return  # in a test, a receiver we cannot type is nearly always one of ours (a fixture, a `.clone()`)
        elif self.graph.node(res.node_id).kind is not NodeKind.PACKAGE or self.graph.node(res.node_id).name in INERT_PACKAGES:
            return
        node = self.graph.node(cls.node_id)
        sites = node.attrs.setdefault("escapes", [])
        if len(sites) < 3:
            sites.append(f"{self.facts[mid].rel}:{line}")

    def _unresolved_call(self, spec: LanguageSpec, chain: tuple[str, ...]) -> None:
        head = chain[0]
        if head in spec.builtins or (spec.name == "python" and head in PY_BUILTINS):
            self.stats["calls_builtin"] += 1
            return
        self.stats["calls_unresolved"] += 1
        self.used_member_names[chain[-1]] += 1  # a method name, or the bare name itself

    def _link_ref(self, mid: str, ref: RefSite) -> None:
        """One reference to a name: an edge with the contexts it is used in."""
        self.stats["refs_total"] += 1
        src = self._scope_node(mid, ref.scope)
        res = self._resolve_chain(mid, ref.scope, ref.chain)
        if res is None:
            if len(ref.chain) > 1:
                self.used_member_names[ref.chain[-1]] += 1
            return
        self.stats["refs_resolved"] += 1
        self._reference_via(mid, ref.scope, src, res, ref.line)
        is_pkg = self.graph.node(res.node_id).kind is NodeKind.PACKAGE
        self._typed_use(mid, ref.chain, res, self.graph.node(res.node_id))
        if res.rest and not is_pkg:
            self.used_member_names[res.rest[-1]] += 1
        if res.node_id == src or self._is_own_class(mid, ref.scope, ref.chain, res.node_id):
            return
        e = self.graph.add_edge(Edge(src=src, dst=res.node_id, kind=EdgeKind.REFERENCES, line=ref.line))
        ctxs = e.attrs.setdefault("contexts", [])
        if ref.context not in ctxs:
            ctxs.append(ref.context)
        if is_pkg and res.rest:
            _add_member(e, ".".join(res.rest))

    def _reference_via(self, mid: str, scope: str | None, src: str, res: Resolution, line: int) -> None:
        """`Sub.as_view()` resolves to `View.as_view`, but it names Sub: that is a use of Sub (unless Sub is the caller's own class)."""
        if res.via is not None and res.via != src and res.via != self._enclosing_class(mid, scope):
            e = self.graph.add_edge(Edge(src=src, dst=res.via, kind=EdgeKind.REFERENCES, line=line))
            if "name" not in (ctxs := e.attrs.setdefault("contexts", [])):
                ctxs.append("name")

    def _is_own_class(self, mid: str, scope: str | None, chain: tuple[str, ...], node_id: str) -> bool:
        """True for `self.attr` style partial resolutions that land on the enclosing class."""
        return chain[0] in self.specs[mid].self_names and self._enclosing_class(mid, scope) == node_id

    def _scope_node(self, mid: str, scope: str | None) -> str:
        if scope is None:
            return mid
        table = self.symbols[mid]
        parts = scope.split(".")
        for i in range(len(parts), 0, -1):
            sid = table.get(".".join(parts[:i]))
            if sid is not None:
                return sid
        return mid

    def _enclosing_class(self, mid: str, scope: str | None) -> str | None:
        if scope is None:
            return None
        table = self.symbols[mid]
        parts = scope.split(".")
        for i in range(len(parts), 0, -1):
            sid = table.get(".".join(parts[:i]))
            if sid is not None and self.graph.node(sid).kind is NodeKind.CLASS:
                return sid
        return None

    def _lookup(self, mid: str, scope: str | None, name: str) -> Binding | None:
        if scope:
            parts = scope.split(".")
            for i in range(len(parts), 0, -1):
                b = self.scoped_bindings.get((mid, ".".join(parts[:i])), {}).get(name)
                if b is not None:
                    self._mark_used(mid, name, ".".join(parts[:i]))
                    return b
        b = self.bindings.get(mid, {}).get(name)
        if not isinstance(b, DefBinding) or (mid, name) in self.fallbacks:  # an unresolved import is still used by its name
            self._mark_used(mid, name, None)
        return b

    def _mark_used(self, mid: str, local_name: str, scope: str | None) -> None:
        for rec in self.import_records.get(mid, ()):
            if rec["local_name"] == local_name and rec["scope"] == scope:
                rec["used"] = True

    # --- the resolver ----------------------------------------------------------
    def _resolve_chain(self, mid: str, scope: str | None, chain: tuple[str, ...], depth: int = 0) -> Resolution | None:
        if not chain or chain[0] == "*" or depth > 6:
            return None
        head, rest = chain[0], chain[1:]
        spec = self.specs[mid]
        if head in spec.self_names:
            cls = self._enclosing_class(mid, scope)
            if cls is None:
                return None
            return self._instance_member(cls, rest, depth + 1) if rest else Resolution(cls)
        # nested scopes: a function defined inside the current def/class, then inside each enclosing def (closures see
        # their enclosing function's names; a class body is not a scope its methods see)
        if (sid := self._local(mid, scope, head)) is not None:
            return self._resolve_member(sid, rest, depth + 1)
        if rest and (key := self._local_key(mid, scope, head)) is not None:
            # a name the function assigns is its own local, whatever the module binds under that name
            cls = self._class_of(key)
            return self._instance_member(cls, rest, depth + 1) if cls is not None else None
        b = self._lookup(mid, scope, head)
        if b is None:
            return None
        res = self._resolve_binding(b, rest, depth + 1)
        if rest and res is not None and res.rest == rest and (cls := self._variable_class(res.node_id)) is not None:
            typed = self._instance_member(cls, rest, depth + 1)  # `client.get()` with `client = Client()` at module level
            if typed is not None:
                typed.via = res.node_id  # it still reads `client`
                return typed
        return res

    def _local(self, mid: str, scope: str | None, head: str) -> str | None:
        """A def named `head` inside `scope` or inside an enclosing def."""
        table = self.symbols[mid]
        return next((sid for name in self._scopes(mid, scope) if (sid := table.get(f"{name}.{head}")) is not None), None)

    def _scopes(self, mid: str, scope: str | None):
        """`scope`, then each def enclosing it, innermost first: the scopes whose names code in `scope` sees. A class
        body is not one of them for the methods inside it."""
        parts = scope.split(".") if scope else []
        if parts:
            yield scope
        while len(parts) > 1:
            parts.pop()
            enclosing = self.symbols[mid].get(".".join(parts))
            if enclosing is None or self.graph.node(enclosing).kind not in CALLABLE_KINDS:
                return
            yield ".".join(parts)

    def _resolve_binding(self, b: Binding, rest: tuple[str, ...], depth: int) -> Resolution | None:
        if isinstance(b, DefBinding):
            return self._resolve_member(b.symbol_id, rest, depth)
        if isinstance(b, PackageBinding):
            return Resolution(b.package_id, b.prefix + rest)
        if isinstance(b, ModuleBinding):
            return self._resolve_dotted(b.path, rest, depth)
        if isinstance(b, MemberBinding):
            # a member import may name a submodule or a symbol
            as_module = self._resolve_dotted(f"{b.module_path}.{b.member}", rest, depth, strict=True)
            if as_module is not None:
                return as_module
            target_mid = self.module_index.get(b.module_path)
            if target_mid is None:
                return None
            return self._resolve_member(target_mid, (b.member,) + rest, depth)
        return None

    def _resolve_dotted(self, path: str, rest: tuple[str, ...], depth: int, strict: bool = False) -> Resolution | None:
        parts = tuple(path.split(".")) + rest
        for i in range(len(parts), 0, -1):
            name = ".".join(parts[:i])
            target_mid = self.module_index.get(name)
            if target_mid is not None:
                if strict and i < len(path.split(".")):
                    return None
                return self._resolve_member(target_mid, parts[i:], depth)
        return None

    def _resolve_member(self, node_id: str, rest: tuple[str, ...], depth: int) -> Resolution | None:
        """`rest` looked up inside a module or class; on anything else (a package, a function) it stays unresolved."""
        if not rest:
            return Resolution(node_id)
        if depth > 8:
            return Resolution(node_id, rest)
        kind = self.graph.node(node_id).kind
        if kind is NodeKind.MODULE:
            return self._module_member(node_id, rest, depth)
        if kind is NodeKind.CLASS:
            return self._class_member(node_id, rest, depth)
        return Resolution(node_id, rest)

    def _module_member(self, mid: str, rest: tuple[str, ...], depth: int) -> Resolution:
        """A top-level definition (`default`: JS `import X from './m'` takes the default export), else a name the module
        re-exports through its own imports (package `__init__` style)."""
        table = self.symbols.get(mid, {})
        sid = table.get(rest[0])
        if sid is None and rest[0] == "default":
            sid = next((s for s in table.values() if self.graph.node(s).attrs.get("default_export")), None)
        if sid is not None:
            return self._resolve_member(sid, rest[1:], depth + 1)
        b = self.bindings.get(mid, {}).get(rest[0])
        if b is not None and not isinstance(b, DefBinding) and (res := self._resolve_binding(b, rest[1:], depth + 1)) is not None:
            return res
        return Resolution(mid, rest)

    def _class_member(self, cls: str, rest: tuple[str, ...], depth: int) -> Resolution:
        """A member the class defines, else one an ancestor defines (`via` remembers it was looked up on `cls`)."""
        for child in self.graph.children(cls):
            if child.name == rest[0]:
                return self._resolve_member(child.id, rest[1:], depth + 1)
        for e in self.graph.out_edges(cls, EdgeKind.INHERITS):
            if self.graph.node(e.dst).kind is NodeKind.CLASS and (res := self._resolve_member(e.dst, rest, depth + 1)) is not None and res.exact:
                return Resolution(res.node_id, via=cls)
        return Resolution(cls, rest)

    # ------------------------------------------------------------------ finish
    def _finalize_meta(self) -> None:
        for mid, records in self.import_records.items():
            self.graph.node(mid).attrs["imports"] = records
        langs = Counter(f.language for f in self.files)
        self.graph.meta.update(
            {
                "root": str(self.root),
                "scope": self.scope,
                "files": len(self.files),
                "languages": dict(langs),
                "parse_errors": self.parse_errors,
                "used_member_names": dict(self.used_member_names),
                "read_member_names": dict(self.read_member_names),
                "resolution": dict(self.stats),
                "modules": len(self.facts),
                "symbols": sum(len(t) for t in self.symbols.values()),
            }
        )


def _is_property(node: Node) -> bool:
    return any(d.split(".")[-1] in ("property", "cached_property") for d in node.attrs.get("decorators", ()))


def _add_member(e: Edge, member: str) -> None:
    """Record which member of a package an edge uses, once."""
    members = e.attrs.setdefault("members", [])
    if member and member not in members:
        members.append(member)
