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


def test_html_viewer_has_a_living_graph_layer(program):
    html = emit_html(program)
    assert '<canvas id="flow"' in html  # traffic overlay: particles travel along import/call edges
    assert "underlay-color" in html  # glow halos on live nodes
    assert "node.dead" in html and "prefers-reduced-motion" in html  # dead code is a ghost; motion is optional


# ------------------------------------------------------------------ liveness beside bloat
def test_size_line_prints_live_after_bloat(program):
    from vibepiler.backend.terminal import size_line

    s = size_line(program.metrics)
    assert s.index("[bold]bloat") < s.index("[bold]live") and "definitions" in s


def test_markdown_carries_the_live_number(program):
    md = emit_markdown(program)
    assert "**live " in md and "definitions" in md


def test_html_carries_the_liveness_score_and_the_colour_switch(program):
    html = emit_html(program)
    assert '"liveness_score"' in html and 'id="t-colour"' in html and "livenessBlock" in html


def test_html_graph_hides_test_modules_until_asked(program):
    html = emit_html(program)
    assert 'id="t-tests" aria-pressed="false"' in html  # off by default: the scores leave tests out, so does the graph
    assert "tests: false" in html and "!show.tests && isTest(n)" in html


def test_a_cloned_repo_is_named_after_the_repo_not_its_cache_folder(tmp_path):
    from vibepiler.backend.common import project_name
    from vibepiler.ir.builder import build_program

    root = tmp_path / "repos" / "github.com__owner__my-repo@dev"
    root.mkdir(parents=True)
    (root / "a.py").write_text("def f():\n    return 1\n")
    prog = build_program(root)
    assert project_name(prog) == "my-repo@dev"
    assert "<span>my-repo@dev</span>" in emit_html(prog)
    assert emit_markdown(prog).startswith("# Vibepiler report for `my-repo@dev`")


def test_header_phases_never_shrink_under_the_meter(program):
    # the meter does not wrap, so the phase tabs must not shrink into it; the header wraps the meter instead
    assert ".phases { display: flex; flex: 1 0 auto;" in emit_html(program)
