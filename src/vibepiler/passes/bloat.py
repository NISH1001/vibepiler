"""bloat: dependencies nobody imports, imports nobody declares, defensive boilerplate, stubs."""

from __future__ import annotations

from vibepiler.ir.model import CALLABLE_KINDS, EdgeKind, NodeKind
from vibepiler.ir.program import Program
from vibepiler.passes.base import Pass
from vibepiler.passes.diagnostic import Diagnostic, Severity
from vibepiler.passes.entry_points import is_dunder
from vibepiler.passes.manifests import (
    is_js_tool_package,
    is_py_stdlib,
    js_package_for_specifier,
    normalize_py,
    PY_IMPORT_TO_DIST,
    PY_TOOL_PACKAGES,
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
        g = program.graph
        if g.meta.get("scope", "directory") != "directory":
            return []  # one file or a snippet cannot be held to a folder's manifest
        manifests = read_manifests(program.root)
        if not manifests:
            return []
        diags: list[Diagnostic] = []
        # which external packages are actually imported, per language
        imported: dict[str, dict[str, set[str]]] = {"python": {}, "javascript": {}}
        for e in g.edges(EdgeKind.IMPORTS):
            dst = g.node(e.dst)
            src = g.node(e.src)
            if dst.kind is not NodeKind.PACKAGE:
                continue
            lang = "python" if src.language == "python" else "javascript"
            key = normalize_py(PY_IMPORT_TO_DIST.get(dst.name, dst.name)) if lang == "python" else (js_package_for_specifier(dst.name) or dst.name)
            imported[lang].setdefault(key, set()).add(src.file or "")

        for m in manifests:
            used = imported[m.language]
            declared = {**{k: ("dep", v) for k, v in m.dependencies.items()}, **{k: ("dev", v) for k, v in m.dev_dependencies.items()}}
            for name, (group, spec) in sorted(declared.items()):
                key = normalize_py(name) if m.language == "python" else name
                if key in used:
                    continue
                if m.language == "python" and (key in PY_TOOL_PACKAGES or key.startswith(("pytest-", "types-", "sphinx-", "mkdocs-", "flake8-", "django-", "pre-commit"))):
                    continue
                if m.language == "javascript" and is_js_tool_package(name):
                    continue
                # a dependency may be imported under a different top-level name we do not know
                if m.language == "python" and any(normalize_py(u).replace("-", "") == key.replace("-", "") for u in used):
                    continue
                diags.append(
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
            declared_keys = set(declared)
            for key, files in sorted(used.items()):
                if m.language == "python":
                    if is_py_stdlib(key) or key in declared_keys or any(k.replace("-", "") == key.replace("-", "") for k in declared_keys):
                        continue
                    # local module names we could not resolve as imports (e.g. `import utils` from a script dir)
                    local_roots = {x.name.split(".")[0] for x in g.nodes(NodeKind.MODULE)}
                    local_roots |= {a.split(".")[0] for x in g.nodes(NodeKind.MODULE) for a in x.attrs.get("aliases", [])}
                    if key in local_roots:
                        continue
                else:
                    if key in declared_keys or key.startswith("node:"):
                        continue
                diags.append(
                    self.diag(
                        "undeclared-dependency",
                        Severity.WARNING,
                        f"`{key}` is imported in {len(files)} file(s) but not declared in {m.file}",
                        file=sorted(files)[0] if files else m.file,
                        suggestion=f"Add `{key}` to {m.file}, or it is a transitive dependency you rely on by accident.",
                        package=key,
                        files=sorted(files),
                        qualname=key,
                    )
                )
        return diags

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
                line = clause.start_point[0] + 1
                kinds = [s.type for s in stmts]
                if not stmts or all(k in ("pass_statement", "ellipsis") or (k == "expression_statement" and sf.text(s).strip() == "...") for k, s in zip(kinds, stmts)):
                    diags.append(
                        self.diag(
                            "swallowed-exception",
                            Severity.WARNING if broad else Severity.INFO,
                            f"`{n.qualname}` silently swallows {'all exceptions' if broad else 'an exception'} at line {line}",
                            node=n,
                            line=line,
                            suggestion="Handle it, narrow it, or let it propagate. Silent `except: pass` hides real failures.",
                            broad=broad,
                        )
                    )
                    continue
                is_reraise = [sf.text(s).strip() == "raise" or sf.text(s).strip().startswith(("throw ", "throw e", "throw err")) for s in stmts]
                try_node = clause.parent
                siblings = [c for c in try_node.named_children if c.type in ("except_clause", "catch_clause", "finally_clause", "else_clause")] if try_node is not None else [clause]
                if len(stmts) == 1 and is_reraise[0] and len(siblings) == 1:
                    diags.append(
                        self.diag(
                            "redundant-try",
                            Severity.INFO,
                            f"`{n.qualname}` catches and immediately re-raises at line {line}",
                            node=n,
                            line=line,
                            suggestion="Delete the try/except; it changes nothing.",
                        )
                    )
                elif len(stmts) == 2 and is_reraise[1] and spec.is_log_call(stmts[0], sf):
                    diags.append(
                        self.diag(
                            "log-and-reraise",
                            Severity.INFO,
                            f"`{n.qualname}` logs and re-raises at line {line}",
                            node=n,
                            line=line,
                            suggestion="Log once at the boundary that actually handles the error; per-call-site log-and-reraise multiplies noise.",
                        )
                    )
        return diags

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
