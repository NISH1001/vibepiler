"""bloat: dependencies nobody imports, imports nobody declares, defensive boilerplate, stubs."""

from __future__ import annotations

from pathlib import PurePosixPath

from vibepiler.ir.model import CALLABLE_KINDS, EdgeKind, NodeKind
from vibepiler.ir.program import Program
from vibepiler.passes.base import Pass
from vibepiler.passes.diagnostic import Diagnostic, Severity
from vibepiler.passes.entry_points import is_dunder
from vibepiler.passes.manifests import (
    script_dependencies,
    is_js_tool_package,
    is_py_stdlib,
    js_package_for_specifier,
    normalize_py,
    same_py_package,
    PY_IMPORT_TO_DIST,
    PY_TOOL_PACKAGES,
    governing,
    read_manifests,
)


class BloatPass(Pass):
    name = "bloat"
    description = "Declared-but-unused dependencies, undeclared imports, swallowed/re-raised exceptions, stub functions."
    analogue = "link-time pruning / dead-store elimination"
    cost = 1

    def run(self, program: Program) -> list[Diagnostic]:
        diags: list[Diagnostic] = []
        diags += self._dependencies(program)
        diags += self._exceptions(program)
        diags += self._stubs(program)
        program.metrics["bloat"] = {
            "unused_dependencies": sum(1 for d in diags if d.code == "unused-dependency"),
            "undeclared_dependencies": sum(1 for d in diags if d.code == "undeclared-dependency"),
            "swallowed_exceptions": sum(1 for d in diags if d.code == "swallowed-exception"),
            "redundant_try": sum(1 for d in diags if d.code == "redundant-try"),
            "stubs": sum(1 for d in diags if d.code == "stub-function"),
        }
        return diags

    # --- dependencies ----------------------------------------------------------
    def _dependencies(self, program: Program) -> list[Diagnostic]:
        """Each manifest against the imports of the files it governs: declared but never imported, imported but never declared."""
        g = program.graph
        if g.meta.get("scope", "directory") != "directory":
            return []  # one file or a snippet cannot be held to a folder's manifest
        files = [n.file for n in g.nodes(NodeKind.MODULE) if n.file]
        manifests = read_manifests(program.root, {"", *(str(p) for f in files for p in PurePosixPath(f).parents if str(p) != ".")})
        if not manifests:
            return []
        imported = self._imported_packages(g)
        # a standalone script may declare its own dependencies (PEP 723); those imports owe the manifest nothing
        inline = {f.file.rel: deps for f in program.facts.values() if f.file.language == "python" and (deps := script_dependencies(f.file.source.decode("utf-8", "replace")))}
        local_roots = {x.name.split(".")[0] for x in g.nodes(NodeKind.MODULE)} | {a.split(".")[0] for x in g.nodes(NodeKind.MODULE) for a in x.attrs.get("aliases", [])}
        owners = {(lang, f): governing(manifests, lang, f) for lang, by_key in imported.items() for fs in by_key.values() for f in fs}
        diags: list[Diagnostic] = []
        reported: set[tuple[str, str]] = set()
        for m in manifests:
            # only the imports of files this manifest governs: a frontend/ package.json answers for frontend/ alone
            used = {k: own for k, fs in imported[m.language].items() if (own := {f for f in fs if any(o is m for o in owners[(m.language, f)])})}
            diags += self._unused(m, used)
            if (m.language, m.dir) in reported:
                continue  # pyproject.toml, requirements.txt and test_requirements.txt in one folder declare together
            reported.add((m.language, m.dir))
            siblings = [o for o in manifests if o.language == m.language and o.dir == m.dir]
            declared = {k for o in siblings for k in self._declared(o)}
            diags += self._undeclared(m, used, declared, inline, local_roots)
        return diags

    @staticmethod
    def _imported_packages(g) -> dict[str, dict[str, set[str]]]:  # noqa: ANN001
        """Language -> external package (by its distribution name) -> the files that import it."""
        imported: dict[str, dict[str, set[str]]] = {"python": {}, "javascript": {}}
        for e in g.edges(EdgeKind.IMPORTS):
            dst, src = g.node(e.dst), g.node(e.src)
            if dst.kind is not NodeKind.PACKAGE:
                continue
            lang = "python" if src.language == "python" else "javascript"
            if lang == "python" and is_py_stdlib(dst.name.split(".")[0]):
                continue  # before the name is lowercased: `cProfile` is the standard library, `cprofile` is not
            key = normalize_py(PY_IMPORT_TO_DIST.get(dst.name, dst.name)) if lang == "python" else (js_package_for_specifier(dst.name) or dst.name)
            imported[lang].setdefault(key, set()).add(src.file or "")
        return imported

    @staticmethod
    def _declared(m) -> dict[str, tuple[str, str]]:  # noqa: ANN001
        return {**{k: ("dep", v) for k, v in m.dependencies.items()}, **{k: ("dev", v) for k, v in m.dev_dependencies.items()}}

    @staticmethod
    def _needs_no_import(m, name: str, key: str, used) -> bool:  # noqa: ANN001
        """A tool or plugin (pytest, eslint, ...), or a dependency imported under a top-level name we do not know."""
        if m.language == "javascript":
            return is_js_tool_package(name)
        if key in PY_TOOL_PACKAGES or key.startswith(("pytest-", "types-", "sphinx-", "mkdocs-", "flake8-", "django-", "pre-commit")):
            return True
        return any(same_py_package(key, normalize_py(u)) for u in used)

    def _unused(self, m, used) -> list[Diagnostic]:  # noqa: ANN001
        out = []
        for name, (group, spec) in sorted(self._declared(m).items()):
            key = normalize_py(name) if m.language == "python" else name
            if key in used or self._needs_no_import(m, name, key, used):
                continue
            out.append(
                self.diag(
                    "unused-dependency",
                    Severity.WARNING if group == "dep" else Severity.INFO,
                    f"`{name}` is declared in {m.file} but never imported",
                    file=m.file,
                    suggestion=f"Remove `{name}` from {m.file} (or it is a tool/plugin: add it to the ignore list).",
                    package=name,
                    group=group,
                    spec=spec,
                    qualname=name,
                )
            )
        return out

    @staticmethod
    def _owes(m, key: str, declared: set[str], local_roots: set[str]) -> bool:  # noqa: ANN001
        """Must this manifest declare `key`? Not the standard library, not a local module, not what it already declares."""
        if m.language != "python":
            return key not in declared and not key.startswith("node:")
        if is_py_stdlib(key) or key in local_roots:  # local: a module name we could not resolve (`import utils` from a script dir)
            return False
        return key not in declared and not any(same_py_package(k, key) for k in declared)

    def _undeclared(self, m, used, declared: set[str], inline: dict[str, set[str]], local_roots: set[str]) -> list[Diagnostic]:  # noqa: ANN001
        """Imports no manifest in m's folder declares; `declared` is what they declare together."""
        out = []
        for key, importers in sorted(used.items()):
            importers = {f for f in importers if key not in inline.get(f, ())}
            if not importers or not self._owes(m, key, declared, local_roots):
                continue
            out.append(
                self.diag(
                    "undeclared-dependency",
                    Severity.WARNING,
                    f"`{key}` is imported in {len(importers)} file(s) but not declared in {m.file}",
                    file=sorted(importers)[0],
                    suggestion=f"Add `{key}` to {m.file}, or it is a transitive dependency you rely on by accident.",
                    package=key,
                    files=sorted(importers),
                    qualname=key,
                )
            )
        return out

    # --- exceptions ------------------------------------------------------------
    def _exceptions(self, program: Program) -> list[Diagnostic]:
        diags: list[Diagnostic] = []
        for n in program.graph.nodes(CALLABLE_KINDS):
            d = program.symbol_def(n)
            if d is None or d.node is None:
                continue
            spec = program.spec_for(n)
            sf = program.facts[n.module].file  # type: ignore[index]
            for clause, stmts, broad in spec.except_clauses(d.node, sf):
                if (diag := self._judge_handler(n, spec, sf, clause, stmts, broad)) is not None:
                    diags.append(diag)
        return diags

    def _judge_handler(self, n, spec, sf, clause, stmts, broad: bool) -> Diagnostic | None:  # noqa: ANN001
        """One except/catch clause: empty (swallowed), a bare re-raise as the only clause (redundant), or log then re-raise."""
        line = clause.start_point[0] + 1
        if all(s.type in ("pass_statement", "ellipsis") or (s.type == "expression_statement" and sf.text(s).strip() == "...") for s in stmts):
            return self.diag(
                "swallowed-exception",
                Severity.WARNING if broad else Severity.INFO,
                f"`{n.qualname}` silently swallows {'all exceptions' if broad else 'an exception'} at line {line}",
                node=n,
                line=line,
                suggestion="Handle it, narrow it, or let it propagate. Silent `except: pass` hides real failures.",
                broad=broad,
            )
        if len(stmts) == 1 and _reraises(stmts[0], sf) and _only_handler(clause):
            return self.diag("redundant-try", Severity.INFO, f"`{n.qualname}` catches and immediately re-raises at line {line}", node=n, line=line, suggestion="Delete the try/except; it changes nothing.")
        if len(stmts) == 2 and _reraises(stmts[1], sf) and spec.is_log_call(stmts[0], sf):
            return self.diag(
                "log-and-reraise",
                Severity.INFO,
                f"`{n.qualname}` logs and re-raises at line {line}",
                node=n,
                line=line,
                suggestion="Log once at the boundary that actually handles the error; per-call-site log-and-reraise multiplies noise.",
            )
        return None

    # --- stubs -----------------------------------------------------------------
    def _stubs(self, program: Program) -> list[Diagnostic]:
        diags: list[Diagnostic] = []
        g = program.graph
        for n in g.nodes(CALLABLE_KINDS):
            if is_dunder(n.name) or any("abstract" in d or "overload" in d for d in n.attrs.get("decorators", [])):
                continue
            parent = g.parent(n.id)
            if parent is not None and parent.kind is NodeKind.CLASS and any(b.split(".")[-1] in ("Protocol", "ABC") for b in parent.attrs.get("bases", [])):
                continue
            d = program.symbol_def(n)
            if d is None or d.node is None or n.attrs.get("decorators"):
                continue  # decorated stubs (callbacks, abstract methods) are placeholders on purpose
            spec = program.spec_for(n)
            sf = program.facts[n.module].file  # type: ignore[index]
            if spec.is_stub_body(spec.body_statements(d.node, sf), sf):
                diags.append(
                    self.diag(
                        "stub-function",
                        Severity.INFO,
                        f"`{n.qualname}` is a stub (empty body / NotImplementedError)",
                        node=n,
                        suggestion="Implement it or delete it; placeholders left by a coding agent are the first thing to prune.",
                    )
                )
        return diags


def _reraises(stmt, sf) -> bool:  # noqa: ANN001
    """`raise` alone, or JS `throw e` of what was caught."""
    text = sf.text(stmt).strip()
    return text == "raise" or text.startswith(("throw ", "throw e", "throw err"))


def _only_handler(clause) -> bool:  # noqa: ANN001
    """No other except, catch, else or finally beside this clause on its try."""
    try_node = clause.parent
    return try_node is None or sum(c.type in ("except_clause", "catch_clause", "finally_clause", "else_clause") for c in try_node.named_children) == 1
