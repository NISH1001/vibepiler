from pathlib import Path

import pytest

from vibepiler.config import Config
from vibepiler.ir.builder import build_program
from vibepiler.passes import Severity
from vibepiler.passes.registry import run_pass

FIX = Path(__file__).parent / "fixtures" / "jsproj"


@pytest.fixture(scope="module")
def program():
    cfg = Config.load(FIX)
    cfg.thresholds.duplicate_min_nodes = 10
    prog = build_program(FIX, config=cfg)
    run_pass("entry-points", prog)
    return prog


def names(diags, code=None):
    return {d.data.get("qualname") for d in diags if code is None or d.code == code}


def test_test_module_and_entry_modules_are_roots(program):
    g = program.graph
    assert g.node("mod:src/__tests__/math.test.ts").attrs["is_root"] is True
    assert "test" in g.node("mod:src/__tests__/math.test.ts").attrs["entry"]
    assert g.node("mod:src/index.ts").attrs["is_root"] is True
    assert g.node("sym:src/utils/math.ts::compute").attrs["reachable"] is True
    assert g.node("sym:src/components/Header.tsx::Header").attrs["reachable"] is True


def test_dead_exported_function_is_reported_as_unused_export(program):
    diags = run_pass("dead-code", program)
    unused = [d for d in diags if d.code == "unused-export"]
    assert "neverUsed" in names(unused)
    assert all(d.severity is Severity.INFO for d in unused)
    assert "internalHelper" in names(diags, "dead-symbol")
    for keep in ["add", "multiply", "double", "compute", "App", "Header", "UserService", "UserService.find", "BaseService.log", "shout"]:
        assert keep not in names(diags), keep


def test_unreachable_module_is_reported(program):
    diags = run_pass("dead-code", program)
    assert "src/legacy.js" in {d.file for d in diags if d.code == "unreachable-module"}


def test_unused_dependency_and_no_false_undeclared(program):
    diags = run_pass("bloat", program)
    assert {d.data["package"] for d in diags if d.code == "unused-dependency"} == {"lodash"}
    assert not [d for d in diags if d.code == "undeclared-dependency"]


def test_exception_boilerplate_in_js(program):
    diags = run_pass("bloat", program)
    assert "UserService.lookup" in names(diags, "swallowed-exception")
    assert "UserService.save" in names(diags, "redundant-try")


def test_stub_function_detected(program):
    diags = run_pass("bloat", program)
    assert "todo" in names(diags, "stub-function")
    assert "BaseService.find" not in names(diags, "stub-function")  # abstract signature


def test_wrapper_detected(program):
    diags = run_pass("wrappers", program)
    flagged = {d.data["qualname"]: d for d in diags}
    assert "addWrapper" in flagged and flagged["addWrapper"].code == "pass-through-wrapper"
    assert flagged["addWrapper"].data["target"] == "add"


def test_duplicates_detected(program):
    diags = run_pass("duplicates", program)
    groups = [set(d.data["members"]) for d in diags if d.code == "duplicate-function"]
    assert {"scaleAbove", "scaleAboveCopy"} in groups


def test_single_implementation_abstraction(program):
    diags = run_pass("abstraction", program)
    single = [d for d in diags if d.code == "single-implementation"]
    assert "BaseService" in names(single)


def test_complexity_counts_js_branches(program):
    run_pass("complexity", program)
    assert program.graph.node("sym:src/utils/math.ts::neverUsed").attrs["complexity"] == 3  # for + if
    assert program.graph.node("sym:src/utils/math.ts::add").attrs["complexity"] == 1
