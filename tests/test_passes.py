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



def test_script_metadata_block_lists_its_dependencies():
    from vibepiler.passes.manifests import script_dependencies

    text = '# /// script\n# requires-python = ">=3.12"\n# dependencies = [\n#   "matplotlib>=3.8",\n#   "Pillow",\n# ]\n# ///\nimport matplotlib\n'
    assert script_dependencies(text) == {"matplotlib", "pillow"}
    assert script_dependencies("import matplotlib\n") == set()
    assert script_dependencies("# /// script\n# dependencies = [\n") == set()  # unterminated block: nothing declared


def test_bloat_honours_a_script_that_declares_its_own_dependencies(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0"\ndependencies = ["requests"]\n')
    (tmp_path / "app.py").write_text("import requests\nimport matplotlib\n")
    (tmp_path / "plot.py").write_text('# /// script\n# dependencies = ["matplotlib", "pillow"]\n# ///\nimport matplotlib\nfrom PIL import Image\n')
    prog = build_program(tmp_path)
    diags = codes(run_pass("bloat", prog), "undeclared-dependency")
    assert [(d.data["package"], d.data["files"]) for d in diags] == [("matplotlib", ["app.py"])]  # app.py still owes it; plot.py does not


def test_dead_code_stamps_the_verdict_on_the_node(program):
    diags = run_pass("dead-code", program)
    assert diags and all(program.graph.node(d.node_id).attrs.get("dead") == d.code for d in diags if d.node_id)


def test_accepted_findings_stamp_their_node():
    cfg = Config.load(FIX)
    cfg.accept = ["dead-symbol:*"]
    prog = build_program(FIX, config=cfg)
    run_passes(prog, opt_level=1)
    assert any(n.attrs.get("accepted") for n in prog.graph.nodes()) and not any(d.code == "dead-symbol" for d in prog.diagnostics)


def test_migration_files_are_loaded_by_the_framework_not_dead(tmp_path):
    (tmp_path / "alembic" / "versions").mkdir(parents=True)
    (tmp_path / "app.py").write_text("def main():\n    return 1\n")
    (tmp_path / "alembic" / "versions" / "001_initial.py").write_text(
        'revision = "001"\n\n\ndef upgrade():\n    _helper()\n\n\ndef downgrade():\n    pass\n\n\ndef _helper():\n    return 1\n'
    )
    diags = run_pass("dead-code", build_program(tmp_path))
    assert [d.message for d in diags if "001_initial" in (d.file or "")] == []


def test_a_nested_package_json_declares_its_own_folder_s_imports(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "root", "devDependencies": {"prettier": "3"}}')
    (tmp_path / "frontend" / "src").mkdir(parents=True)
    (tmp_path / "frontend" / "package.json").write_text('{"name": "fe", "dependencies": {"react": "19", "left-pad": "1"}}')
    (tmp_path / "frontend" / "src" / "index.tsx").write_text('import { useState } from "react";\nimport axios from "axios";\nexport const a = [useState, axios];\n')
    diags = run_pass("bloat", build_program(tmp_path))
    undeclared = [(d.data["package"], d.message.split(" in ")[-1]) for d in codes(diags, "undeclared-dependency")]
    unused = [(d.data["package"], d.file) for d in codes(diags, "unused-dependency")]
    assert undeclared == [("axios", "frontend/package.json")]  # react is declared by the folder's own manifest
    assert unused == [("left-pad", "frontend/package.json")]


def test_migrations_repeat_by_design_and_alembic_env_is_framework_run(tmp_path):
    v = tmp_path / "alembic" / "versions"
    v.mkdir(parents=True)
    (tmp_path / "app.py").write_text("def main():\n    return 1\n")
    (tmp_path / "alembic" / "env.py").write_text("def run_migrations_online():\n    return 1\n")
    body = "def upgrade():\n    op.add_column('t', sa.Column('a', sa.String()))\n    op.create_index('i', 't', ['a'])\n    op.drop_column('t', 'b')\n    op.alter_column('t', 'c', nullable=True)\n"
    (v / "001_a.py").write_text(body)
    (v / "002_b.py").write_text(body)
    prog = build_program(tmp_path)
    assert [d.message for d in run_pass("duplicates", prog) if d.code == "duplicate-function"] == []
    assert [d.message for d in run_pass("dead-code", prog) if (d.file or "").startswith("alembic")] == []


def test_a_nested_pyproject_declares_its_own_folder_s_imports(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "root"\nversion = "0"\ndependencies = ["click"]\n')
    (tmp_path / "cli.py").write_text("import click\n")
    (tmp_path / "backend" / "app").mkdir(parents=True)
    (tmp_path / "backend" / "pyproject.toml").write_text('[project]\nname = "b"\nversion = "0"\ndependencies = ["fastapi"]\n')
    (tmp_path / "backend" / "app" / "main.py").write_text("import fastapi\nimport httpx\n")
    diags = run_pass("bloat", build_program(tmp_path))
    assert [(d.data["package"], d.message.split(" in ")[-1]) for d in codes(diags, "undeclared-dependency")] == [("httpx", "backend/pyproject.toml")]
    assert codes(diags, "unused-dependency") == []  # click is used by the root, fastapi by backend/


def test_code_only_tests_reach_is_reported_as_test_only_not_dead(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "app" / "main.py").write_text("from app.lib import used\n\n\ndef main():\n    return used()\n")
    (tmp_path / "app" / "lib.py").write_text(
        "def used():\n    return 1\n\n\ndef only_tested():\n    return _inner()\n\n\ndef _inner():\n    return 2\n\n\n"
        "class OnlyTested:\n    def go(self):\n        return 3\n"
    )
    (tmp_path / "app" / "legacy.py").write_text("def old():\n    return 4\n")
    (tmp_path / "tests" / "test_lib.py").write_text(
        "from app.lib import only_tested, OnlyTested\nfrom app.legacy import old\n\n\n"
        "def test_it():\n    assert only_tested() and OnlyTested().go() and old()\n"
    )
    prog = build_program(tmp_path)
    run_pass("entry-points", prog)
    diags = run_pass("dead-code", prog)
    test_only = sorted((d.file, d.data["qualname"]) for d in diags if d.code == "test-only")
    # the outermost unit only: a test-only module, not each function in it; a class, not each method
    assert test_only == [("app/legacy.py", "legacy"), ("app/lib.py", "OnlyTested"), ("app/lib.py", "_inner"), ("app/lib.py", "only_tested")]
    assert not [d for d in diags if d.code in ("dead-symbol", "unreachable-symbol", "unreachable-module")]
    assert prog.metrics["dead_code"]["symbols"] == 0  # not dead: nothing to delete, something to look at


def test_import_module_with_a_literal_name_is_an_import(tmp_path):
    (tmp_path / "pkg" / "measures").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "measures" / "__init__.py").write_text("")
    (tmp_path / "main.py").write_text("from pkg.registry import ALL\n\n\ndef main():\n    return ALL\n")
    (tmp_path / "pkg" / "registry.py").write_text(
        "import importlib\nfrom importlib import import_module\n\nALL = []\n\n\ndef register(c):\n    ALL.append(c)\n    return c\n\n\n"
        'import_module("pkg.measures.judge")\nimportlib.import_module("pkg.measures.safety")\n'
    )
    for name in ("judge", "safety"):
        (tmp_path / "pkg" / "measures" / f"{name}.py").write_text(f"from pkg.registry import register\n\n\n@register\nclass {name.title()}:\n    pass\n")
    (tmp_path / "tests" / "test_m.py").write_text("from pkg.measures.judge import Judge\n\n\ndef test_j():\n    assert Judge\n")
    prog = build_program(tmp_path)
    from vibepiler.ir.model import EdgeKind

    targets = {e.dst for e in prog.graph.out_edges("mod:pkg/registry.py", EdgeKind.IMPORTS)}
    assert {"mod:pkg/measures/judge.py", "mod:pkg/measures/safety.py"} <= targets
    run_pass("entry-points", prog)
    diags = run_pass("dead-code", prog)
    assert not [d.message for d in diags if "measures" in (d.file or "")]


def test_imports_under_type_checking_or_inside_a_function_do_not_close_a_cycle(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text(
        "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from pkg.driver import run\n\n\n"
        "def __getattr__(name):\n    from pkg import driver\n\n    return getattr(driver, name)\n"
    )
    (tmp_path / "pkg" / "driver.py").write_text("from pkg import VERSION\n\n\ndef run():\n    return VERSION\n")
    (tmp_path / "pkg" / "a.py").write_text("from pkg.b import y\n\nx = 1\n")
    (tmp_path / "pkg" / "b.py").write_text("y = 2\n\n\ndef f():\n    from pkg.a import x\n\n    return x\n")
    prog = build_program(tmp_path)
    assert run_pass("cycles", prog) == []
    assert [d.message for d in run_pass("unused-imports", prog) if "TYPE_CHECKING" in d.message] == []


def test_an_import_in_the_else_of_type_checking_still_runs(tmp_path):
    (tmp_path / "a.py").write_text("from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    pass\nelse:\n    from b import y\n")
    (tmp_path / "b.py").write_text("from a import TYPE_CHECKING\n\ny = 1\n")
    assert len(run_pass("cycles", build_program(tmp_path))) == 1


def test_pytest_importorskip_with_a_literal_name_uses_the_dependency(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "p"\nversion = "0"\ndependencies = []\n\n[dependency-groups]\ndev = ["complexipy"]\n')
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text('import pytest\n\n\ndef test_x():\n    cx = pytest.importorskip("complexipy")\n    assert cx\n')
    assert codes(run_pass("bloat", build_program(tmp_path)), "unused-dependency") == []


def test_what_a_top_level_package_re_exports_is_public_api_not_test_only(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "lib" / "__init__.py").write_text(
        "from typing import TYPE_CHECKING\n\nfrom lib.core import load, _private\n\nif TYPE_CHECKING:\n    from lib.core import compile\n"
    )
    (tmp_path / "lib" / "core.py").write_text(
        "def load():\n    return 1\n\n\ndef compile():\n    return 2\n\n\ndef _private():\n    return 3\n\n\ndef helper():\n    return 4\n"
    )
    (tmp_path / "tests" / "test_core.py").write_text(
        "from lib.core import _private, compile, helper, load\n\n\ndef test_all():\n    assert load() + compile() + _private() + helper()\n"
    )
    prog = build_program(tmp_path)
    run_pass("entry-points", prog)
    test_only = sorted(d.data["qualname"] for d in run_pass("dead-code", prog) if d.code == "test-only")
    assert test_only == ["_private", "helper"]  # load and compile are the package's API; underscores never are


def test_all_narrows_the_public_api_of_a_package(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "lib" / "__init__.py").write_text('from lib.core import a, b\n\n__all__ = ["a"]\n')
    (tmp_path / "lib" / "core.py").write_text("def a():\n    return 1\n\n\ndef b():\n    return 2\n")
    (tmp_path / "tests" / "test_core.py").write_text("from lib.core import a, b\n\n\ndef test_ab():\n    assert a() + b()\n")
    prog = build_program(tmp_path)
    run_pass("entry-points", prog)
    assert [d.data["qualname"] for d in run_pass("dead-code", prog) if d.code == "test-only"] == ["b"]


def test_names_in_a_string_annotation_are_used(tmp_path):
    (tmp_path / "m.py").write_text(
        "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    import os\n    from pathlib import Path\n    from collections import OrderedDict\n\n\n"
        'def main(p: "Path", q: list["os.PathLike"]) -> "dict[str, int]":\n    x: "OrderedDict | None" = None\n    return {str(p): len(q), "x": x}\n'
    )
    assert run_pass("unused-imports", build_program(tmp_path)) == []
