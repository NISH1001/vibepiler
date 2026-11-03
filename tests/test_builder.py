from pathlib import Path

import pytest

from vibepiler.ir.builder import build_program
from vibepiler.ir.model import EdgeKind, NodeKind

FIX = Path(__file__).parent / "fixtures" / "pyproj"


@pytest.fixture(scope="module")
def program():
    return build_program(FIX)


@pytest.fixture(scope="module")
def g(program):
    return program.graph


def test_every_source_file_becomes_a_module_node(g):
    names = {n.name for n in g.nodes(NodeKind.MODULE)}
    assert {"app", "app.main", "app.core", "app.utils", "app.models", "test_core"} <= names


def test_module_ids_are_path_based_and_symbol_ids_are_module_scoped(g):
    assert g.has_node("mod:src/app/core.py")
    assert g.has_node("sym:src/app/core.py::Impl.run")
    assert g.node("sym:src/app/core.py::Impl.run").kind is NodeKind.METHOD
    assert g.node("sym:src/app/core.py::Impl.run").module == "mod:src/app/core.py"


def test_contains_edges_nest_module_class_method(g):
    assert [c.id for c in g.children("mod:src/app/core.py") if c.kind is NodeKind.CLASS] == [
        "sym:src/app/core.py::Base",
        "sym:src/app/core.py::Impl",
    ]
    assert {c.name for c in g.children("sym:src/app/core.py::Impl")} == {"run", "__repr__"}


def test_internal_imports_link_modules_and_external_imports_link_packages(g):
    targets = {e.dst for e in g.out_edges("mod:src/app/main.py", EdgeKind.IMPORTS)}
    assert "mod:src/app/core.py" in targets
    assert "mod:src/app/models.py" in targets  # relative import
    assert "mod:src/app/utils.py" in targets  # `from app import utils` resolves to the submodule
    assert {"pkg:sys", "pkg:click", "pkg:requests"} <= targets


def test_direct_call_to_imported_function_resolves(g):
    callees = {e.dst for e in g.out_edges("sym:src/app/main.py::cli", EdgeKind.CALLS)}
    assert "sym:src/app/core.py::process" in callees


def test_call_through_imported_submodule_resolves(g):
    callees = {e.dst for e in g.out_edges("sym:src/app/main.py::main", EdgeKind.CALLS)}
    assert "sym:src/app/utils.py::helper" in callees


def test_aliased_import_resolves(g):
    callees = {e.dst for e in g.out_edges("sym:src/app/core.py::process", EdgeKind.CALLS)}
    assert "sym:src/app/utils.py::_impl" in callees


def test_class_method_call_via_class_name_resolves(g):
    callees = {e.dst for e in g.out_edges("sym:src/app/main.py::cli", EdgeKind.CALLS)}
    assert "sym:src/app/models.py::Config.load" in callees


def test_instantiation_is_a_call_to_the_class(g):
    callees = {e.dst for e in g.out_edges("sym:src/app/main.py::main", EdgeKind.CALLS)}
    assert "sym:src/app/core.py::Impl" in callees


def test_call_into_external_package_links_package_with_member(g):
    edges = [e for e in g.out_edges("sym:src/app/main.py::main", EdgeKind.CALLS) if e.dst == "pkg:requests"]
    assert edges and edges[0].attrs["members"] == ["get"]


def test_module_level_code_calls_are_attributed_to_the_module(g):
    callees = {e.dst for e in g.out_edges("mod:src/app/main.py", EdgeKind.CALLS)}
    assert "sym:src/app/main.py::main" in callees
    assert "pkg:sys" in callees


def test_inheritance_edges(g):
    assert [e.dst for e in g.out_edges("sym:src/app/core.py::Impl", EdgeKind.INHERITS)] == ["sym:src/app/core.py::Base"]
    assert [e.dst for e in g.out_edges("sym:src/app/core.py::Base", EdgeKind.INHERITS)] == ["pkg:abc"]


def test_call_through_module_alias_import_resolves(g):
    # `from app import main as entry` ; `entry.main()`
    callees = {e.dst for e in g.out_edges("sym:src/app/core.py::uses_entry", EdgeKind.CALLS)}
    assert "sym:src/app/main.py::main" in callees


def test_unresolved_method_calls_are_recorded_by_name(program):
    assert "run" in program.graph.meta["used_member_names"]
    assert program.graph.meta["used_member_names"]["run"] >= 1


def test_entry_hints_land_on_node_attrs(g):
    assert "main_guard" in g.node("mod:src/app/main.py").attrs["entry"]
    assert "command" in g.node("sym:src/app/main.py::cli").attrs["entry"]
    assert "test" in g.node("sym:tests/test_core.py::test_process").attrs["entry"]


def test_import_bindings_record_usage(g):
    imports = {i["local_name"]: i for i in g.node("mod:src/app/utils.py").attrs["imports"]}
    assert imports["os"]["used"] is False
    assert imports["Any"]["used"] is False
    assert imports["json"]["used"] is True


def test_resolution_stats_are_reported(g):
    stats = g.meta["resolution"]
    assert stats["calls_total"] > 0
    assert 0 < stats["calls_resolved"] <= stats["calls_total"]


def test_package_init_reexport_is_followed(g):
    # tests/test_core.py: `from app.core import process` is direct; also check `from app import process` style via __init__
    assert g.has_node("pkg:requests")
    assert "mod:src/app/core.py" in {e.dst for e in g.out_edges("mod:src/app/__init__.py", EdgeKind.IMPORTS)}


def test_program_exposes_facts_per_module(program):
    facts = program.facts_for("mod:src/app/core.py")
    assert facts.module_name == "app.core"
    assert any(d.qualname == "process" for d in facts.defs)
