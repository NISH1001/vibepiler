from pathlib import Path

import pytest

from vibepiler.ir.builder import build_program
from vibepiler.ir.model import EdgeKind, NodeKind

FIX = Path(__file__).parent / "fixtures" / "jsproj"


@pytest.fixture(scope="module")
def g():
    return build_program(FIX).graph


def test_relative_alias_and_package_imports_resolve(g):
    targets = {e.dst for e in g.out_edges("mod:src/index.ts", EdgeKind.IMPORTS)}
    assert "mod:src/utils/math.ts" in targets
    assert "mod:src/services/user.ts" in targets  # @/ alias
    assert "mod:src/utils/index.ts" in targets  # directory import -> index
    assert "mod:src/polyfill.ts" in targets  # side-effect import
    assert "pkg:express" in targets


def test_default_import_resolves_to_default_exported_symbol(g):
    callees = {e.dst for e in g.out_edges("mod:src/__tests__/math.test.ts", EdgeKind.CALLS)}
    assert "sym:src/utils/math.ts::compute" in callees
    assert "sym:src/utils/math.ts::add" in callees


def test_new_expression_links_to_class(g):
    callees = {e.dst for e in g.out_edges("mod:src/index.ts", EdgeKind.CALLS)}
    assert "sym:src/services/user.ts::UserService" in callees


def test_this_method_calls_resolve_including_inherited(g):
    callees = {e.dst for e in g.out_edges("sym:src/services/user.ts::UserService.find", EdgeKind.CALLS)}
    assert "sym:src/services/user.ts::UserService.lookup" in callees
    assert "sym:src/services/base.ts::BaseService.log" in callees


def test_inheritance_edge(g):
    assert [e.dst for e in g.out_edges("sym:src/services/user.ts::UserService", EdgeKind.INHERITS)] == ["sym:src/services/base.ts::BaseService"]


def test_jsx_usage_creates_reference_edges(g):
    refs = {e.dst for e in g.out_edges("sym:src/components/App.tsx::App", EdgeKind.REFERENCES)}
    assert "sym:src/components/Header.tsx::Header" in refs
    main_refs = {e.dst for e in g.out_edges("mod:src/main.tsx", EdgeKind.REFERENCES)}
    assert "sym:src/components/App.tsx::App" in main_refs


def test_namespace_import_call_resolves(g):
    callees = {e.dst for e in g.out_edges("sym:src/index.ts::bootstrap", EdgeKind.CALLS)}
    assert "sym:src/utils/math.ts::double" in callees
    assert "sym:src/utils/math.ts::multiply" in callees


def test_require_edges_resolve(g):
    assert "mod:src/utils/math.ts" in {e.dst for e in g.out_edges("mod:src/legacy.js", EdgeKind.IMPORTS)}
    callees = {e.dst for e in g.out_edges("sym:src/legacy.js::legacySum", EdgeKind.CALLS)}
    assert "sym:src/utils/math.ts::add" in callees


def test_reexports_are_followed(g):
    # `import { shout } from './utils'` -> utils/index.ts -> `export * from './strings'`
    callees = {e.dst for e in g.out_edges("mod:src/index.ts", EdgeKind.CALLS)}
    assert "sym:src/utils/strings.ts::shout" in callees


def test_package_calls_record_members(g):
    edges = [e for e in g.out_edges("mod:src/main.tsx", EdgeKind.CALLS) if e.dst == "pkg:react-dom"]
    assert edges and "client.createRoot" in edges[0].attrs["members"]


def test_node_builtins_are_namespaced(g):
    assert not g.has_node("pkg:fs")


def test_unresolved_receiver_methods_land_in_name_registry(g):
    assert g.meta["used_member_names"].get("find", 0) >= 1
