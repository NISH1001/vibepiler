"""File discovery and lazy parsing.

This is the lexer/parser stage of the compiler: it finds every source file in
the project, decides which language grammar applies, and parses it with
tree-sitter on first access.
"""

from __future__ import annotations

import os
from contextlib import suppress
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import pathspec
from tree_sitter import Tree

from vibepiler.frontend.parser import parse, walk

DEFAULT_IGNORES: tuple[str, ...] = (
    ".git/",
    ".hg/",
    ".svn/",
    ".venv/",
    "venv/",
    "env/",
    "node_modules/",
    "__pycache__/",
    ".mypy_cache/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".tox/",
    ".nox/",
    "dist/",
    "build/",
    ".next/",
    ".nuxt/",
    ".turbo/",
    "coverage/",
    "site-packages/",
    "*.min.js",
    "*.min.css",
    "*.bundle.js",
    "*.d.ts",
    "*.egg-info/",
    ".idea/",
    ".vscode/",
)

MAX_FILE_BYTES = 2_000_000
STRING_ROOT = Path("<string>")  # the root of a program handed over as a string, named as Python names it

# extension -> language name (must match a LanguageSpec name)
EXTENSIONS: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
}


@dataclass
class SourceFile:
    path: Path
    rel: str
    language: str
    root: Path
    source: bytes = field(repr=False)

    @cached_property
    def tree(self) -> Tree:
        return parse(self.language, self.source, rel=self.rel)

    @property
    def lines(self) -> int:
        return self.source.count(b"\n") + (1 if self.source and not self.source.endswith(b"\n") else 0)

    def text(self, node) -> str:  # noqa: ANN001 - tree_sitter.Node
        return self.source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


CODE_MARKS = "(){};="  # a string with one of these is code, not a path
#: the grammars tried on a snippet, in tie-break order, with the file name the snippet is given
SNIPPET_CANDIDATES: tuple[tuple[str, str], ...] = (("python", "snippet.py"), ("javascript", "snippet.js"), ("typescript", "snippet.ts"), ("typescript", "snippet.tsx"))


def classify_source(source: str | Path) -> tuple[str, Path]:
    """("directory" | "file", the path) or ("string", STRING_ROOT). A path that is not on disk is an error, never a snippet."""
    if isinstance(source, Path):
        if source.is_dir():
            return "directory", source
        if source.is_file():
            return "file", source
        raise FileNotFoundError(f"{source}: no such file or directory")
    if not source.strip():
        raise ValueError("empty source: give a directory, a file, or a code string")
    if "\n" in source:
        return "string", STRING_ROOT
    with suppress(OSError, ValueError):  # too long for a path, or an embedded NUL: not on disk either way
        p = Path(source)
        if p.is_dir():
            return "directory", p
        if p.is_file():
            return "file", p
    if any(c in CODE_MARKS for c in source) or (any(c.isspace() for c in source) and any(errors == 0 for _, errors in _snippet_parses(source.encode("utf-8")).values())):
        return "string", STRING_ROOT
    raise FileNotFoundError(f"{source!r}: no such file or directory (a code string has a newline, code-like characters, or parses cleanly)")


def _snippet_parses(data: bytes) -> dict[tuple[str, str], tuple[Tree, int]]:
    """Every candidate grammar's parse of a snippet, with its count of error and missing nodes."""
    out: dict[tuple[str, str], tuple[Tree, int]] = {}
    for lang, rel in SNIPPET_CANDIDATES:
        tree = parse(lang, data, rel=rel)
        out[(lang, rel)] = (tree, sum(1 for n in walk(tree.root_node) if n.is_error or n.is_missing))
    return out


