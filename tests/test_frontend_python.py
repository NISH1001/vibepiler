from pathlib import Path

import pytest

from vibepiler.frontend.loader import discover
from vibepiler.frontend.languages import get_language
from vibepiler.ir.model import NodeKind

FIX = Path(__file__).parent / "fixtures" / "pyproj"


@pytest.fixture(scope="module")
def facts_by_file():
    files = discover(FIX)
    py = get_language("python")
    return {f.rel: py.extract(f) for f in files}


def test_module_name_strips_src_root_and_extension(facts_by_file):
    assert facts_by_file["src/app/core.py"].module_name == "app.core"
    assert facts_by_file["src/app/__init__.py"].module_name == "app"
    assert facts_by_file["tests/test_core.py"].module_name == "test_core"


def test_definitions_have_kinds_qualnames_and_lines(facts_by_file):
    facts = facts_by_file["src/app/core.py"]
    by_q = {d.qualname: d for d in facts.defs}
    assert by_q["Base"].kind is NodeKind.CLASS
    assert by_q["Base.run"].kind is NodeKind.METHOD
    assert by_q["Impl"].bases == ["Base"]
    assert by_q["Base"].bases == ["ABC"]
    assert by_q["process"].kind is NodeKind.FUNCTION
    assert by_q["process"].start_line == 20
    assert by_q["process"].params == ["x"]
    assert by_q["Base.run"].decorators == ["abstractmethod"]
    assert by_q["Impl.run"].parent_qualname == "Impl"


def test_definitions_include_module_level_variables(facts_by_file):
    facts = facts_by_file["src/app/__init__.py"]
    assert any(d.kind is NodeKind.VARIABLE and d.name == "__all__" for d in facts.defs)


def test_imports_cover_plain_from_relative_and_aliased_forms(facts_by_file):
    imps = {(i.module, i.name, i.alias, i.level) for i in facts_by_file["src/app/main.py"].imports}
    assert ("sys", None, None, 0) in imps
    assert ("app.core", "process", None, 0) in imps
    assert ("app.core", "Impl", None, 0) in imps
    assert ("app", "utils", None, 0) in imps
    assert ("models", "Config", None, 1) in imps
    core = {(i.module, i.name, i.alias) for i in facts_by_file["src/app/core.py"].imports}
    assert ("app.utils", "_impl", "impl_alias") in core


def test_import_local_name_is_the_bound_identifier(facts_by_file):
    imps = {i.local_name for i in facts_by_file["src/app/core.py"].imports}
    assert {"ABC", "abstractmethod", "helper", "impl_alias", "entry"} <= imps


def test_calls_record_chain_and_enclosing_scope(facts_by_file):
    calls = facts_by_file["src/app/main.py"].calls
    by_scope = {}
    for c in calls:
        by_scope.setdefault(c.scope, set()).add(c.chain)
    assert ("requests", "get") in by_scope["main"]
    assert ("Impl",) in by_scope["main"]
    assert ("impl", "run") in by_scope["main"]
    assert ("utils", "helper") in by_scope["main"]
    assert ("process",) in by_scope["cli"]
    assert ("Config", "load") in by_scope["cli"]


def test_module_level_calls_have_no_scope_and_main_guard_is_detected(facts_by_file):
    facts = facts_by_file["src/app/main.py"]
    module_level = {c.chain for c in facts.calls if c.scope is None}
    assert ("main",) in module_level
    assert ("sys", "exit") in module_level
    assert facts.has_main_guard is True
    assert facts_by_file["src/app/core.py"].has_main_guard is False


def test_decorators_are_recorded_as_references(facts_by_file):
    refs = facts_by_file["src/app/main.py"].refs
    assert any(r.chain == ("click", "command") and r.context == "decorator" for r in refs)


def test_self_calls_keep_self_in_chain(facts_by_file):
    calls = facts_by_file["src/app/core.py"].calls
    assert any(c.chain == ("helper",) and c.scope == "Impl.run" for c in calls)


def test_test_file_detection(facts_by_file):
    assert facts_by_file["tests/test_core.py"].is_test is True
    assert facts_by_file["src/app/core.py"].is_test is False
