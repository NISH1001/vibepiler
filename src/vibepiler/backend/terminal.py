"""The terminal backend: everything the CLI prints, in one place.

Phase lines, the findings, the `size`-style line, the summary table, the missed-optimization
remarks and the change since a baseline. The other backends write files; this one writes to you.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from vibepiler.backend.common import liveness_parts, size_parts
from vibepiler.ir.program import Program
from vibepiler.passes.diagnostic import Severity
from vibepiler.passes.measures import METRICS
from vibepiler.progress import mem_text

console = Console()
err_console = Console(stderr=True)

SEV_STYLE = {Severity.ERROR: "bold red", Severity.WARNING: "yellow", Severity.INFO: "cyan"}


class PassLog:
    """A loguru sink for what the running pass is doing, under its line: with `verbose`, every step dimmed; otherwise
    one live counter that rewrites itself and goes when the pass finishes."""

    def __init__(self, verbose: bool) -> None:
        self.verbose = verbose
        self.level = "DEBUG" if verbose else "INFO"
        self.status = None

    def __call__(self, message) -> None:  # noqa: ANN001 - loguru's Message
        record = message.record
        p = record["extra"].get("progress")
        if p is None:
            if self.verbose:
                console.print(f"        [dim]· {escape(record['message'])}[/dim]", highlight=False)
            return
        if self.verbose:
            return  # the step lines already carry it
        text = f"      [dim]{p['what']} {p['done']} / {p['total']}" + (f" · batch {p['batch']}/{p['batches']}" if "batch" in p else "") + (f" · {p['rate']:.0f}/s" if "rate" in p else "") + f" · {mem_text()}[/dim]"
        if self.status is None:
            self.status = console.status(text, spinner="dots")
            self.status.start()
        else:
            self.status.update(text)

    def finish(self) -> None:
        if self.status is not None:
            self.status.stop()
            self.status = None


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


def size_line(m: dict) -> str:
    """Every score on one line, like `size` prints text, data and bss: waste, tangle, bloat, then live, then the stamp."""
    b, lv = m.get("bloat_score"), liveness_parts(m.get("liveness_score"))
    out, stamp = "", ""
    if b:
        p = size_parts(b)
        out = f"[bold]waste[/bold] {p['waste']} [dim]({p['lines']})[/dim]   [bold]tangle[/bold] {p['tangle']}" + (f" [dim]({p['reasons']})[/dim]" if p["reasons"] else "") + f"   [bold]bloat[/bold] {p['bloat']}"
        stamp = f"   [dim]\\[{p['stamp']}][/dim]" if p["stamp"] else ""
    if lv:
        out += ("   " if out else "") + f"[bold]live[/bold] {lv['live']} [dim]({lv['defs']})[/dim]"
    return out + stamp


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

    from vibepiler.passes.liveness import Liveness

    try:
        before = json.loads(Path(path).read_text(encoding="utf-8"))["metrics"]
        prev = before["bloat_score"]
    except (OSError, ValueError, KeyError) as exc:
        console.print(f"[yellow]baseline {path}: no bloat score in it ({type(exc).__name__})[/yellow]")
        return
    cur = program.metrics.get("bloat_score")
    if not cur:
        return
    moved = []
    dl = cur["removable_loc"] - prev["removable_loc"]
    db = cur["score"] - prev["score"]
    if abs(dl) >= 0.5 or abs(db) >= 0.005 or abs(cur["tangle"] - prev["tangle"]) >= 0.005:
        moved.append(
            f"waste {prev['removable_loc']:.0f} → {cur['removable_loc']:.0f} lines ({dl:+.0f}), "
            f"tangle {prev['tangle']:.2f} → {cur['tangle']:.2f}, bloat {prev['score']:.0%} → {cur['score']:.0%} ({db * 100:+.0f} pts)"
        )
    was, now = Liveness.of(before.get("liveness_score") or {}), Liveness.of(program)
    if was and now and now.moved(was):
        moved.append(f"live {was.value:.1%} → {now.value:.1%} ({(now.value - was.value) * 100:+.1f} pts, {now.live - now.defined - (was.live - was.defined):+d} dead)")
    for cls in METRICS:
        was, now = cls.of(before.get(f"{cls.key}_score") or {}), cls.of(program)
        if was and now and now.moved(was):
            moved.append(f"{cls.label} {was.value:.1%} → {now.value:.1%} ({(now.value - was.value) * 100:+.1f} pts)")
    console.print("[bold]since baseline[/bold]: " + ("; ".join(moved) if moved else "no change"))


def print_summary(program: Program) -> None:
    m = program.metrics
    if "bloat_score" in m or "liveness_score" in m:
        console.print()
        console.print(size_line(m))
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
