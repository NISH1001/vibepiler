from pathlib import Path

from vibepiler.frontend.loader import discover

FIX = Path(__file__).parent / "fixtures" / "pyproj"


def rel_paths(files):
    return sorted(f.rel for f in files)


def test_discover_finds_python_sources_and_detects_language():
    files = discover(FIX)
    assert "src/app/core.py" in rel_paths(files)
    core = next(f for f in files if f.rel == "src/app/core.py")
    assert core.language == "python"
    assert core.source.startswith(b"from abc import ABC")


def test_discover_skips_default_ignored_directories():
    paths = rel_paths(discover(FIX))
    assert not any(p.startswith("node_modules/") for p in paths)
    assert not any(p.startswith(".venv/") for p in paths)


def test_discover_respects_gitignore():
    paths = rel_paths(discover(FIX))
    assert not any(p.startswith("generated/") for p in paths)


def test_discover_accepts_extra_ignore_globs():
    paths = rel_paths(discover(FIX, ignore=["tests/**"]))
    assert not any(p.startswith("tests/") for p in paths)


def test_discover_can_restrict_languages():
    files = discover(FIX, languages=["javascript"])
    assert files == []


def test_source_file_parses_lazily_into_tree_sitter_tree():
    core = next(f for f in discover(FIX) if f.rel == "src/app/core.py")
    assert core.tree.root_node.type == "module"
    assert not core.tree.root_node.has_error
