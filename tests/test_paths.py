"""Which paths a compile reads: the source, the folders named inside it (include), and the paths cut out (exclude)."""

import json
import subprocess

import pytest
from typer.testing import CliRunner

from vibepiler import Compiler
from vibepiler.cli import app
from vibepiler.config import Config
from vibepiler.frontend.loader import discover
from vibepiler.remote import remote_subpath

runner = CliRunner()


@pytest.fixture
def repo(tmp_path):
    """A small monorepo: backend/, services/, scripts/ and a migrations folder."""
    files = {
        "backend/app/main.py": "from services.care import care\n\n\ndef main():\n    return care()\n",
        "backend/app/gen/schema.py": "def generated():\n    return 1\n",
        "services/care.py": "def care():\n    return 1\n",
        "scripts/once.py": "def main():\n    return 2\n",
        "frontend/index.ts": "export const x = 1;\n",
    }
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return tmp_path


def rels(files):
    return sorted(f.rel for f in files)


# ------------------------------------------------------------------ discovery
def test_discover_reads_only_the_included_paths_and_keeps_them_root_relative(repo):
    assert rels(discover(repo, include=["backend/", "services/care.py"])) == ["backend/app/gen/schema.py", "backend/app/main.py", "services/care.py"]


def test_discover_exclude_cuts_inside_the_included_paths(repo):
    assert rels(discover(repo, include=["backend/"], exclude=["**/gen/"])) == ["backend/app/main.py"]


def test_discover_names_a_missing_include_path(repo):
    with pytest.raises(FileNotFoundError, match="nowhere/"):
        discover(repo, include=["backend/", "nowhere/"])


def test_discover_include_may_not_leave_the_root(repo):
    with pytest.raises(ValueError, match="outside"):
        discover(repo / "backend", include=["../services"])


# ------------------------------------------------------------------ Compiler
def test_compiler_include_and_exclude_as_keywords(repo):
    r = Compiler(repo, include=["backend/", "services/"], exclude=["**/gen/"]).run(opt_level=1, emit=())
    assert sorted(f.rel for f in r.program.files) == ["backend/app/main.py", "services/care.py"]


def test_compiler_include_and_exclude_chain(repo):
    c = Compiler(repo).include("backend/").include("services/").exclude("**/gen/")
    assert isinstance(c, Compiler)
    r = c.run(opt_level=1, emit=())
    assert sorted(f.rel for f in r.program.files) == ["backend/app/main.py", "services/care.py"]
    # the included folders link to each other: care() is called from backend, so it is not dead
    assert not [d for d in r.diagnostics if d.code in ("dead-symbol", "unreachable-symbol") and "care" in d.message]


def test_compiler_without_include_reads_the_whole_source(repo):
    r = Compiler(repo).run(opt_level=0, emit=())
    assert len(r.program.files) == 5


def test_compiler_include_on_a_file_or_string_is_an_error(repo):
    with pytest.raises(ValueError, match="folder"):
        Compiler(repo / "services" / "care.py", include=["x/"])


# ------------------------------------------------------------------ config file
def test_config_file_include_and_exclude(repo):
    (repo / "vibepiler.toml").write_text('include = ["backend/"]\nexclude = ["**/gen/"]\n')
    r = Compiler(repo).run(opt_level=0, emit=())
    assert sorted(f.rel for f in r.program.files) == ["backend/app/main.py"]


def test_config_file_ignore_still_works_as_the_old_name(repo):
    (repo / "vibepiler.toml").write_text('ignore = ["scripts/", "frontend/"]\n')
    assert Config.load(repo).exclude == ["scripts/", "frontend/"]


# ------------------------------------------------------------------ CLI
def test_cli_paths_after_the_source_and_exclude(repo, tmp_path_factory):
    out = tmp_path_factory.mktemp("out")
    res = runner.invoke(app, ["compile", str(repo), "backend/", "services/", "--exclude", "**/gen/", "-o", str(out), "--emit", "json", "-O1"])
    assert res.exit_code == 0, res.output
    files = sorted(n["file"] for n in json.loads((out / "ir.json").read_text())["nodes"] if n["kind"] == "module")
    assert files == ["backend/app/main.py", "services/care.py"]


def test_cli_ignore_is_an_old_name_for_exclude_and_both_repeat(repo, tmp_path_factory):
    out = tmp_path_factory.mktemp("out")
    res = runner.invoke(app, ["compile", str(repo), "--ignore", "scripts/", "--exclude", "frontend/,**/gen/", "-o", str(out), "--emit", "json", "-O0"])
    assert res.exit_code == 0, res.output
    files = sorted(n["file"] for n in json.loads((out / "ir.json").read_text())["nodes"] if n["kind"] == "module")
    assert files == ["backend/app/main.py", "services/care.py"]


def test_cli_a_missing_path_is_a_clean_error(repo):
    res = runner.invoke(app, ["compile", str(repo), "nowhere/", "--emit", "json"])
    assert res.exit_code == 2 and "nowhere/" in res.output


# ------------------------------------------------------------------ remotes
def test_a_github_folder_link_names_its_folder():
    assert remote_subpath("https://github.com/a/b/tree/main/backend/app") == "backend/app"
    assert remote_subpath("https://github.com/a/b/blob/dev/src/x.py") == "src/x.py"
    assert remote_subpath("https://github.com/a/b") is None
    assert remote_subpath("a/b@dev") is None


def _git_repo(src):
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=src, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], cwd=src, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"], cwd=src, check=True)


def test_cli_remote_with_paths(repo, tmp_path_factory):
    _git_repo(repo)
    out, cache = tmp_path_factory.mktemp("out"), tmp_path_factory.mktemp("cache")
    res = runner.invoke(app, ["compile", f"file://{repo}", "services/", "-o", str(out), "--emit", "json", "-O0", "--cache", str(cache)])
    assert res.exit_code == 0, res.output
    files = sorted(n["file"] for n in json.loads((out / "ir.json").read_text())["nodes"] if n["kind"] == "module")
    assert files == ["services/care.py"]


def test_compiler_accepts_a_remote(repo, tmp_path_factory, monkeypatch):
    _git_repo(repo)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path_factory.mktemp("cache")))
    r = Compiler(f"file://{repo}", include=["services/"]).run(opt_level=0, emit=())
    assert sorted(f.rel for f in r.program.files) == ["services/care.py"]


def test_a_long_code_string_is_not_mistaken_for_a_remote():
    from vibepiler.remote import parse_remote

    code = "def f(a):\n    return a\n" * 40  # longer than any file name: asking the disk about it raises
    assert parse_remote(code) is None
    assert parse_remote("x" * 5000) is None
