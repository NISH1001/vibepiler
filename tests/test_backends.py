import json
from pathlib import Path

import pytest

from vibepiler.backend import emit_dot, emit_html, emit_json, emit_markdown, emit_mermaid
from vibepiler.ir.builder import build_program
from vibepiler.passes.registry import run_passes

FIX = Path(__file__).parent / "fixtures" / "pyproj"


@pytest.fixture(scope="module")
def program():
    prog = build_program(FIX)
    run_passes(prog, opt_level=2)
    return prog


def test_json_output_is_serializable_and_complete(program):
    data = emit_json(program)
    text = json.dumps(data)
    back = json.loads(text)
    assert {"meta", "nodes", "edges", "diagnostics", "metrics"} <= set(back)
    assert any(n["id"] == "sym:src/app/core.py::process" for n in back["nodes"])
    assert any(d["code"] == "dead-symbol" for d in back["diagnostics"])
    assert back["metrics"]["dead_code"]["symbols"] >= 1


def test_dot_module_level_has_modules_and_marks_cycles(program):
    dot = emit_dot(program, level="module")
    assert dot.startswith("digraph")
    assert '"mod:src/app/core.py"' in dot
    assert "red" in dot  # cycle edge highlighted


def test_dot_symbol_level_clusters_by_module(program):
    dot = emit_dot(program, level="symbol")
    assert "subgraph cluster_" in dot
    assert "sym:src/app/utils.py::helper" in dot


def test_mermaid_output(program):
    mmd = emit_mermaid(program)
    assert mmd.startswith("flowchart LR")
    assert "app.main" in mmd and "app.core" in mmd
    assert "-->" in mmd


def test_markdown_report_sections(program):
    md = emit_markdown(program)
    assert "# Vibepiler report" in md
    assert "## Summary" in md
    assert "dead-code" in md
    assert "`unused_fn`" in md
    assert "## Entry points" in md


def test_html_viewer_embeds_graph(program):
    html = emit_html(program)
    assert "<html" in html and "cytoscape" in html
    assert "sym:src/app/core.py::process" in html
