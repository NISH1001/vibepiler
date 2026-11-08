"""The compiler driver: frontend -> IR -> passes -> backends."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from loguru import logger as log

from vibepiler.backend import emit_dot, emit_html, emit_json, emit_markdown, emit_mermaid
from vibepiler.config import Config
from vibepiler.frontend.languages import available
from vibepiler.frontend.loader import SourceFile, classify_source, source_from_file, source_from_string
from vibepiler.ir.builder import build_program, read_sources
from vibepiler.ir.graph import ProgramGraph
from vibepiler.ir.program import Program
from vibepiler.passes.bloat_score import Bloat
from vibepiler.passes.diagnostic import Diagnostic
from vibepiler.passes.liveness import Liveness
from vibepiler.passes.metric import Metric
from vibepiler.passes.registry import all_metrics, run_passes
from vibepiler.remote import fetch_remote, parse_remote, remote_subpath

EMITTERS = ("json", "dot", "mermaid", "html", "md")
PhaseCallback = Callable[[int, str, str], None]  # (phase number, phase name, message)


@dataclass
class CompileResult:
    """What a compile returns: the program, the files written, the phase timings, and typed views over the scores."""

    program: Program
    outputs: dict[str, Path] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def scores(self) -> dict[str, Metric]:
        """Every score that was measured, by label: `result.scores["live"].value`. Empty at -O0."""
        return {cls.label: m for cls in all_metrics() if (m := cls.of(self.program)) is not None}

    @property
    def bloat(self) -> Bloat | None:
        return Bloat.of(self.program)

    @property
    def liveness(self) -> Liveness | None:
        return Liveness.of(self.program)

    @property
    def diagnostics(self) -> list[Diagnostic]:
        return self.program.diagnostics

    @property
    def graph(self) -> ProgramGraph:
        return self.program.graph

    @property
    def metrics(self) -> dict[str, Any]:
        """The raw per-pass numbers: the untyped layer under `scores`."""
        return self.program.metrics

    def to_dict(self) -> dict[str, Any]:
        """The ir.json payload."""
        return emit_json(self.program)


class Compiler:
    """`Compiler(source)`: a project directory, a remote repo (owner/repo[@ref], a GitHub or git URL), one source file,
    or a code string. For a directory or a repo, `include` names the folders and files inside it to read (default:
    all of it) and `exclude` cuts gitignore-style patterns out; both also chain: `Compiler(".").include("api/").exclude("**/gen/")`.
    By default the whole source is still read, so callers and entry points outside the included paths count, and
    only the included paths are scored and reported; `isolate` reads the included paths alone.

    A path must exist. A string that is not a path on disk is source code when it contains a newline, code-like
    characters, or parses cleanly in some grammar; `language` names its grammar (default: the grammar that parses
    it with the fewest errors). For a directory, `language` restricts discovery; for a file, it must match.
    """

    phases = ("frontend", "semantic", "optimize", "emit")

    def __init__(
        self,
        source: str | Path,
        config: Config | None = None,
        on_phase: PhaseCallback | None = None,
        *,
        language: str | None = None,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        isolate: bool | None = None,
        max_tokens: int | None = None,
        use_cache: bool | None = None,
    ) -> None:
        if language is not None and language not in available():
            raise ValueError(f"unknown language {language!r}; known: {available()}")
        self.remote = parse_remote(source) if isinstance(source, str) else None
        if self.remote is not None:  # cloned (or refreshed) into the cache, then compiled as the folder it is
            include = [*(include or []), *filter(None, [remote_subpath(str(source))])]
            source = fetch_remote(*self.remote, log=lambda m: log.debug("remote: {}", m))
        self.kind, target = classify_source(source)
        self.language = language
        self.target = target if self.kind == "string" else target.resolve()
        self.root = self.target.parent if self.kind == "file" else self.target
        # a snippet or a named file is fixed here, so a bad language or extension fails before any work; a directory is walked at run time
        self._given: list[SourceFile] | None = None
        if self.kind == "string":
            self._given = [source_from_string(str(source), language)]
        elif self.kind == "file":
            self._given = [source_from_file(self.target, self.root, language)]
        cfg = config or (Config() if self.kind == "string" else Config.load(self.root))
        self.config = replace(cfg, include=list(cfg.include), exclude=list(cfg.exclude))  # chaining never edits the caller's Config
        if max_tokens is not None:  # -O3: tokens embedded per function, 0 for the model's whole context
            self.config.embedding_max_tokens = max_tokens
        if use_cache is not None:  # -O3: False embeds everything afresh and leaves the embedding cache untouched
            self.config.embedding_cache = use_cache
        self.on_phase = on_phase or (lambda *_: None)
        self.include(*(include or []))
        self.exclude(*(exclude or []))
        if isolate is not None:
            self.isolate(isolate)

    def include(self, *paths: str) -> Compiler:
        """Read these folders or files of the source (relative to it) instead of all of it. Chains."""
        if paths and self.kind != "directory":
            raise ValueError(f"include needs a folder or a repo to look inside; this source is a {self.kind}")
        self.config.include += [str(p) for p in paths]
        return self

    def isolate(self, on: bool = True) -> Compiler:
        """Read only the included paths. Faster, but calls and entry points outside them are not seen. Chains."""
        self.config.isolate = on
        return self

    def exclude(self, *patterns: str) -> Compiler:
        """Leave out paths matching these gitignore-style patterns. Chains."""
        self.config.exclude += [str(p) for p in patterns]
        return self

    def _sources(self) -> list[SourceFile]:
        if self._given is not None:
            return self._given
        return read_sources(self.target, self.config, [self.language] if self.language else None)

    def run(
        self,
        opt_level: int | None = None,
        emit: tuple[str, ...] | list[str] = EMITTERS,
        out_dir: str | Path | None = None,
        only: list[str] | None = None,
        skip: list[str] | None = None,
        on_pass=None,  # noqa: ANN001
    ) -> CompileResult:
        opt = self.config.opt_level if opt_level is None else opt_level
        timings: dict[str, float] = {}

        t = time.perf_counter()
        files = self._sources()
        timings["frontend"] = time.perf_counter() - t
        langs: dict[str, int] = {}
        for f in files:
            langs[f.language] = langs.get(f.language, 0) + 1
        self.on_phase(1, "frontend", f"parsed {len(files)} files (" + ", ".join(f"{k}: {v}" for k, v in sorted(langs.items())) + ")")

        t = time.perf_counter()
        program = build_program(self.root, config=self.config, files=files, scope=self.kind)
        timings["semantic"] = time.perf_counter() - t
        meta = program.graph.meta
        res = meta.get("resolution", {})
        total = res.get("calls_total", 0)
        linked = res.get("calls_resolved", 0) + res.get("calls_external", 0) + res.get("calls_builtin", 0)
        pct = f"{linked / total:.0%}" if total else "n/a"
        self.on_phase(2, "semantic", f"{meta.get('modules', 0)} modules, {meta.get('symbols', 0)} symbols, {len(program.graph.edges())} edges; {pct} of {total} call sites linked")
        if meta.get("parse_errors"):
            self.on_phase(2, "semantic", f"{len(meta['parse_errors'])} files failed to parse: " + "; ".join(meta["parse_errors"][:3]))

        t = time.perf_counter()
        run_passes(program, opt_level=opt, only=only, skip=skip, on_pass=on_pass)
        timings["optimize"] = time.perf_counter() - t
        counts = {}
        for d in program.diagnostics:
            counts[d.severity.label] = counts.get(d.severity.label, 0) + 1
        self.on_phase(3, "optimize", f"-O{opt}: {len(program.diagnostics)} findings (" + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())) + ")" if program.diagnostics else f"-O{opt}: no passes run" if opt == 0 else f"-O{opt}: no findings")

        t = time.perf_counter()
        outputs: dict[str, Path] = {}
        if out_dir is not None and emit:
            out = Path(out_dir)
            out.mkdir(parents=True, exist_ok=True)
            for kind in emit:
                t_kind = time.perf_counter()
                path = self._emit(program, kind, out)
                if path is not None:
                    outputs[kind] = path
                    log.debug("emit {}  {:.1f} KB  {:.2f} s", path.name, path.stat().st_size / 1024, time.perf_counter() - t_kind)
        timings["emit"] = time.perf_counter() - t
        program.metrics["timings_phases"] = {k: round(v, 3) for k, v in timings.items()}
        if outputs:
            names = sorted({p.name for p in outputs.values()} | ({"graph-symbols.dot"} if "dot" in outputs else set()))
            self.on_phase(4, "emit", f"wrote {', '.join(names)} to {Path(out_dir)}")  # type: ignore[arg-type]
        else:
            self.on_phase(4, "emit", "nothing to write")
        return CompileResult(program=program, outputs=outputs, timings=timings)

    @staticmethod
    def _emit(program: Program, kind: str, out: Path) -> Path | None:
        if kind == "json":
            path = out / "ir.json"
            path.write_text(json.dumps(emit_json(program), indent=1), encoding="utf-8")
        elif kind == "dot":
            path = out / "graph.dot"
            path.write_text(emit_dot(program, level="module"), encoding="utf-8")
            (out / "graph-symbols.dot").write_text(emit_dot(program, level="symbol"), encoding="utf-8")
        elif kind == "mermaid":
            path = out / "graph.mmd"
            path.write_text(emit_mermaid(program), encoding="utf-8")
        elif kind == "md":
            path = out / "report.md"
            path.write_text(emit_markdown(program), encoding="utf-8")
        elif kind == "html":
            path = out / "report.html"
            path.write_text(emit_html(program), encoding="utf-8")
        else:
            return None
        return path


def compile(  # noqa: A001 - the compiler metaphor, on purpose
    source: str | Path,
    *,
    config: Config | None = None,
    language: str | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    isolate: bool | None = None,
    max_tokens: int | None = None,
    use_cache: bool | None = None,
    on_phase: PhaseCallback | None = None,
    **run,  # noqa: ANN003
) -> CompileResult:
    """One call: `Compiler(source, ...).run(...)`. `run` keywords: opt_level, emit, out_dir, only, skip, on_pass."""
    return Compiler(source, config, on_phase, language=language, include=include, exclude=exclude, isolate=isolate, max_tokens=max_tokens, use_cache=use_cache).run(**run)
