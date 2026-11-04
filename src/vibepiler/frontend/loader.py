"""File discovery and lazy parsing.

This is the lexer/parser stage of the compiler: it finds every source file in
the project, decides which language grammar applies, and parses it with
tree-sitter on first access.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import pathspec
from tree_sitter import Tree

from vibepiler.frontend.parser import GRAMMARS, parse, walk

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


def source_from_string(code: str, language: str | None = None) -> SourceFile:
    """One in-memory source file for a code string. Without `language`, the grammar with the fewest error nodes wins (python, javascript, typescript on a tie)."""
    data = code.encode("utf-8")
    if language is None:
        errors = {name: sum(1 for n in walk(parse(name, data).root_node) if n.is_error or n.is_missing) for name in GRAMMARS}
        language = min(errors, key=errors.__getitem__)
    rel = "snippet" + next(ext for ext, name in EXTENSIONS.items() if name == language)
    return SourceFile(path=STRING_ROOT / rel, rel=rel, language=language, root=STRING_ROOT, source=data)


def _read_gitignore(root: Path) -> list[str]:
    gi = root / ".gitignore"
    if not gi.is_file():
        return []
    return gi.read_text(encoding="utf-8", errors="replace").splitlines()


def discover(
    root: str | os.PathLike[str],
    *,
    ignore: list[str] | None = None,
    languages: list[str] | None = None,
    use_gitignore: bool = True,
) -> list[SourceFile]:
    """Walk `root` and return the parseable source files, sorted by path."""
    root = Path(root).resolve()
    if root.is_file():
        files = [root]
        root = root.parent
    else:
        files = None

    patterns = list(DEFAULT_IGNORES)
    if use_gitignore:
        patterns += _read_gitignore(root)
    patterns += ignore or []
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

    if files is not None:
        for p in files:
            consider(p)
    else:
        for dirpath, dirnames, filenames in os.walk(root):
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
    out.sort(key=lambda f: f.rel)
    return out
