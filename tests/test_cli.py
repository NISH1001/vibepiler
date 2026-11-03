from pathlib import Path

from typer.testing import CliRunner

from vibepiler.cli import app

FIX = Path(__file__).parent / "fixtures" / "pyproj"
runner = CliRunner()


def test_compile_writes_all_artifacts_and_reports_phases(tmp_path):
    res = runner.invoke(app, ["compile", str(FIX), "-o", str(tmp_path)])
    assert res.exit_code == 0, res.output
    for name in ["ir.json", "graph.dot", "graph.mmd", "report.md", "report.html"]:
        assert (tmp_path / name).is_file(), name
    assert "[1/4]" in res.output and "[4/4]" in res.output
    assert "dead-code" in res.output


def test_compile_emit_subset(tmp_path):
    res = runner.invoke(app, ["compile", str(FIX), "-o", str(tmp_path), "--emit", "json"])
    assert res.exit_code == 0, res.output
    assert (tmp_path / "ir.json").is_file()
    assert not (tmp_path / "report.html").exists()


def test_compile_opt_level_zero_only_builds_graph(tmp_path):
    res = runner.invoke(app, ["compile", str(FIX), "-o", str(tmp_path), "-O0", "--emit", "json"])
    assert res.exit_code == 0, res.output
    import json

    data = json.loads((tmp_path / "ir.json").read_text())
    assert data["diagnostics"] == []
    assert len(data["nodes"]) > 10


def test_compile_warning_level_filters_terminal_output(tmp_path):
    quiet = runner.invoke(app, ["compile", str(FIX), "-o", str(tmp_path), "--emit", "json"])
    loud = runner.invoke(app, ["compile", str(FIX), "-o", str(tmp_path), "--emit", "json", "-Wall"])
    assert quiet.exit_code == 0 and loud.exit_code == 0
    assert "unused-imports/unused-import" not in quiet.output
    assert "unused-imports/unused-import" in loud.output


def test_graph_command_prints_mermaid():
    res = runner.invoke(app, ["graph", str(FIX), "--format", "mermaid"])
    assert res.exit_code == 0, res.output
    assert "flowchart LR" in res.output


def test_passes_command_lists_passes():
    res = runner.invoke(app, ["passes"])
    assert res.exit_code == 0
    assert "dead-code" in res.output and "duplicates" in res.output


def test_paths_command_shows_execution_pathways():
    res = runner.invoke(app, ["paths", str(FIX), "--from", "app.main.main"])
    assert res.exit_code == 0, res.output
    assert "helper" in res.output and "_impl" in res.output


def test_paths_between_two_symbols():
    res = runner.invoke(app, ["paths", str(FIX), "--from", "app.main.cli", "--to", "app.utils._impl"])
    assert res.exit_code == 0, res.output
    assert "process" in res.output


def test_compile_fails_with_nonzero_on_errors_when_requested(tmp_path):
    res = runner.invoke(app, ["compile", str(FIX), "-o", str(tmp_path), "--emit", "json", "--fail-on", "warning"])
    assert res.exit_code == 1
