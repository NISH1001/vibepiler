"""The terminal backend: everything the CLI prints, in one place.

Phase lines, the findings, the `size`-style line, the summary table, the missed-optimization
remarks and the change since a baseline. The other backends write files; this one writes to you.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.table import Table

from vibepiler.backend.common import size_parts
from vibepiler.ir.program import Program
from vibepiler.passes.diagnostic import Severity

console = Console()
err_console = Console(stderr=True)

SEV_STYLE = {Severity.ERROR: "bold red", Severity.WARNING: "yellow", Severity.INFO: "cyan"}


def print_skipped(program: Program) -> None:
    for key, m in program.metrics.items():
        if isinstance(m, dict) and m.get("skipped"):
            console.print(f"      [yellow]skipped[/yellow] {key.replace('_', '-')}: {m['skipped']}")


def print_findings(program: Program, min_sev: Severity, max_print: int, report: Path) -> None:
    shown = [d for d in program.diagnostics if d.severity >= min_sev]
    if not shown:
        return
    console.print()
    for d in shown[:max_print]:
        style = SEV_STYLE[d.severity]
        console.print(f"[{style}]{d.severity.label:7}[/{style}] [dim]{d.pass_name}[/dim]/{d.code}  [cyan]{d.location}[/cyan]: {d.message}")
        if d.suggestion:
            console.print(f"        [dim italic]{d.suggestion}[/dim italic]")
    if len(shown) > max_print:
        console.print(f"[dim]... {len(shown) - max_print} more; see {report} or ir.json[/dim]")


def print_phase(n: int, name: str, msg: str) -> None:
    console.print(f"[dim][{n}/4][/dim] [bold]{name:9}[/bold] {msg}")


def size_line(b: dict) -> str:
    p = size_parts(b)
    return (
        f"[bold]waste[/bold] {p['waste']} [dim]({p['lines']})[/dim]   [bold]tangle[/bold] {p['tangle']}" + (f" [dim]({p['reasons']})[/dim]" if p["reasons"] else "")
        + f"   [bold]bloat[/bold] {p['bloat']}" + (f"   [dim]\\[{p['stamp']}][/dim]" if p["stamp"] else "")
    )


def print_remarks(program: Program, limit: int = 60) -> None:
    b = program.metrics.get("bloat_score", {})
    plan = b.get("plan", [])
    console.print()
    console.print(f"[bold]missed-optimization remarks[/bold] [dim]({len(plan)} edits; {b.get('removable_loc', 0):.0f} lines shown by analysis" + (f", {b['model_loc']:.0f} more if the semantic pairs merge" if b.get("model_loc") else "") + "; apply the top one, re-run, repeat)[/dim]")
    for p in plan[:limit]:
        where = f"{p['file']}:{p['line']}" if p.get("line") else (p.get("file") or "")
        console.print(f"  [dim]remark:[/dim] [bold]{p['action']:8}[/bold] {p['code']:24} [cyan]{p['qualname'] or ''}[/cyan] [dim]({where})[/dim]  {p['lines']:.0f} lines [dim]{p['confidence']}[/dim]")
    if len(plan) > limit:
        console.print(f"  [dim]... {len(plan) - limit} more in report.md[/dim]")


def print_baseline(program: Program, path: Path) -> None:
    import json

    try:
        prev = json.loads(Path(path).read_text(encoding="utf-8"))["metrics"]["bloat_score"]
    except (OSError, ValueError, KeyError) as exc:
        console.print(f"[yellow]baseline {path}: no bloat score in it ({type(exc).__name__})[/yellow]")
        return
    cur = program.metrics.get("bloat_score")
    if not cur:
        return
    dl = cur["removable_loc"] - prev["removable_loc"]
    db = cur["score"] - prev["score"]
    if abs(dl) < 0.5 and abs(db) < 0.005 and abs(cur["tangle"] - prev["tangle"]) < 0.005:
        console.print("[bold]since baseline[/bold]: no change")
        return
    console.print(
        f"[bold]since baseline[/bold]: waste {prev['removable_loc']:.0f} → {cur['removable_loc']:.0f} lines ({dl:+.0f}), "
        f"tangle {prev['tangle']:.2f} → {cur['tangle']:.2f}, bloat {prev['score']:.0%} → {cur['score']:.0%} ({db * 100:+.0f} pts)"
    )


def print_summary(program: Program) -> None:
    m = program.metrics
    if "bloat_score" in m:
        console.print()
        console.print(size_line(m["bloat_score"]))
    table = Table(title="what can go", show_lines=False, title_style="bold", header_style="dim")
    table.add_column("category")
    table.add_column("found")
    table.add_column("action", style="dim")
    if "dead_code" in m:
        table.add_row("dead code", f"{m['dead_code']['symbols']} symbols, {m['dead_code']['loc']} lines", "delete")
    if "duplicates" in m:
        table.add_row("duplicate functions", f"{m['duplicates']['exact_groups']} groups ({m['duplicates']['removable_loc']} lines), {m['duplicates']['near_pairs']} near pairs", "merge")
    if "wrappers" in m:
        table.add_row("pass-through wrappers", f"{m['wrappers']['count']} ({m['wrappers']['chains']} chains)", "inline")
    if "abstraction" in m:
        table.add_row("needless abstraction", str(m["abstraction"]["count"]), "flatten")
    if "bloat" in m:
        b = m["bloat"]
        table.add_row("dependency bloat", f"{b['unused_dependencies']} unused, {b['undeclared_dependencies']} undeclared", "prune")
        table.add_row("defensive boilerplate", f"{b['swallowed_exceptions']} swallowed, {b['redundant_try']} redundant try, {b['stubs']} stubs", "remove")
    if "unused_imports" in m:
        table.add_row("unused imports", str(m["unused_imports"]["count"]), "remove")
    if "cycles" in m:
        table.add_row("import cycles", str(m["cycles"]["count"]), "break")
    if table.row_count:
        console.print()
        console.print(table)
