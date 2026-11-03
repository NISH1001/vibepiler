from pathlib import Path

import pytest

from vibepiler.config import Config
from vibepiler.ir.builder import build_program
from vibepiler.passes import Severity
from vibepiler.passes.registry import all_passes, run_pass, run_passes

FIX = Path(__file__).parent / "fixtures" / "pyproj"


@pytest.fixture(scope="module")
def program():
    cfg = Config.load(FIX)
    cfg.thresholds.complexity = 5
    cfg.thresholds.duplicate_min_nodes = 10
    cfg.thresholds.god_module_fan = 1
    prog = build_program(FIX, config=cfg)
    run_pass("entry-points", prog)  # most passes rely on reachability
    return prog


def codes(diags, code=None):
    return [d for d in diags if code is None or d.code == code]


def names(diags):
    return {d.data.get("qualname") or d.node_id for d in diags}


# --------------------------------------------------------------------- registry
def test_registry_lists_passes_with_metadata():
    passes = all_passes()
    assert [p.name for p in passes][:2] == ["entry-points", "dead-code"]
    assert all(p.description and p.analogue for p in passes)


def test_opt_level_zero_runs_nothing(program):
    assert run_passes(program, opt_level=0) == []


# ------------------------------------------------------------------ entry-points
def test_entry_points_marks_roots_and_reachability(program):
    g = program.graph
    assert g.node("mod:src/app/main.py").attrs["is_root"] is True
    assert g.node("sym:src/app/main.py::cli").attrs["is_root"] is True
    assert g.node("sym:tests/test_core.py::test_process").attrs["is_root"] is True
    assert g.node("sym:src/app/utils.py::_impl").attrs["reachable"] is True  # via main -> helper -> _impl
    assert g.node("sym:src/app/core.py::Base").attrs["reachable"] is True  # via Impl inherits Base
    assert g.node("sym:src/app/utils.py::unused_fn").attrs["reachable"] is False
    assert program.metrics["entry_points"]["count"] >= 4


def test_entry_points_treats_parent_packages_as_imported(program):
    # `from app import utils` executes app/__init__.py too
    assert program.graph.node("mod:src/app/__init__.py").attrs["reachable"] is True


# --------------------------------------------------------------------- dead-code
def test_dead_code_flags_unreferenced_symbols(program):
    diags = run_pass("dead-code", program)
    dead = names(diags)
    assert {"uses_entry", "unused_fn", "dup_a", "dup_b", "swallow", "reraise", "OnlyOneMethod"} <= dead


def test_dead_code_is_conservative(program):
    dead = names(run_pass("dead-code", program))
    for keep in ["helper", "_impl", "process", "Impl", "Base", "Base.run", "Impl.run", "Impl.__repr__", "Config", "Config.load", "main", "cli", "test_process"]:
        assert keep not in dead, keep


def test_dead_code_reports_members_of_dead_class_only_once(program):
    dead = names(run_pass("dead-code", program))
    assert "OnlyOneMethod.compute" not in dead


def test_dead_code_summarises_removable_lines(program):
    run_pass("dead-code", program)
    assert program.metrics["dead_code"]["symbols"] >= 7
    assert program.metrics["dead_code"]["loc"] > 20


# ---------------------------------------------------------------- unused-imports
def test_unused_imports_flags_only_unused_bindings(program):
    diags = run_pass("unused-imports", program)
    flagged = {(d.file, d.data["local_name"]) for d in diags}
    assert flagged == {("src/app/utils.py", "os"), ("src/app/utils.py", "Any")}


# ------------------------------------------------------------------------ cycles
def test_cycles_reports_import_cycle_between_main_and_core(program):
    diags = run_pass("cycles", program)
    assert len(diags) == 1
    cyc = diags[0]
    assert cyc.severity is Severity.WARNING
    assert set(cyc.data["modules"]) == {"src/app/main.py", "src/app/core.py"}


# -------------------------------------------------------------------- complexity
def test_complexity_metric_is_attached_to_every_function(program):
    run_pass("complexity", program)
    g = program.graph
    assert g.node("sym:src/app/core.py::process").attrs["complexity"] == 10
    assert g.node("sym:src/app/utils.py::helper").attrs["complexity"] == 1


