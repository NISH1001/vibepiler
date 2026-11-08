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


def test_compile_prints_the_size_line_like_a_linker(tmp_path):
    res = runner.invoke(app, ["compile", str(FIX), "-o", str(tmp_path), "--emit", "json"])
    assert res.exit_code == 0, res.output
    assert "waste" in res.output and "tangle" in res.output and "bloat" in res.output and "-O2" in res.output


def test_remarks_flag_prints_missed_optimizations(tmp_path):
    res = runner.invoke(app, ["compile", str(FIX), "-o", str(tmp_path), "--emit", "json", "-R", "-q"])
    assert res.exit_code == 0, res.output
    assert "missed-optimization remarks" in res.output
    assert "remark:" in res.output and "delete" in res.output and "unused_fn" in res.output


def test_fail_on_bloat_threshold(tmp_path):
    ok = runner.invoke(app, ["compile", str(FIX), "-o", str(tmp_path), "--emit", "json", "-q", "--fail-on", "bloat>0.99"])
    assert ok.exit_code == 0, ok.output
    bad = runner.invoke(app, ["compile", str(FIX), "-o", str(tmp_path), "--emit", "json", "-q", "--fail-on", "bloat>0.01"])
    assert bad.exit_code == 1, bad.output
    assert "bloat" in bad.output


def test_baseline_prints_the_change_since_a_previous_run(tmp_path):
    first = runner.invoke(app, ["compile", str(FIX), "-o", str(tmp_path / "a"), "--emit", "json", "-q"])
    assert first.exit_code == 0, first.output
    second = runner.invoke(app, ["compile", str(FIX), "-o", str(tmp_path / "b"), "--emit", "json", "-q", "--baseline", str(tmp_path / "a" / "ir.json")])
    assert second.exit_code == 0, second.output
    assert "since baseline" in second.output and "+0" in second.output or "±0" in second.output or "no change" in second.output


def test_size_command_prints_waste_tangle_and_bloat_per_folder():
    res = runner.invoke(app, ["size", str(FIX)])
    assert res.exit_code == 0, res.output
    assert "waste" in res.output and "tangle" in res.output and "bloat" in res.output
    assert "src/app" in res.output and "remark:" in res.output


def test_fail_on_live_threshold():
    bad = runner.invoke(app, ["compile", str(FIX), "-q", "--fail-on", "live<0.999"])
    ok = runner.invoke(app, ["compile", str(FIX), "-q", "--fail-on", "live<0.0"])
    assert bad.exit_code == 1 and "live" in bad.output and ok.exit_code == 0


def test_baseline_reports_a_live_move(tmp_path):
    import json

    first = runner.invoke(app, ["compile", str(FIX), "-q", "--emit", "json", "-o", str(tmp_path)])
    ir = json.loads((tmp_path / "ir.json").read_text())
    ir["metrics"]["liveness_score"].update({"defined": 1000, "live": 123})  # the value is derived from the counts
    (tmp_path / "ir.json").write_text(json.dumps(ir))
    second = runner.invoke(app, ["compile", str(FIX), "-q", "--emit", "json", "-o", str(tmp_path / "2"), "--baseline", str(tmp_path / "ir.json")])
    assert first.exit_code == 0 and "live 12.3% →" in second.output


def test_size_command_has_a_live_column():
    res = runner.invoke(app, ["size", str(FIX)])
    assert res.exit_code == 0 and "live" in res.output and "definitions" in res.output


def test_baseline_reports_a_metric_move_and_a_metric_gates(tmp_path):
    import json

    first = runner.invoke(app, ["compile", str(FIX), "-q", "--emit", "json", "-o", str(tmp_path)])
    ir = json.loads((tmp_path / "ir.json").read_text())
    ir["metrics"]["typed_score"].update({"have": 1, "total": 100})
    (tmp_path / "ir.json").write_text(json.dumps(ir))
    second = runner.invoke(app, ["compile", str(FIX), "-q", "--emit", "json", "-o", str(tmp_path / "2"), "--baseline", str(tmp_path / "ir.json")])
    assert first.exit_code == 0 and "typed 1.0% →" in second.output
    gated = runner.invoke(app, ["compile", str(FIX), "-q", "--fail-on", "documented<1.01"])
    assert gated.exit_code == 1 and "documented" in gated.output
