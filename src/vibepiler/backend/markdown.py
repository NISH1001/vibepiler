"""Markdown report."""

from __future__ import annotations

from collections import defaultdict

from vibepiler.backend.common import liveness_parts, project_name, size_parts, summarize
from vibepiler.ir.program import Program
from vibepiler.passes.diagnostic import Diagnostic
from vibepiler.passes.measures import METRICS
from vibepiler.passes.metric import Share
from vibepiler.passes.registry import all_passes


def _table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def _summary(program: Program) -> list[str]:
    s = summarize(program)
    lines = ["## Summary", ""]
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
    return lines


def _size(m: dict) -> list[str]:
    """The size line and the per-folder table, like `size` on a binary."""
    b = m["bloat_score"]
    p, lv = size_parts(b), liveness_parts(m.get("liveness_score"))
    live = f" · **live {lv['live']}** ({lv['defs']})" if lv else ""
    line = f"**waste {p['waste']}** ({p['lines']}) · **tangle {p['tangle']}**" + (f" ({p['reasons']})" if p["reasons"] else "") + f" · **bloat {p['bloat']}**" + live + (f" — measured at {p['stamp']}" if p["stamp"] else "") + f". `{b['formula']}`." + (f" {p['tests'].capitalize()}." if p["tests"] else "")
    live_by = {f["path"]: f for f in (m.get("liveness_score") or {}).get("folders", [])}
    rows = [[f"`{f['path']}`", str(f["modules"]), str(f["loc"]), f"{f['score']:.0%}", f"{f['waste']:.0%}", f"{f['tangle']:.2f}", f"{live_by[f['path']]['value']:.1%}" if f["path"] in live_by else ""] for f in b["folders"][:15]]
    return ["", line, "", _table(["folder", "modules", "lines", "bloat", "waste", "tangle", "live"], rows)]


def _what_can_go(m: dict) -> list[str]:
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
    return ["", "### What can go", "", _table(["category", "found", "action"], rows)] if rows else ["", "### What can go", ""]


def _entry_points(ep: dict) -> list[str]:
    lines = ["", "## Entry points", "", f"{ep['count']} roots; {ep['reachable_symbols']}/{ep['total_symbols']} symbols reachable from them.", ""]
    lines.append(_table(["root", "kind", "location"], [[f"`{r['qualname']}`", ", ".join(r["kinds"]), f"{r['file']}:{r['line']}" if r["line"] else r["file"]] for r in ep["roots"][:60]]))
    if ep["unreachable_modules"]:
        lines += ["", "Modules never imported or executed: " + ", ".join(f"`{f}`" for f in ep["unreachable_modules"][:30])]
    return lines


def _hotspots(hs: dict) -> list[str]:
    lines = ["", "## Hotspots", "", "Modules by fan-in + fan-out:", ""]
    lines.append(_table(["module", "fan-in", "fan-out", "external deps", "lines"], [[f"`{r['name']}`", r["fan_in"], r["fan_out"], r["external_deps"], r["loc"]] for r in hs["modules"]]))
    lines += ["", "Most central functions (PageRank over the call graph):", ""]
    lines.append(_table(["function", "callers", "callees", "pagerank"], [[f"`{r['qualname']}`", r["callers"], r["callees"], r["pagerank"]] for r in hs["functions"]]))
    return lines


def _complexity(c: dict) -> list[str]:
    lines = ["", "## Complexity", "", f"{c['functions']} functions, the hardest to follow first; average cyclomatic complexity {c['avg']}, max {c['max']}.", ""]
    lines.append(_table(["function", "cognitive", "cyclomatic", "lines", "nesting"], [[f"`{r['qualname']}`", r["cognitive"], r["complexity"], r["loc"], r["nesting"]] for r in c["top"]]))
    return lines


READING = (
    "Each is 0–1, read one of two ways. A **share** (typed, documented) is a plain percentage: how much of the code has it. "
    "An **excess** score (cognitive, cyclomatic, mi) is the share of complexity past the limit, weighted by lines: a function at "
    "twice the limit scores 0.5, one within it 0, so 0.4 is not \"40% of functions\". How widespread it is reads beside it, as the "
    "functions and the share of lines over the limit; how bad, as the worst raw number."
)


