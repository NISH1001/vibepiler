"""Command-line driver, shaped like a compiler: `vibepiler compile <path> -O2 -Wall --emit json,html -o out/`."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import networkx as nx
import typer
from rich.console import Console
from rich.table import Table
from rich.tree import Tree

from vibepiler import __version__
from vibepiler.backend import emit_dot, emit_json, emit_mermaid
from vibepiler.compiler import EMITTERS, Compiler
from vibepiler.config import Config
from vibepiler.frontend.languages import available
from vibepiler.ir.builder import build_program
from vibepiler.ir.model import EdgeKind, NodeKind
from vibepiler.ir.program import Program
from vibepiler.passes.diagnostic import Severity
from vibepiler.passes.registry import all_passes, run_passes

app = typer.Typer(
    name="vibepiler",
    help="A compiler for vibe-coded software: parse a project into a program graph, run optimization passes, emit reports.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console()
err_console = Console(stderr=True)

SEV_STYLE = {Severity.ERROR: "bold red", Severity.WARNING: "yellow", Severity.INFO: "cyan"}


def _version(value: bool) -> None:
    if value:
        console.print(f"vibepiler {__version__}")
        raise typer.Exit()


@app.callback()
def _main(version: bool = typer.Option(False, "--version", "-V", callback=_version, is_eager=True, help="Show version and exit.")) -> None:
    pass


def _parse_warn(w: str | None) -> Severity:
    if w is None or w == "":
        return Severity.WARNING
    w = w.lower()
    if w in ("all", "info"):
        return Severity.INFO
    if w in ("error", "errors"):
        return Severity.ERROR
    if w in ("warning", "warn", "warnings"):
        return Severity.WARNING
    raise typer.BadParameter(f"unknown warning level {w!r}; use all|warning|error")


def _print_phase(n: int, name: str, msg: str) -> None:
    console.print(f"[dim][{n}/4][/dim] [bold]{name:9}[/bold] {msg}")


@app.command()
def compile(  # noqa: A001 - mirrors the compiler metaphor on purpose
    path: Path = typer.Argument(Path("."), help="Project root to compile."),
    out: Path = typer.Option(Path("vibepiler-out"), "-o", "--out", help="Output directory."),
    opt: int = typer.Option(None, "-O", "--opt", min=0, max=2, help="-O0 graph only, -O1 cheap passes, -O2 everything (default)."),
    warn: Optional[str] = typer.Option(None, "-W", "--warn", help="Minimum severity to print: all|warning|error (default warning). `-Wall` shows infos."),
    emit: str = typer.Option(",".join(EMITTERS), "--emit", help="Comma-separated outputs: json,dot,mermaid,html,md."),
    only: Optional[str] = typer.Option(None, "--only", help="Comma-separated pass names to run (others skipped)."),
    skip: Optional[str] = typer.Option(None, "--skip", help="Comma-separated pass names to skip."),
    ignore: Optional[str] = typer.Option(None, "--ignore", help="Extra gitignore-style patterns, comma-separated."),
    lang: Optional[str] = typer.Option(None, "--lang", help="Restrict to languages: python,javascript,typescript."),
    fail_on: Optional[str] = typer.Option(None, "--fail-on", help="Exit 1 if any finding is at/above this severity (warning|error)."),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="Only print the phase lines and the summary."),
    max_print: int = typer.Option(60, "--max-print", help="Max findings printed to the terminal."),
) -> None:
    """Compile a project: frontend -> semantic graph -> optimization passes -> reports."""
    root = path.resolve()
    if not root.exists():
        err_console.print(f"[red]error:[/red] {root} does not exist")
        raise typer.Exit(2)
    langs = [x.strip() for x in lang.split(",")] if lang else None
    if langs and (bad_langs := [x for x in langs if x not in available()]):
        raise typer.BadParameter(f"unknown language(s) {bad_langs}; choose from {', '.join(available())}")
    cfg = Config.load(root, languages=langs)
    if ignore:
        cfg.ignore += [p.strip() for p in ignore.split(",") if p.strip()]
    min_sev = _parse_warn(warn)
    emits = tuple(e.strip() for e in emit.split(",") if e.strip())
    bad = [e for e in emits if e not in EMITTERS]
    if bad:
        raise typer.BadParameter(f"unknown --emit {bad}; choose from {', '.join(EMITTERS)}")

    console.print(f"[bold]vibepiler[/bold] {__version__} compiling [cyan]{root}[/cyan]")

    def on_pass(p, diags, secs):  # noqa: ANN001
        if not quiet:
            n = len(diags)
            console.print(f"      [dim]pass[/dim] {p.name:15} {n:4} finding{'s' if n != 1 else ''}  [dim]{secs * 1000:.0f} ms[/dim]")

    result = Compiler(root, cfg, on_phase=_print_phase).run(
        opt_level=opt,
        emit=emits,
        out_dir=out,
        only=[s.strip() for s in only.split(",")] if only else None,
        skip=[s.strip() for s in skip.split(",")] if skip else None,
        on_pass=on_pass,
    )
    program = result.program
    shown = [d for d in program.diagnostics if d.severity >= min_sev]
    if not quiet and shown:
        console.print()
        for d in shown[:max_print]:
            style = SEV_STYLE[d.severity]
            console.print(f"[{style}]{d.severity.label:7}[/{style}] [dim]{d.pass_name}[/dim]/{d.code}  [cyan]{d.location}[/cyan]: {d.message}")
            if d.suggestion:
                console.print(f"        [dim italic]{d.suggestion}[/dim italic]")
        if len(shown) > max_print:
            console.print(f"[dim]... {len(shown) - max_print} more; see {out / 'report.md'} or ir.json[/dim]")
    _print_summary(program)
    if fail_on:
        threshold = _parse_warn(fail_on)
        if any(d.severity >= threshold for d in program.diagnostics):
            raise typer.Exit(1)


def _print_summary(program: Program) -> None:
    m = program.metrics
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


@app.command()
def passes() -> None:
    """List the optimization passes and the compiler concept each one mirrors."""
    table = Table(header_style="dim")
    table.add_column("pass", style="bold")
    table.add_column("-O")
    table.add_column("compiler analogue", style="cyan")
    table.add_column("finds")
    for p in all_passes():
        table.add_row(p.name, str(p.cost), p.analogue, p.description)
    console.print(table)


@app.command()
def graph(
    path: Path = typer.Argument(Path(".")),
    level: str = typer.Option("module", "--level", help="module | symbol"),
    fmt: str = typer.Option("dot", "--format", help="dot | mermaid | json"),
    packages: bool = typer.Option(False, "--packages/--no-packages", help="Include external packages."),
    ignore: Optional[str] = typer.Option(None, "--ignore"),
) -> None:
    """Print the dependency / call graph in DOT, Mermaid or JSON."""
    cfg = Config.load(path.resolve())
    if ignore:
        cfg.ignore += ignore.split(",")
    program = build_program(path, config=cfg)
    run_passes(program, opt_level=1, only=["entry-points", "cycles"])
    if fmt == "dot":
        typer.echo(emit_dot(program, level=level, include_packages=packages))
    elif fmt == "mermaid":
        typer.echo(emit_mermaid(program, include_packages=packages))
    elif fmt == "json":
        import json

        typer.echo(json.dumps(emit_json(program), indent=1))
    else:
        raise typer.BadParameter("format must be dot, mermaid or json")


def _find_symbol(program: Program, query: str) -> str:
    g = program.graph
    exact = []
    suffix = []
    for n in g.nodes():
        if n.kind is NodeKind.PACKAGE:
            continue
        full = n.qualname if n.kind is NodeKind.MODULE else f"{g.node(n.module).qualname}.{n.qualname}"  # type: ignore[arg-type]
        if query in (n.id, full, n.qualname):
            exact.append(n.id)
        elif full.endswith("." + query) or (n.file and n.file.endswith(query)):
            suffix.append(n.id)
    hits = exact or suffix
    if not hits:
        raise typer.BadParameter(f"no symbol matches {query!r}")
    if len(hits) > 1:
        raise typer.BadParameter(f"{query!r} is ambiguous: " + ", ".join(hits[:8]))
    return hits[0]


@app.command()
def paths(
    path: Path = typer.Argument(Path(".")),
    src: str = typer.Option(..., "--from", help="Start symbol, e.g. app.main.main or main.py"),
    dst: Optional[str] = typer.Option(None, "--to", help="Target symbol; omit to print the execution tree from --from."),
    depth: int = typer.Option(6, "--depth", help="Max depth of the execution tree."),
    refs: bool = typer.Option(True, "--refs/--no-refs", help="Follow reference edges too, not just calls."),
) -> None:
    """Show execution pathways: the call tree from a symbol, or every shortest path between two symbols."""
    program = build_program(path)
    g = program.graph
    kinds = [EdgeKind.CALLS] + ([EdgeKind.REFERENCES] if refs else [])
    start = _find_symbol(program, src)
    if dst:
        target = _find_symbol(program, dst)
        cg = g.call_graph(include_references=refs)
        try:
            found = list(nx.all_shortest_paths(cg, start, target))
        except nx.NetworkXNoPath:
            console.print(f"[yellow]no path[/yellow] from {start} to {target}")
            raise typer.Exit(1)
        for p in found:
            console.print(" -> ".join(f"[cyan]{g.node(n).qualname}[/cyan]" for n in p))
        return
    root_node = g.node(start)
    tree = Tree(f"[bold]{root_node.qualname}[/bold] [dim]{root_node.location}[/dim]")
    seen = {start}

    def grow(branch: Tree, nid: str, d: int) -> None:
        if d >= depth:
            return
        for e in g.out_edges(nid, kinds):
            t = g.node(e.dst)
            label = f"{t.qualname}" if t.kind is not NodeKind.PACKAGE else f"[dim][{t.name}].{', .'.join(e.attrs.get('members', []))}[/dim]"
            if e.kind is EdgeKind.REFERENCES:
                label = f"[dim]{label} (ref)[/dim]"
            loc = t.location if t.kind is not NodeKind.PACKAGE else ""
            sub = branch.add(f"{label} [dim]{loc}[/dim]" + (" [dim](…)[/dim]" if e.dst in seen else ""))
            if e.dst in seen or t.kind is NodeKind.PACKAGE:
                continue
            seen.add(e.dst)
            grow(sub, e.dst, d + 1)

    grow(tree, start, 0)
    console.print(tree)


if __name__ == "__main__":
    app()
