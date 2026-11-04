"""The compiler driver: frontend -> IR -> passes -> backends."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from vibepiler.backend import emit_dot, emit_html, emit_json, emit_markdown, emit_mermaid
from vibepiler.config import Config
from vibepiler.frontend.languages import available
from vibepiler.frontend.loader import STRING_ROOT, SourceFile, discover, source_from_string
from vibepiler.ir.builder import build_program
from vibepiler.ir.program import Program
from vibepiler.passes.registry import run_passes

EMITTERS = ("json", "dot", "mermaid", "html", "md")
PhaseCallback = Callable[[int, str, str], None]  # (phase number, phase name, message)
CODE_MARKS = "(){};="  # a string with one of these, or whitespace, is code rather than a path


def classify_source(source: str | os.PathLike[str]) -> tuple[str, Path | None]:
    """("directory" | "file", the path) or ("string", None). A path that is not on disk is an error, never a snippet."""
    if isinstance(source, os.PathLike):
        p = Path(source)
        kind = "directory" if p.is_dir() else "file" if p.is_file() else None
        if kind is None:
            raise FileNotFoundError(f"{source}: no such file or directory")
        return kind, p
    if "\n" in source:
        return "string", None
    try:
        p = Path(source)
        if p.is_dir():
            return "directory", p
        if p.is_file():
            return "file", p
    except (OSError, ValueError):  # too long for a path, or an embedded NUL: it is code
        return "string", None
    if any(c.isspace() or c in CODE_MARKS for c in source):
        return "string", None
    raise FileNotFoundError(f"{source!r}: no such file or directory (a code string has a newline or code-like characters)")


@dataclass
class CompileResult:
    program: Program
    outputs: dict[str, Path] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)


class Compiler:
    """`Compiler(source)`: a project directory, one source file, or a code string.

    A path must exist. A string that is not a path on disk is source code when it contains a newline or
    code-like characters; `language` names its grammar (default: the grammar that parses it with the fewest
    errors). For a path, `language` restricts discovery to that language.
    """

    phases = ("frontend", "semantic", "optimize", "emit")

    def __init__(self, source: str | os.PathLike[str], config: Config | None = None, on_phase: PhaseCallback | None = None, *, language: str | None = None) -> None:
        if language is not None and language not in available():
            raise ValueError(f"unknown language {language!r}; known: {available()}")
        self.kind, target = classify_source(source)
        self.language = language
        if self.kind == "string":
            self.root, self.target = STRING_ROOT, STRING_ROOT
            self._snippet: SourceFile | None = source_from_string(str(source), language)
        else:
            self.target = target.resolve()  # type: ignore[union-attr]
            self.root = self.target if self.kind == "directory" else self.target.parent
            self._snippet = None
        self.config = config or Config.load(self.root)
        self.on_phase = on_phase or (lambda *_: None)

    def _sources(self) -> list[SourceFile]:
        if self._snippet is not None:
            return [self._snippet]
        langs = [self.language] if self.language else self.config.languages
        return discover(self.target, ignore=self.config.ignore, languages=langs, use_gitignore=self.config.use_gitignore)

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
        program = build_program(self.root, config=self.config, files=files)
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
                path = self._emit(program, kind, out)
                if path is not None:
                    outputs[kind] = path
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


def compile(source: str | os.PathLike[str], *, config: Config | None = None, language: str | None = None, on_phase: PhaseCallback | None = None, **run) -> CompileResult:  # noqa: A001, ANN003 - the compiler metaphor, on purpose
    """One call: `Compiler(source, ...).run(...)`. `run` keywords: opt_level, emit, out_dir, only, skip, on_pass."""
    return Compiler(source, config, on_phase, language=language).run(**run)
