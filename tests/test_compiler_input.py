"""Compiler(source): a directory, a single file, or a code string."""

from pathlib import Path

import pytest

import vibepiler
from vibepiler.compiler import Compiler, CompileResult

FIX = Path(__file__).parent / "fixtures" / "pyproj"

PY = "def f():\n    return 1\n\n\ndef g():\n    return f()\n"
JS = "function f() { return 1 }\nexport default f\n"
TS = "function f(x: number): number { return x }\nexport default f\n"


def rels(result):
    return sorted(f.rel for f in result.program.files)


def symbols(result):
    return {n.name for n in result.program.graph.nodes() if n.kind.value in ("function", "class")}


# ------------------------------------------------------------------ directory
def test_a_directory_is_discovered_as_before():
    result = Compiler(FIX).run(emit=())
    assert "src/app/core.py" in rels(result)
    assert result.program.root == FIX.resolve()


# ------------------------------------------------------------------ a file
def test_a_single_file_compiles_only_that_file(tmp_path):
    (tmp_path / "a.py").write_text(PY)
    (tmp_path / "b.py").write_text("def unused():\n    pass\n")
    result = Compiler(tmp_path / "a.py").run(emit=())
    assert rels(result) == ["a.py"]
    assert result.program.root == tmp_path.resolve()


def test_a_single_file_loads_the_config_from_its_folder(tmp_path):
    (tmp_path / "a.py").write_text(PY)
    (tmp_path / "pyproject.toml").write_text("[tool.vibepiler]\nopt_level = 1\n")
    assert Compiler(str(tmp_path / "a.py")).config.opt_level == 1


# ------------------------------------------------------------------ a code string
def test_a_python_string_is_parsed_as_python():
    result = Compiler(PY).run(emit=())
    assert [f.language for f in result.program.files] == ["python"]
    assert {"f", "g"} <= symbols(result)
    assert result.program.root.name == "<string>"


def test_a_javascript_string_is_detected():
    result = Compiler(JS).run(emit=())
    assert [f.language for f in result.program.files] == ["javascript"]
    assert "f" in symbols(result)


def test_a_typed_string_is_detected_as_typescript():
    result = Compiler(TS).run(emit=())
    assert [f.language for f in result.program.files] == ["typescript"]


def test_language_keyword_wins_over_detection():
    result = Compiler("x = 1\n", language="javascript").run(emit=())
    assert [f.language for f in result.program.files] == ["javascript"]


def test_a_one_line_snippet_is_code_not_a_path():
    result = Compiler("def f(): pass").run(emit=())
    assert "f" in symbols(result)


def test_a_string_emits_every_report_into_the_out_dir(tmp_path):
    result = Compiler(PY).run(emit=["json", "html", "md"], out_dir=tmp_path)
    assert sorted(p.name for p in result.outputs.values()) == ["ir.json", "report.html", "report.md"]
    html = (tmp_path / "report.html").read_text()
    assert "<title>Vibepiler: &lt;string&gt;</title>" in html  # the name is text in the markup...
    assert '"root":"<string>"' in html  # ...and the true, unresolved value in the data


# ------------------------------------------------------------------ errors
def test_a_missing_path_is_an_error_not_a_snippet():
    with pytest.raises(FileNotFoundError, match="no/such/dir"):
        Compiler("no/such/dir")


def test_a_missing_path_object_is_an_error():
    with pytest.raises(FileNotFoundError, match="nowhere"):
        Compiler(Path("nowhere"))


def test_an_unknown_language_is_an_error():
    with pytest.raises(ValueError, match="cobol"):
        Compiler(PY, language="cobol")


# ------------------------------------------------------------------ the function
def test_vibepiler_compile_is_the_one_call_api():
    result = vibepiler.compile(PY, emit=())
    assert isinstance(result, CompileResult)
    assert "g" in symbols(result)
