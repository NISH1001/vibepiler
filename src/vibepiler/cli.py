"""Command-line driver, shaped like a compiler: `vibepiler compile <path> -O2 -Wall --emit json,html -o out/`."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import networkx as nx
import typer
from rich.table import Table
from rich.tree import Tree

from vibepiler import __version__
from vibepiler.backend import emit_dot, emit_json, emit_mermaid
from vibepiler.backend.terminal import console, err_console, print_baseline, print_findings, print_phase, print_remarks, print_skipped, print_summary, size_line
from vibepiler.compiler import EMITTERS, Compiler
from vibepiler.config import Config
from vibepiler.frontend.languages import available
from vibepiler.ir.builder import build_program
from vibepiler.ir.model import EdgeKind, NodeKind
from vibepiler.ir.program import Program
from vibepiler.passes.diagnostic import Severity
from vibepiler.passes.registry import all_passes, parse_gate, run_passes
from vibepiler.remote import default_cache, fetch_remote, parse_remote, remote_subpath, repo_name

app = typer.Typer(
    name="vibepiler",
    help="A compiler for vibe-coded software: parse a project into a program graph, run optimization passes, emit reports.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)


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


def _resolve_source(path: str, cache: Path | None, refresh: bool = False) -> tuple[Path, str | None]:
    """Local path as-is; `owner/repo`, GitHub URLs or git URLs are cloned into the cache. Returns (root, repo name or None)."""
    remote = parse_remote(path)
    if remote is None:
        root = Path(path).resolve()
        if not root.exists():
            err_console.print(f"[red]error:[/red] {root} does not exist (to compile a GitHub repo, pass owner/repo or a URL)")
            raise typer.Exit(2)
        return root, None
    url, ref = remote
    try:
        root = fetch_remote(url, ref, cache=cache or default_cache(), refresh=refresh, log=lambda m: console.print(f"[dim]remote[/dim]    {m}"))
    except RuntimeError as exc:
        err_console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(2)
    return root, repo_name(url)


def _split(values: list[str] | None) -> list[str]:
    """Repeated and comma-separated option values, flattened: `--exclude a,b --exclude c` -> [a, b, c]."""
    return [p.strip() for v in values or [] for p in v.split(",") if p.strip()]


def _scoped_config(root: Path, spec: str, paths: list[str] | None, exclude: list[str] | None, isolate: bool = False, **overrides) -> Config:  # noqa: ANN003
    """The project's config plus what this command line names: the paths to report on (and a GitHub link's folder), the
    exclusions, and whether to read the named paths alone."""
    cfg = Config.load(root, **overrides)
    cfg.isolate = cfg.isolate or isolate
    cfg.include += [*filter(None, [remote_subpath(spec)]), *(paths or [])]
    cfg.exclude += _split(exclude)
    if cfg.include and not root.is_dir():
        err_console.print(f"[red]error:[/red] paths after the source need a folder or a repo to look inside; {root} is a file")
        raise typer.Exit(2)
    return cfg


def _run(compiler: Compiler, **run):  # noqa: ANN003, ANN202
    """Run, turning a named path that is not there into a clean error rather than a traceback."""
    try:
        return compiler.run(**run)
    except (FileNotFoundError, ValueError) as exc:
        err_console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(2)


@app.command()
def compile(  # noqa: A001 - mirrors the compiler metaphor on purpose
    path: str = typer.Argument(".", help="Project root, or a remote repo: owner/repo[@ref], a GitHub URL (a /tree/<ref>/<folder> link compiles that folder), or any git URL."),
    paths: Optional[List[str]] = typer.Argument(None, help="Folders or files inside the source to compile (default: all of it), e.g. backend/ services/."),
    out: Path = typer.Option(None, "-o", "--out", help="Output directory (default vibepiler-out/, or vibepiler-out/<repo> for remotes)."),
    opt: int = typer.Option(None, "-O", "--opt", min=0, max=3, help="-O0 graph only, -O1 cheap passes, -O2 all structural passes (default), -O3 adds the embedding tier (needs vibepiler[semantic])."),
    warn: Optional[str] = typer.Option(None, "-W", "--warn", help="Minimum severity to print: all|warning|error (default warning). `-Wall` shows infos."),
    emit: str = typer.Option(",".join(EMITTERS), "--emit", help="Comma-separated outputs: json,dot,mermaid,html,md."),
    only: Optional[str] = typer.Option(None, "--only", help="Comma-separated pass names to run (others skipped)."),
    skip: Optional[str] = typer.Option(None, "--skip", help="Comma-separated pass names to skip."),
    exclude: Optional[List[str]] = typer.Option(None, "--exclude", "--ignore", help="Gitignore-style patterns to leave out, comma-separated or repeated (--ignore is the old name)."),
    isolate: bool = typer.Option(False, "--isolate", help="Read only the named paths (faster). By default the whole source is read, so callers outside them count, and only the named paths are reported."),
    lang: Optional[str] = typer.Option(None, "--lang", help="Restrict to languages: python,javascript,typescript."),
    fail_on: Optional[str] = typer.Option(None, "--fail-on", help="Exit 1 if any finding is at/above this severity (warning|error), or if the repo's bloat exceeds a value: bloat>0.25."),
    remarks: bool = typer.Option(False, "-R", "--remarks", help="Print missed-optimization remarks: the elimination plan, one line per edit with the lines it frees."),
    baseline: Optional[Path] = typer.Option(None, "--baseline", help="A previous run's ir.json: print how waste, tangle and bloat moved since it."),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="Only print the phase lines and the summary."),
    max_print: int = typer.Option(60, "--max-print", help="Max findings printed to the terminal."),
    cache: Optional[Path] = typer.Option(None, "--cache", help="Where remote repos are cloned (default ~/.cache/vibepiler)."),
    refresh: bool = typer.Option(False, "--refresh", help="Re-clone a remote repo instead of reusing the cached clone."),
) -> None:
    """Compile a project: frontend -> semantic graph -> optimization passes -> reports."""
    root, remote_name = _resolve_source(path, cache, refresh)
    if out is None:
        out = Path("vibepiler-out") / remote_name if remote_name else Path("vibepiler-out")
    langs = [x.strip() for x in lang.split(",")] if lang else None
    if langs and (bad_langs := [x for x in langs if x not in available()]):
        raise typer.BadParameter(f"unknown language(s) {bad_langs}; choose from {', '.join(available())}")
    cfg = _scoped_config(root, path, paths, exclude, isolate, languages=langs)
    min_sev = _parse_warn(warn)
    emits = tuple(e.strip() for e in emit.split(",") if e.strip())
    bad = [e for e in emits if e not in EMITTERS]
    if bad:
        raise typer.BadParameter(f"unknown --emit {bad}; choose from {', '.join(EMITTERS)}")

    console.print(f"[bold]vibepiler[/bold] {__version__} compiling [cyan]{root}[/cyan]" + (f" [dim]({', '.join(cfg.include)})[/dim]" if cfg.include else ""))

    def on_pass(p, diags, secs):  # noqa: ANN001
        if not quiet:
            n = len(diags)
            console.print(f"      [dim]pass[/dim] {p.name:19} {n:4} finding{'s' if n != 1 else ''}  [dim]{secs * 1000:.0f} ms[/dim]")

    result = _run(
        Compiler(root, cfg, on_phase=print_phase),
        opt_level=opt,
        emit=emits,
        out_dir=out,
        only=[s.strip() for s in only.split(",")] if only else None,
        skip=[s.strip() for s in skip.split(",")] if skip else None,
        on_pass=on_pass,
    )
    program = result.program
    print_skipped(program)
    if not quiet:
        print_findings(program, min_sev, max_print, out / "report.md")
    print_summary(program)
    if remarks:
        print_remarks(program, max_print)
    if baseline:
        print_baseline(program, baseline)
    if fail_on:
        _gate(program, fail_on)


def _gate(program: Program, fail_on: str) -> None:
    """Exit 1 when the run crosses the line the user drew: a severity, or a score such as `bloat>0.25` or `live<0.9`."""
    gate = parse_gate(fail_on)
    if gate is not None:
        cls, op, limit = gate
        m = cls.of(program)
        if m is not None and m.trips(op, limit):
            console.print(f"[red]{cls.label} {m.value:.1%} is {'over' if op == '>' else 'under'} the limit {limit:.1%}[/red]")
            raise typer.Exit(1)
    elif any(d.severity >= _parse_warn(fail_on) for d in program.diagnostics):
        raise typer.Exit(1)


@app.command()
def size(
    path: str = typer.Argument(".", help="Project root, or a remote repo: owner/repo[@ref], a GitHub URL, or any git URL."),
    paths: Optional[List[str]] = typer.Argument(None, help="Folders or files inside the source to size (default: all of it)."),
    opt: int = typer.Option(None, "-O", "--opt", min=1, max=3, help="Which passes feed the score (default from config, usually -O2)."),
    top: int = typer.Option(15, "--top", help="How many remarks to print."),
    exclude: Optional[List[str]] = typer.Option(None, "--exclude", "--ignore", help="Gitignore-style patterns to leave out, comma-separated or repeated."),
    isolate: bool = typer.Option(False, "--isolate", help="Read only the named paths (faster). By default the whole source is read, so callers outside them count, and only the named paths are reported."),
    cache: Optional[Path] = typer.Option(None, "--cache", help="Where remote repos are cloned (default ~/.cache/vibepiler)."),
) -> None:
    """The size report: waste, tangle and bloat per folder, like `size` on a binary, plus the top remarks."""
    root, _ = _resolve_source(path, cache)
    cfg = _scoped_config(root, path, paths, exclude, isolate)
    program = _run(Compiler(root, cfg), opt_level=opt, emit=()).program
    b = program.metrics.get("bloat_score")
    if not b:
        console.print("[yellow]no passes ran, nothing to size[/yellow]")
        raise typer.Exit(1)
    console.print(f"[bold]vibepiler size[/bold] [cyan]{root}[/cyan]")
    console.print(size_line(program.metrics))
    lv = program.metrics.get("liveness_score") or {}
    live_by = {f["path"]: f"{f['value']:.1%}" for f in lv.get("folders", [])}
    table = Table(header_style="dim", show_lines=False)
    for col, just in (("folder", "left"), ("modules", "right"), ("lines", "right"), ("removable", "right"), ("waste", "right"), ("tangle", "right"), ("bloat", "right"), ("live", "right")):
        table.add_column(col, justify=just)  # type: ignore[arg-type]
    for f in b["folders"]:
        table.add_row(f["path"], str(f["modules"]), str(f["loc"]), f"{f['removable_loc']:.0f}", f"{f['waste']:.1%}", f"{f['tangle']:.1%}", f"{f['score']:.1%}", live_by.get(f["path"], ""))
    t = b.get("tests", {})
    if t.get("loc"):
        lt = lv.get("tests") or {}
        table.add_row("[dim]tests (not in the totals)[/dim]", str(t["modules"]), str(t["loc"]), f"{t['removable_loc']:.0f}", f"{t['waste']:.1%}", f"{t['tangle']:.1%}", f"{t['score']:.1%}", f"{lt['value']:.1%}" if lt else "")
    console.print(table)
    print_remarks(program, top)


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
    path: str = typer.Argument(".", help="Project root, owner/repo, or git URL."),
    paths: Optional[List[str]] = typer.Argument(None, help="Folders or files inside the source to graph (default: all of it)."),
    level: str = typer.Option("module", "--level", help="module | symbol"),
    fmt: str = typer.Option("dot", "--format", help="dot | mermaid | json"),
    packages: bool = typer.Option(False, "--packages/--no-packages", help="Include external packages."),
    exclude: Optional[List[str]] = typer.Option(None, "--exclude", "--ignore", help="Gitignore-style patterns to leave out."),
    isolate: bool = typer.Option(False, "--isolate", help="Read only the named paths (faster). By default the whole source is read, so callers outside them count, and only the named paths are reported."),
) -> None:
    """Print the dependency / call graph in DOT, Mermaid or JSON."""
    root, _ = _resolve_source(path, None)
    cfg = _scoped_config(root, path, paths, exclude, isolate)
    try:
        program = build_program(root, config=cfg)
    except (FileNotFoundError, ValueError) as exc:
        err_console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(2)
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
    path: str = typer.Argument(".", help="Project root, owner/repo, or git URL."),
    src: str = typer.Option(..., "--from", help="Start symbol, e.g. app.main.main or main.py"),
    dst: Optional[str] = typer.Option(None, "--to", help="Target symbol; omit to print the execution tree from --from."),
    depth: int = typer.Option(6, "--depth", help="Max depth of the execution tree."),
    refs: bool = typer.Option(True, "--refs/--no-refs", help="Follow reference edges too, not just calls."),
) -> None:
    """Show execution pathways: the call tree from a symbol, or every shortest path between two symbols."""
    root, _ = _resolve_source(path, None)
    program = build_program(root)
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


@app.command()
def similar(
    path: str = typer.Argument(".", help="Project root, owner/repo, or git URL."),
    symbol: str = typer.Argument(..., help="Function or method, e.g. app.utils.sum_even"),
    top: int = typer.Option(10, "-n", "--top", help="How many neighbours to show."),
) -> None:
    """Find the functions most similar in meaning to SYMBOL (embedding tier; needs vibepiler[semantic])."""
    from vibepiler.semantic.similar import similar_symbols

    root, _ = _resolve_source(path, None)
    program = build_program(root)
    start = _find_symbol(program, symbol)
    try:
        hits = similar_symbols(program, start, top=top)
    except (RuntimeError, KeyError) as exc:
        err_console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(2)
    table = Table(header_style="dim", title=f"closest to {program.graph.node(start).qualname}", title_style="bold")
    table.add_column("similarity", justify="right")
    table.add_column("symbol", style="cyan")
    table.add_column("location", style="dim")
    for node, score in hits:
        table.add_row(f"{score:.0%}", node.qualname, node.location)
    console.print(table)


if __name__ == "__main__":
    app()
