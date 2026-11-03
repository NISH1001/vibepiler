"""Markdown report."""

from __future__ import annotations

from collections import defaultdict

from vibepiler.backend.common import summarize
from vibepiler.ir.program import Program
from vibepiler.passes.diagnostic import Diagnostic
from vibepiler.passes.registry import all_passes


def _table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def emit_markdown(program: Program, max_per_pass: int = 40) -> str:
    s = summarize(program)
    m = program.metrics
    lines = [f"# Vibepiler report for `{program.root.name}`", ""]
    lines += ["## Summary", ""]
    lines.append(
        _table(
            ["files", "modules", "symbols", "lines", "external packages", "edges", "errors", "warnings", "infos"],
            [[s["files"], s["modules"], s["symbols"], s["loc"], s["packages"], s["edges"], s["severity"]["error"], s["severity"]["warning"], s["severity"]["info"]]],
        )
    )
    res = s["resolution"]
    if res:
        total = res.get("calls_total", 0) or 1
        resolved = res.get("calls_resolved", 0) + res.get("calls_external", 0) + res.get("calls_builtin", 0)
        lines += ["", f"Call resolution: {resolved}/{total} call sites linked ({resolved / total:.0%}); {res.get('calls_unresolved', 0)} dynamic/unknown receivers."]
    b = m.get("bloat_score")
    if b:
        conf = ", ".join(f"{k} {v:.0f}" for k, v in b.get("waste_by_confidence", {}).items() if k != "model") + (f"; +{b['model_loc']:.0f} more if the semantic pairs merge" if b.get("model_loc") else "") + (f"; {b['unverified_loc']:.0f} lines are alive only because an unresolved call used their name" if b.get("unverified_loc") else "")
        reasons = ", ".join(f"{r['code']} ×{r['count']}" for r in b.get("tangle_reasons", [])[:4])
        c = b.get("conditions", {})
        lines += ["", f"**waste {b['waste']:.0%}** ({b['removable_loc']:.0f} of {b['loc']} source lines could go" + (f"; {conf}" if conf else "") + f") · **tangle {b['tangle']:.2f}**" + (f" ({reasons})" if reasons else "") + f" · **bloat {b['score']:.0%}** — measured at -O{c.get('opt_level')}" + (f", {c['linked']:.0%} of call sites linked" if c.get("linked") is not None else "") + (f", model `{c['model']}`" if c.get("model") else "") + f". `{b['formula']}`." + (f" Tests ({b['tests']['loc']} lines) are scored apart: waste {b['tests']['waste']:.0%}." if b.get("tests", {}).get("loc") else ""), ""]
        lines += [_table(["folder", "modules", "lines", "bloat", "waste", "tangle"], [[f"`{f['path']}`", str(f["modules"]), str(f["loc"]), f"{f['score']:.0%}", f"{f['waste']:.0%}", f"{f['tangle']:.2f}"] for f in b["folders"][:15]])]
    lines += ["", "### What can go", ""]
    rows = []
    if "dead_code" in m:
        rows.append(["dead code", f"{m['dead_code']['symbols']} symbols, {m['dead_code']['loc']} lines", "delete"])
    if "duplicates" in m:
        rows.append(["duplicate functions", f"{m['duplicates']['exact_groups']} groups, {m['duplicates']['removable_loc']} removable lines; {m['duplicates']['near_pairs']} near-duplicate pairs", "merge"])
    if "wrappers" in m:
        rows.append(["pass-through wrappers", f"{m['wrappers']['count']} functions, {m['wrappers']['chains']} chains", "inline"])
    if "abstraction" in m:
        rows.append(["needless abstraction", f"{m['abstraction']['count']} findings", "flatten"])
    if "bloat" in m:
        b = m["bloat"]
        rows.append(["dependency bloat", f"{b['unused_dependencies']} unused, {b['undeclared_dependencies']} undeclared", "prune manifest"])
        rows.append(["defensive boilerplate", f"{b['swallowed_exceptions']} swallowed exceptions, {b['redundant_try']} redundant try blocks, {b['stubs']} stubs", "remove"])
    if "unused_imports" in m:
        rows.append(["unused imports", str(m["unused_imports"]["count"]), "remove"])
    if "cycles" in m:
        rows.append(["import cycles", f"{m['cycles']['count']} (largest {m['cycles']['largest']} modules)", "break"])
    if rows:
        lines.append(_table(["category", "found", "action"], rows))

    if "entry_points" in m:
        ep = m["entry_points"]
        lines += ["", "## Entry points", "", f"{ep['count']} roots; {ep['reachable_symbols']}/{ep['total_symbols']} symbols reachable from them.", ""]
        lines.append(_table(["root", "kind", "location"], [[f"`{r['qualname']}`", ", ".join(r["kinds"]), f"{r['file']}:{r['line']}" if r["line"] else r["file"]] for r in ep["roots"][:60]]))
        if ep["unreachable_modules"]:
            lines += ["", "Modules never imported or executed: " + ", ".join(f"`{f}`" for f in ep["unreachable_modules"][:30])]

    if "hotspots" in m:
        hs = m["hotspots"]
        lines += ["", "## Hotspots", "", "Modules by fan-in + fan-out:", ""]
        lines.append(_table(["module", "fan-in", "fan-out", "external deps", "lines"], [[f"`{r['name']}`", r["fan_in"], r["fan_out"], r["external_deps"], r["loc"]] for r in hs["modules"]]))
        lines += ["", "Most central functions (PageRank over the call graph):", ""]
        lines.append(_table(["function", "callers", "callees", "pagerank"], [[f"`{r['qualname']}`", r["callers"], r["callees"], r["pagerank"]] for r in hs["functions"]]))

    if "complexity" in m and m["complexity"]["top"]:
        c = m["complexity"]
        lines += ["", "## Complexity", "", f"{c['functions']} functions, average cyclomatic complexity {c['avg']}, max {c['max']}.", ""]
        lines.append(_table(["function", "complexity", "lines", "nesting"], [[f"`{r['qualname']}`", r["complexity"], r["loc"], r["nesting"]] for r in c["top"]]))

    by_pass: dict[str, list[Diagnostic]] = defaultdict(list)
    for d in program.diagnostics:
        by_pass[d.pass_name].append(d)
    c = program.metrics.get("concepts")
    if c and c.get("concepts"):
        lines += ["", "## Concepts (-O3)", "", f"{len(c['concepts'])} concepts cover {c['clustered']} of {c['symbols']} functions, grouped by meaning with `{c['model']}`, regardless of where they live. A concept spread over several modules with no clear home is a scattered concern.", ""]
        rows = [[f"`{x['label']}`", str(x["size"]), str(len(x["modules"])), f"`{program.graph.node(x['exemplar']).qualname}`", "scattered" if x["scattered"] else ""] for x in c["concepts"][:30]]
        lines += [_table(["concept", "functions", "modules", "exemplar", ""], rows)]
    if b and b.get("plan"):
        lines += ["", "## Elimination plan", "", "Findings ordered by lines freed. Apply the top one, re-run, repeat: the score is a fixpoint iteration.", ""]
        lines += [_table(["lines", "action", "confidence", "finding", "symbol", "where"], [[f"{p['lines']:.0f}", p["action"], p["confidence"], p["code"], f"`{p['qualname']}`" if p["qualname"] else "", f"{p['file']}:{p['line']}" if p["line"] else (p["file"] or "")] for p in b["plan"][:40]])]
    lines += ["", "## Findings by pass", ""]
    for p in all_passes():
        diags = by_pass.get(p.name)
        if not diags:
            continue
        lines += [f"### {p.name} ({len(diags)}) — {p.analogue}", "", f"_{p.description}_", ""]
        diags = sorted(diags, key=lambda d: (-int(d.severity), d.file or "", d.line or 0))
        for d in diags[:max_per_pass]:
            loc = f"`{d.location}`" if d.file else ""
            lines.append(f"- **{d.severity.label}** `{d.code}` {loc}: {d.message}" + (f"  \n  _{d.suggestion}_" if d.suggestion else ""))
        if len(diags) > max_per_pass:
            lines.append(f"- … {len(diags) - max_per_pass} more (see ir.json)")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
