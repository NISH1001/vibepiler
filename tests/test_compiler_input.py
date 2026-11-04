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


# ------------------------------------------------------------------ review round: never the wrong program, never silently nothing
def test_an_empty_string_is_an_error_not_the_working_directory():
    with pytest.raises(ValueError, match="empty"):
        Compiler("")
    with pytest.raises(ValueError, match="empty"):
        Compiler("   \n  ")


def test_a_typo_with_a_space_is_an_error_not_a_snippet():
    with pytest.raises(FileNotFoundError, match="my project"):
        Compiler("my project")


def test_a_clean_one_liner_without_code_marks_is_code():
    result = Compiler("import os").run(emit=())
    assert [f.language for f in result.program.files] == ["python"]


def test_a_named_file_with_an_unknown_extension_is_an_error(tmp_path):
    (tmp_path / "notes.md").write_text("# notes\n")
    with pytest.raises(ValueError, match="notes.md"):
        Compiler(tmp_path / "notes.md")


def test_a_named_file_is_compiled_even_when_its_folder_ignores_it(tmp_path):
    (tmp_path / ".gitignore").write_text("gen.py\n")
    (tmp_path / "gen.py").write_text(PY)
    assert rels(Compiler(tmp_path / "gen.py").run(emit=())) == ["gen.py"]


def test_a_language_that_does_not_match_the_file_is_an_error(tmp_path):
    (tmp_path / "a.py").write_text(PY)
    with pytest.raises(ValueError, match="javascript"):
        Compiler(tmp_path / "a.py", language="javascript")


def test_a_single_file_is_not_checked_against_its_folders_manifest(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\ndependencies = ["requests", "numpy", "rich"]\n')
    (tmp_path / "main.py").write_text("import requests\n\n\ndef main():\n    return requests.get('x')\n")
    result = Compiler(tmp_path / "main.py").run(emit=())
    assert [d.code for d in result.program.diagnostics if d.code == "unused-dependency"] == []
    assert result.program.graph.meta["scope"] == "file"


def test_a_string_program_ignores_config_in_the_working_directory(tmp_path, monkeypatch):
    (tmp_path / "<string>").mkdir()
    (tmp_path / "<string>" / "vibepiler.toml").write_text("opt_level = 1\n")
    monkeypatch.chdir(tmp_path)
    assert Compiler(PY).config.opt_level == 2


def test_a_tsx_snippet_is_detected_as_typescript_with_jsx():
    result = Compiler("const App = ({ name }: { name: string }) => <div>{name}</div>\nexport default App\n").run(emit=())
    sf = result.program.files[0]
    assert (sf.language, sf.rel) == ("typescript", "snippet.tsx")
    assert not sf.tree.root_node.has_error


def test_the_cli_loads_the_config_from_a_files_folder(tmp_path):
    from typer.testing import CliRunner

    from vibepiler.cli import app

    (tmp_path / "a.py").write_text(PY)
    (tmp_path / "pyproject.toml").write_text("[tool.vibepiler]\nopt_level = 1\n")
    res = CliRunner().invoke(app, ["compile", str(tmp_path / "a.py"), "--emit", "json", "-o", str(tmp_path / "out")])
    assert res.exit_code == 0, res.output
    assert "-O1:" in res.output


def test_compile_is_discoverable_on_the_package():
    assert "compile" in dir(vibepiler)


# ------------------------------------------------------------------ the typed result
def test_result_exposes_typed_scores_and_shortcuts():
    r = Compiler(PY).run(emit=())
    assert set(r.scores) == {"bloat", "live"} and r.bloat.value == r.scores["bloat"].value and r.liveness.value == r.scores["live"].value
    assert r.diagnostics is r.program.diagnostics and r.graph is r.program.graph and r.metrics is r.program.metrics
    assert r.to_dict()["metrics"]["liveness_score"]["value"] == r.liveness.value


def test_scores_are_absent_at_opt_zero():
    r = Compiler(PY).run(opt_level=0, emit=())
    assert r.scores == {} and r.bloat is None and r.liveness is None
