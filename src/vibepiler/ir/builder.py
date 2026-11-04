"""IR builder: the semantic-analysis + linking stage.

Takes per-file facts from the language adapters and produces one
`ProgramGraph` for the whole project:

1. every file becomes a module node; every definition a symbol node (contains edges);
2. imports are resolved to internal modules or external packages (imports edges)
   and produce a *binding table* per module (local name -> what it refers to);
3. class bases are resolved (inherits edges);
4. every call site / reference is resolved through the binding table, the
   enclosing class, and module/class member lookup (calls / references edges).

Resolution is name-based and conservative: when a receiver's type is unknown
(e.g. `obj.run()` where `obj` is a local), the call is not guessed; the member
name is recorded in `graph.meta["used_member_names"]` so later passes can stay
conservative too.
"""

from __future__ import annotations

import builtins as _py_builtins
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Union

from vibepiler.config import Config
from vibepiler.frontend.facts import FileFacts, ImportDef
from vibepiler.frontend.languages import get_language
from vibepiler.frontend.languages.base import LanguageSpec
from vibepiler.frontend.loader import STRING_ROOT, SourceFile, discover
from vibepiler.ir.graph import ProgramGraph
from vibepiler.ir.model import CALLABLE_KINDS, Edge, EdgeKind, Node, NodeKind
from vibepiler.ir.program import Program

PY_BUILTINS = frozenset(dir(_py_builtins))


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