def _fmt(cls: type, v: float) -> str:
    """A share as a percent; an excess score as a decimal, so it is not read as a share of functions."""
    return f"{v:.1%}" if issubclass(cls, Share) else f"{v:.2f}"


def _metrics(m: dict) -> list[str]:
    """Every measure with its raw numbers, then each one per folder (the same folders as the size table)."""
    have = [(cls, cls.of(m[f"{cls.key}_score"])) for cls in METRICS if m.get(f"{cls.key}_score")]
    if not have:
        return []
    rows = [[f"**{cls.label}**", _fmt(cls, x.value), str(x).split(" (", 1)[1].rstrip(")"), "lower" if cls.better == "low" else "higher", f"`{cls.formula}`"] for cls, x in have]
    lines = ["", "### Metrics", "", READING, "", _table(["metric", "value", "raw", "better", "formula"], rows)]
    by = {cls.key: {f["path"]: f["value"] for f in m[f"{cls.key}_score"]["folders"]} for cls, _ in have}
    folders = [f["path"] for f in (m.get("bloat_score") or {}).get("folders", [])][:15] or list(next(iter(by.values())))[:15]
    lines += ["", _table(["folder", *(cls.label for cls, _ in have)], [[f"`{p}`", *(_fmt(cls, by[cls.key][p]) if p in by[cls.key] else "" for cls, _ in have)] for p in folders])]
    return lines


def _concepts(program: Program, c: dict) -> list[str]:
    lines = ["", "## Concepts (-O3)", "", f"{len(c['concepts'])} concepts cover {c['clustered']} of {c['symbols']} functions, grouped by meaning with `{c['model']}`, regardless of where they live. A concept spread over several modules with no clear home is a scattered concern.", ""]
    rows = [[f"`{x['label']}`", str(x["size"]), str(len(x["modules"])), f"`{program.graph.node(x['exemplar']).qualname}`", "scattered" if x["scattered"] else ""] for x in c["concepts"][:30]]
    return lines + [_table(["concept", "functions", "modules", "exemplar", ""], rows)]


def _plan(plan: list[dict]) -> list[str]:
    rows = [[f"{p['lines']:.0f}", p["action"], p["confidence"], p["code"], f"`{p['qualname']}`" if p["qualname"] else "", f"{p['file']}:{p['line']}" if p["line"] else (p["file"] or "")] for p in plan[:40]]
    return ["", "## Elimination plan", "", "Findings ordered by lines freed. Apply the top one, re-run, repeat: the score is a fixpoint iteration.", "", _table(["lines", "action", "confidence", "finding", "symbol", "where"], rows)]


def _findings(program: Program, max_per_pass: int) -> list[str]:
    by_pass: dict[str, list[Diagnostic]] = defaultdict(list)
    for d in program.diagnostics:
        by_pass[d.pass_name].append(d)
    lines = ["", "## Findings by pass", ""]
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
    return lines


def emit_markdown(program: Program, max_per_pass: int = 40) -> str:
    m = program.metrics
    b = m.get("bloat_score")
    lines = [f"# Vibepiler report for `{project_name(program)}`", ""]
    lines += _summary(program)
    if b:
        lines += _size(m)
    lines += _metrics(m)
    lines += _what_can_go(m)
    if "entry_points" in m:
        lines += _entry_points(m["entry_points"])
    if "hotspots" in m:
        lines += _hotspots(m["hotspots"])
    if "complexity" in m and m["complexity"]["top"]:
        lines += _complexity(m["complexity"])
    c = m.get("concepts")
    if c and c.get("concepts"):
        lines += _concepts(program, c)
    if b and b.get("plan"):
        lines += _plan(b["plan"])
    lines += _findings(program, max_per_pass)
    return "\n".join(lines).rstrip() + "\n"
