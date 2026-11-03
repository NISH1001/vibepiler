from pathlib import Path

import pytest

from vibepiler.frontend.languages import get_language
from vibepiler.frontend.loader import discover
from vibepiler.ir.model import NodeKind

FIX = Path(__file__).parent / "fixtures" / "jsproj"


@pytest.fixture(scope="module")
def facts_by_file():
    out = {}
    for f in discover(FIX):
        out[f.rel] = get_language(f.language).extract(f)
    return out


def test_module_names_are_extensionless_paths_with_src_and_index_aliases(facts_by_file):
    assert facts_by_file["src/utils/math.ts"].module_name == "src/utils/math"
    assert "utils/math" in facts_by_file["src/utils/math.ts"].aliases
    idx = facts_by_file["src/utils/index.ts"]
    assert idx.module_name == "src/utils/index"
    assert {"src/utils", "utils", "utils/index"} <= set(idx.aliases)
    assert facts_by_file["src/__tests__/math.test.ts"].module_name == "src/__tests__/math.test"


def test_definitions_with_export_and_default_flags(facts_by_file):
    by_q = {d.qualname: d for d in facts_by_file["src/utils/math.ts"].defs}
    assert by_q["add"].kind is NodeKind.FUNCTION and by_q["add"].exported is True
    assert by_q["double"].kind is NodeKind.FUNCTION  # arrow function assigned to const
    assert by_q["internalHelper"].exported is False
    assert by_q["compute"].attrs["default_export"] is True and by_q["compute"].exported is True
    assert by_q["add"].params == ["a", "b"]
    assert by_q["add"].start_line == 1 and by_q["add"].end_line == 3


def test_class_methods_bases_and_ts_declarations(facts_by_file):
    user = {d.qualname: d for d in facts_by_file["src/services/user.ts"].defs}
    assert user["UserService"].kind is NodeKind.CLASS
    assert user["UserService"].bases == ["BaseService"]
    assert user["UserService.find"].kind is NodeKind.METHOD
    assert user["UserService.find"].parent_qualname == "UserService"
    assert user["UserService.find"].is_async is True
    base = {d.qualname: d for d in facts_by_file["src/services/base.ts"].defs}
    assert base["BaseService"].attrs["is_abstract"] is True
    assert base["Repository"].kind is NodeKind.CLASS and base["Repository"].attrs["is_interface"] is True
    assert "abstract" in base["BaseService.find"].decorators


def test_module_level_variables(facts_by_file):
    defs = {d.qualname: d for d in facts_by_file["src/index.ts"].defs}
    assert defs["app"].kind is NodeKind.VARIABLE
    assert defs["bootstrap"].kind is NodeKind.FUNCTION


def test_imports_of_every_form(facts_by_file):
    imps = {(i.module, i.name, i.alias, i.kind) for i in facts_by_file["src/index.ts"].imports}
    assert ("express", "default", "express", "default") in imps
    assert ("./utils/math", "add", None, "member") in imps
    assert ("@/services/user", "UserService", None, "member") in imps
    assert ("./utils/math", None, "mathNs", "namespace") in imps
    assert ("./polyfill", None, None, "side_effect") in imps
    legacy = {(i.module, i.name, i.alias, i.kind) for i in facts_by_file["src/legacy.js"].imports}
    assert ("./utils/math", None, "math", "module") in legacy
    assert ("./utils/math", "add", None, "member") in legacy
    reexp = {(i.module, i.name, i.kind) for i in facts_by_file["src/utils/index.ts"].imports}
    assert ("./math", "add", "member") in reexp
    assert ("./strings", None, "wildcard") in reexp


def test_import_local_names(facts_by_file):
    names = {i.local_name for i in facts_by_file["src/index.ts"].imports}
    assert {"express", "add", "multiply", "UserService", "mathNs", "shout"} <= names


def test_calls_with_scopes(facts_by_file):
    calls = facts_by_file["src/index.ts"].calls
    by_scope = {}
    for c in calls:
        by_scope.setdefault(c.scope, set()).add(c.chain)
    assert ("mathNs", "double") in by_scope["bootstrap"]
    assert ("multiply",) in by_scope["bootstrap"]
    assert ("express",) in by_scope[None]
    assert ("app", "listen") in by_scope[None]
    assert ("add",) in by_scope[None]  # inside an anonymous route callback -> module scope
    assert ("bootstrap",) in by_scope[None]
    new_calls = [c for c in calls if c.is_new]
    assert new_calls and new_calls[0].chain == ("UserService",)


def test_this_calls_and_method_scope(facts_by_file):
    calls = facts_by_file["src/services/user.ts"].calls
    assert any(c.chain == ("this", "log") and c.scope == "UserService.find" for c in calls)
    assert any(c.chain == ("this", "lookup") and c.scope == "UserService.find" for c in calls)


def test_jsx_elements_are_references(facts_by_file):
    refs = facts_by_file["src/components/App.tsx"].refs
    assert any(r.chain == ("Header",) and r.context == "jsx" and r.scope == "App" for r in refs)
    assert not any(r.chain == ("div",) for r in refs)  # intrinsic elements are not references


def test_module_exports_marks_commonjs_exports(facts_by_file):
    defs = {d.qualname: d for d in facts_by_file["src/legacy.js"].defs}
    assert defs["legacySum"].exported is True


def test_entry_hints_and_test_detection(facts_by_file):
    idx = facts_by_file["src/index.ts"]
    kinds = {h.kind for h in idx.entry_hints}
    assert {"route", "main"} <= kinds
    test = facts_by_file["src/__tests__/math.test.ts"]
    assert test.is_test is True
    assert any(h.kind == "test" for h in test.entry_hints)
    assert facts_by_file["src/utils/math.ts"].is_test is False


def test_entry_module_detection():
    spec = get_language("typescript")
    assert spec.is_entry_module("src/index.ts") is True
    assert spec.is_entry_module("src/main.tsx") is True
    assert spec.is_entry_module("app/dashboard/page.tsx") is True
    assert spec.is_entry_module("pages/api/hello.ts") is True
    assert spec.is_entry_module("vite.config.ts") is True
    assert spec.is_entry_module("src/components/App.tsx") is False
    assert spec.is_entry_module("src/utils/math.ts") is False