def test_complexity_flags_functions_over_threshold(program):
    diags = run_pass("complexity", program)
    assert "process" in names(codes(diags, "high-complexity"))
    assert "helper" not in names(diags)


# -------------------------------------------------------------------- duplicates
def test_duplicates_groups_structurally_identical_functions(program):
    diags = run_pass("duplicates", program)
    groups = [set(d.data["members"]) for d in codes(diags, "duplicate-function")]
    assert {"dup_a", "dup_b"} in groups
    assert not any("unused_fn" in g for g in groups)


# ---------------------------------------------------------------------- wrappers
def test_wrappers_flags_pass_through_function(program):
    diags = run_pass("wrappers", program)
    flagged = names(diags)
    assert "helper" in flagged
    assert next(d for d in diags if d.data["qualname"] == "helper").data["target"] == "_impl"


def test_wrappers_skips_overrides_of_base_methods(program):
    assert "Impl.run" not in names(run_pass("wrappers", program))


# ---------------------------------------------------------------------- hotspots
def test_hotspots_computes_fan_in_and_out(program):
    run_pass("hotspots", program)
    core = program.graph.node("mod:src/app/core.py").attrs
    assert core["fan_in"] == 3  # main, __init__, test_core
    assert core["fan_out"] == 2  # utils, main


def test_hotspots_flags_god_module_above_threshold(program):
    diags = run_pass("hotspots", program)
    assert "src/app/core.py" in {d.file for d in codes(diags, "god-module")}
    assert program.metrics["hotspots"]["functions"][0]["id"]


# ------------------------------------------------------------------- abstraction
def test_abstraction_flags_single_implementation_base_class(program):
    diags = run_pass("abstraction", program)
    single = codes(diags, "single-implementation")
    assert "Base" in names(single)
    assert next(d for d in single if d.data["qualname"] == "Base").data["implementations"] == ["Impl"]


def test_abstraction_flags_class_that_is_really_a_function(program):
    diags = run_pass("abstraction", program)
    assert "OnlyOneMethod" in names(codes(diags, "class-as-function"))
    assert "Config" not in names(diags)  # dataclass
    assert "Impl" not in names(codes(diags, "class-as-function"))  # implements an interface


# ------------------------------------------------------------------------- bloat
def test_bloat_flags_declared_but_unimported_dependency(program):
    diags = run_pass("bloat", program)
    unused = codes(diags, "unused-dependency")
    assert {d.data["package"] for d in unused} == {"numpy"}


def test_bloat_flags_exception_boilerplate(program):
    diags = run_pass("bloat", program)
    assert "swallow" in names(codes(diags, "swallowed-exception"))
    assert "reraise" in names(codes(diags, "redundant-try"))
    assert "process" not in names(codes(diags, "redundant-try"))


# ---------------------------------------------------------------------- pipeline
def test_run_passes_collects_all_diagnostics_in_program(program):
    diags = run_passes(program, opt_level=2)
    assert program.diagnostics == diags
    assert {d.pass_name for d in diags} >= {"dead-code", "cycles", "duplicates", "wrappers", "abstraction", "bloat"}


def test_run_passes_opt_level_one_skips_expensive_passes(program):
    program.diagnostics.clear()
    diags = run_passes(program, opt_level=1)
    assert "duplicates" not in {d.pass_name for d in diags}


def test_bloat_flags_undeclared_dependency_but_not_local_or_stdlib_modules(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0"\ndependencies = ["requests"]\n')
    (tmp_path / "app.py").write_text("import os\nimport flask\nimport requests\nimport helpers\n\ndef main():\n    return flask.Flask(__name__)\n")
    (tmp_path / "helpers.py").write_text("def h():\n    return 1\n")
    prog = build_program(tmp_path)
    diags = run_pass("bloat", prog)
    undeclared = {d.data["package"] for d in codes(diags, "undeclared-dependency")}
    assert undeclared == {"flask"}