def source_from_string(code: str, language: str | None = None) -> SourceFile:
    """One in-memory source file for a code string. Without `language`, the grammar with the fewest error nodes wins (python, javascript, typescript, tsx on a tie)."""
    data = code.encode("utf-8")
    if language is not None:
        rel = next(r for lang, r in SNIPPET_CANDIDATES if lang == language)
        return SourceFile(path=STRING_ROOT / rel, rel=rel, language=language, root=STRING_ROOT, source=data)
    (language, rel), (tree, _) = min(_snippet_parses(data).items(), key=lambda kv: kv[1][1])
    sf = SourceFile(path=STRING_ROOT / rel, rel=rel, language=language, root=STRING_ROOT, source=data)
    sf.__dict__["tree"] = tree  # the winning parse; the snippet is not parsed again
    return sf


def source_from_file(path: Path, root: Path, language: str | None = None) -> SourceFile:
    """The one file the caller named; ignore patterns do not apply to it. An unknown extension, or a `language` that is not the file's, is an error."""
    lang = EXTENSIONS.get(path.suffix.lower())
    if lang is None:
        raise ValueError(f"cannot compile {path.name}: unknown extension {path.suffix!r}; known: {', '.join(EXTENSIONS)}")
    if language is not None and language != lang:
        raise ValueError(f"{path.name} is {lang}, not {language}")
    return SourceFile(path=path, rel=path.relative_to(root).as_posix(), language=lang, root=root, source=path.read_bytes())


def _read_gitignore(root: Path) -> list[str]:
    gi = root / ".gitignore"
    if not gi.is_file():
        return []
    return gi.read_text(encoding="utf-8", errors="replace").splitlines()


def discover(
    root: str | os.PathLike[str],
    *,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    languages: list[str] | None = None,
    use_gitignore: bool = True,
) -> list[SourceFile]:
    """Walk `root` and return the parseable source files, sorted by path.

    `include` names the folders and files under `root` to read (default: all of it); paths stay relative to `root`,
    so its manifests and .gitignore still apply. `exclude` is gitignore-style patterns cut out of whatever is read."""
    root = Path(root).resolve()
    starts: list[Path] = []
    if root.is_file():
        starts = [root]
        root = root.parent
    for rel in include or []:
        p = (root / rel).resolve()
        if not p.is_relative_to(root):
            raise ValueError(f"{rel}: outside {root}; include paths live inside the source")
        if not p.exists():
            raise FileNotFoundError(f"{rel}: no such file or directory under {root}")
        starts.append(p)

    patterns = list(DEFAULT_IGNORES)
    if use_gitignore:
        patterns += _read_gitignore(root)
    patterns += exclude or []
    spec = pathspec.GitIgnoreSpec.from_lines(patterns)

    wanted = set(languages) if languages else None
    out: list[SourceFile] = []

    def consider(p: Path) -> None:
        rel = p.relative_to(root).as_posix()
        lang = EXTENSIONS.get(p.suffix.lower())
        if lang is None or (wanted and lang not in wanted):
            return
        if spec.match_file(rel):
            return
        try:
            if p.stat().st_size > MAX_FILE_BYTES:
                return
            data = p.read_bytes()
        except OSError:
            return
        out.append(SourceFile(path=p, rel=rel, language=lang, root=root, source=data))

    def walk_dir(top: Path) -> None:
        for dirpath, dirnames, filenames in os.walk(top):
            d = Path(dirpath)
            rel_dir = d.relative_to(root).as_posix()
            # prune ignored directories early (big win for node_modules etc.)
            keep = []
            for name in dirnames:
                sub = f"{rel_dir}/{name}/" if rel_dir != "." else f"{name}/"
                if not spec.match_file(sub):
                    keep.append(name)
            dirnames[:] = sorted(keep)
            for name in sorted(filenames):
                consider(d / name)

    for top in starts or [root]:
        if top.is_file():
            consider(top)
        elif top == root or not spec.match_file(top.relative_to(root).as_posix() + "/"):
            walk_dir(top)
    unique = {f.rel: f for f in out}  # overlapping include paths read a file once
    return sorted(unique.values(), key=lambda f: f.rel)