def build_program(root: str | Path, config: Config | None = None, files: list[SourceFile] | None = None, scope: str = "directory") -> Program:
    """`scope` is what the caller handed over: "directory", "file" or "string"; passes that judge a whole project read it from `graph.meta`."""
    root = Path(root)
    root = root if root == STRING_ROOT else root.resolve()  # a string program has no place on disk
    cfg = config or Config.load(root)
    if files is None:
        files = discover(root, ignore=cfg.ignore, languages=cfg.languages, use_gitignore=cfg.use_gitignore)
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
        self.symbols: dict[str, dict[str, str]] = {}  # module id -> qualname -> symbol id
        self.bindings: dict[str, dict[str, Binding]] = {}  # module id -> local name -> binding
        self.scoped_bindings: dict[tuple[str, str], dict[str, Binding]] = {}
        self.import_records: dict[str, list[dict]] = {}
        self.used_member_names: Counter[str] = Counter()
        self.stats: Counter[str] = Counter()
        self.parse_errors: list[str] = []

    # ------------------------------------------------------------------ driver
    def build(self) -> Program:
        self._extract()
        self._index_modules()
        self._create_symbols()
        self._resolve_imports()
        self._resolve_inheritance()
        self._resolve_calls_and_refs()
        self._finalize_meta()
        return Program(root=self.root, graph=self.graph, files=self.files, facts=self.facts, config=self.cfg)

    # ------------------------------------------------------------------ phase 1
    def _extract(self) -> None:
        for sf in self.files:
            spec = get_language(sf.language)
            try:
                facts = spec.extract(sf)
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
                    **facts.attrs,
                },
            )
            self.graph.add_node(node)

    def _index_modules(self) -> None:
        # canonical names first so aliases never shadow them
        for mid, facts in self.facts.items():
            self.module_index.setdefault(facts.module_name, mid)
        for mid, facts in self.facts.items():
            for alias in facts.aliases:
                self.module_index.setdefault(alias, mid)

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
            # local definitions bind first
            for qual, sid in self.symbols[mid].items():
                if "." not in qual:
                    table[qual] = DefBinding(sid)
            records: list[dict] = []
            self.import_records[mid] = records
            for imp in facts.imports:
                rec = {
                    "local_name": imp.local_name,
                    "module": imp.module,
                    "name": imp.name,
                    "alias": imp.alias,
                    "line": imp.line,
                    "kind": imp.kind,
                    "scope": imp.scope,
                    "resolved": None,
                    "used": False,
                }
                records.append(rec)
                binding, target = self._resolve_one_import(imp, facts, spec)
                if target is not None:
                    rec["resolved"] = target
                    edge = self.graph.add_edge(Edge(src=mid, dst=target, kind=EdgeKind.IMPORTS, line=imp.line))
                    names = edge.attrs.setdefault("names", [])
                    label = imp.name or imp.module
                    if label and label not in names:
                        names.append(label)
                    if target in self.facts:
                        for parent in spec.implicit_imports(self.facts[target].module_name):
                            pid = self.module_index.get(parent)
                            if pid is not None and pid != mid and not self.graph.has_edge_kind(mid, pid, EdgeKind.IMPORTS):
                                self.graph.add_edge(Edge(src=mid, dst=pid, kind=EdgeKind.IMPORTS, line=imp.line, attrs={"implicit": True}))
                if binding is None:
                    continue
                if imp.kind == "wildcard":
                    if target is not None and target in self.symbols:
                        for qual, sid in self.symbols[target].items():
                            if "." not in qual and self.graph.node(sid).attrs.get("exported", True):
                                table.setdefault(qual, MemberBinding(self.facts[target].module_name, qual))
                    continue
                local = imp.local_name
                if local is None:
                    continue
                if imp.scope:
                    self.scoped_bindings.setdefault((mid, imp.scope), {})[local] = binding
                else:
                    table[local] = binding

    def _resolve_one_import(self, imp: ImportDef, facts: FileFacts, spec: LanguageSpec) -> tuple[Binding | None, str | None]:
        for option in spec.import_targets(imp, facts):
            for cand in option.candidates:
                target = self.module_index.get(cand)
                if target is None:
                    continue
                if option.member is None:
                    return ModuleBinding(option.binding_path or cand), target
                return MemberBinding(cand, option.member), target
        pkg = spec.package_target(imp)
        if pkg is None:
            self.stats["imports_unresolved"] += 1
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

    # ------------------------------------------------------------------ phase 4
    def _resolve_calls_and_refs(self) -> None:
        for mid, facts in self.facts.items():
            spec = self.specs[mid]
            for call in facts.calls:
                self.stats["calls_total"] += 1
                src = self._scope_node(mid, call.scope)
                res = self._resolve_chain(mid, call.scope, call.chain)
                if res is None:
                    head = call.chain[0]
                    if head in spec.builtins or (spec.name == "python" and head in PY_BUILTINS):
                        self.stats["calls_builtin"] += 1
                    else:
                        self.stats["calls_unresolved"] += 1
                        if len(call.chain) > 1:
                            self.used_member_names[call.chain[-1]] += 1
                        else:
                            self.used_member_names[head] += 1
                    continue
                target = self.graph.node(res.node_id)
                if target.kind is NodeKind.PACKAGE:
                    self.stats["calls_external"] += 1
                    e = self.graph.add_edge(Edge(src=src, dst=res.node_id, kind=EdgeKind.CALLS, line=call.line))
                    members = e.attrs.setdefault("members", [])
                    m = ".".join(res.rest)
                    if m and m not in members:
                        members.append(m)
                elif res.exact and target.kind in CALLABLE_KINDS | {NodeKind.CLASS}:
                    self.stats["calls_resolved"] += 1
                    if res.node_id != src:
                        self.graph.add_edge(Edge(src=src, dst=res.node_id, kind=EdgeKind.CALLS, line=call.line))
                    else:
                        self.graph.node(src).attrs["recursive"] = True
                else:
                    # partially resolved: module.CONST(...) or SomeClass.attr(...) or variable(...)
                    self.stats["calls_partial"] += 1
                    if res.rest:
                        self.used_member_names[res.rest[-1]] += 1
                    if res.node_id != src and not self._is_own_class(mid, call.scope, call.chain, res.node_id):
                        self.graph.add_edge(Edge(src=src, dst=res.node_id, kind=EdgeKind.REFERENCES, line=call.line))
            for ref in facts.refs:
                if ref.context == "base":
                    continue  # covered by inherits edges
                self.stats["refs_total"] += 1
                src = self._scope_node(mid, ref.scope)
                res = self._resolve_chain(mid, ref.scope, ref.chain)
                if res is None:
                    if len(ref.chain) > 1:
                        self.used_member_names[ref.chain[-1]] += 1
                    continue
                self.stats["refs_resolved"] += 1
                is_pkg = self.graph.node(res.node_id).kind is NodeKind.PACKAGE
                if res.rest and not is_pkg:
                    self.used_member_names[res.rest[-1]] += 1
                if res.node_id == src or self._is_own_class(mid, ref.scope, ref.chain, res.node_id):
                    continue
                e = self.graph.add_edge(Edge(src=src, dst=res.node_id, kind=EdgeKind.REFERENCES, line=ref.line))
                ctxs = e.attrs.setdefault("contexts", [])
                if ref.context not in ctxs:
                    ctxs.append(ref.context)
                if self.graph.node(res.node_id).kind is NodeKind.PACKAGE and res.rest:
                    members = e.attrs.setdefault("members", [])
                    m = ".".join(res.rest)
                    if m not in members:
                        members.append(m)

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
        if b is not None and not isinstance(b, DefBinding):
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
            return self._resolve_member(cls, rest, depth + 1)
        # nested scope: a function defined inside the current def/class
        if scope:
            local_qual = f"{scope}.{head}"
            sid = self.symbols[mid].get(local_qual)
            if sid is not None:
                return self._resolve_member(sid, rest, depth + 1)
        b = self._lookup(mid, scope, head)
        if b is None:
            return None
        return self._resolve_binding(b, rest, depth + 1)

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
        if not rest:
            return Resolution(node_id)
        if depth > 8:
            return Resolution(node_id, rest)
        node = self.graph.node(node_id)
        if node.kind is NodeKind.MODULE:
            sid = self.symbols.get(node_id, {}).get(rest[0])
            if sid is None and rest[0] == "default":  # JS `import X from './m'` -> the module's default export
                sid = next((s for s in self.symbols.get(node_id, {}).values() if self.graph.node(s).attrs.get("default_export")), None)
            if sid is not None:
                return self._resolve_member(sid, rest[1:], depth + 1)
            # re-export through that module's own imports (package __init__ style)
            b = self.bindings.get(node_id, {}).get(rest[0])
            if b is not None and not isinstance(b, DefBinding):
                res = self._resolve_binding(b, rest[1:], depth + 1)
                if res is not None:
                    return res
            return Resolution(node_id, rest)
        if node.kind is NodeKind.CLASS:
            for child in self.graph.children(node_id):
                if child.name == rest[0]:
                    return self._resolve_member(child.id, rest[1:], depth + 1)
            for e in self.graph.out_edges(node_id, EdgeKind.INHERITS):
                if self.graph.node(e.dst).kind is NodeKind.CLASS:
                    res = self._resolve_member(e.dst, rest, depth + 1)
                    if res is not None and res.exact:
                        return res
            return Resolution(node_id, rest)
        if node.kind is NodeKind.PACKAGE:
            return Resolution(node_id, rest)
        return Resolution(node_id, rest)

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
                "resolution": dict(self.stats),
                "modules": len(self.facts),
                "symbols": sum(len(t) for t in self.symbols.values()),
            }
        )
