import subprocess
from pathlib import Path

from typer.testing import CliRunner

from vibepiler.cli import app
from vibepiler.remote import fetch_remote, parse_remote

FIX = Path(__file__).parent / "fixtures" / "pyproj"
runner = CliRunner()


def test_owner_repo_shorthand_is_github():
    assert parse_remote("igaurab/gpython") == ("https://github.com/igaurab/gpython", None)


def test_shorthand_with_ref():
    assert parse_remote("igaurab/gpython@dev") == ("https://github.com/igaurab/gpython", "dev")


def test_full_github_urls():
    assert parse_remote("https://github.com/a/b") == ("https://github.com/a/b", None)
    assert parse_remote("https://github.com/a/b.git") == ("https://github.com/a/b.git", None)
    assert parse_remote("github.com/a/b") == ("https://github.com/a/b", None)
    assert parse_remote("git@github.com:a/b.git") == ("git@github.com:a/b.git", None)
    assert parse_remote("https://github.com/a/b/tree/main/sub") == ("https://github.com/a/b", "main")


def test_local_paths_are_not_remote(tmp_path):
    assert parse_remote(str(tmp_path)) is None
    assert parse_remote(".") is None
    assert parse_remote("tests/fixtures") is None  # exists locally


def test_fetch_remote_clones_into_cache(tmp_path):
    src = tmp_path / "origin"
    src.mkdir()
    (src / "a.py").write_text("def f():\n    return 1\n")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=src, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], cwd=src, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"], cwd=src, check=True)
    cache = tmp_path / "cache"
    dest = fetch_remote(f"file://{src}", None, cache=cache)
    assert (dest / "a.py").is_file()
    assert dest.parent == cache / "repos"
    # second call reuses the clone
    assert fetch_remote(f"file://{src}", None, cache=cache) == dest


def test_compile_accepts_git_url(tmp_path):
    src = tmp_path / "origin"
    subprocess.run(["cp", "-r", str(FIX), str(src)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=src, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], cwd=src, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"], cwd=src, check=True)
    out = tmp_path / "out"
    res = runner.invoke(app, ["compile", f"file://{src}", "-o", str(out), "--emit", "json", "--cache", str(tmp_path / "cache")])
    assert res.exit_code == 0, res.output
    assert (out / "ir.json").is_file()
    assert "cloned" in res.output


def test_python_clones_where_told_and_refreshes_like_the_cli(tmp_path):
    from vibepiler.compiler import Compiler

    src = tmp_path / "origin"
    src.mkdir()
    (src / "a.py").write_text("def f():\n    return 1\n")
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=src, check=True)
    subprocess.run([*git, "add", "-A"], cwd=src, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "init"], cwd=src, check=True)
    clones = tmp_path / "clones"
    c = Compiler(f"file://{src}", clone_dir=clones)  # --cache DIR
    assert c.root.parent == clones / "repos" and (c.root / "a.py").is_file()
    (c.root / "stray.py").write_text("x = 1\n")  # left in the clone
    assert (Compiler(f"file://{src}", clone_dir=clones).root / "stray.py").exists()  # the clone is reused (and pulled)
    assert not (Compiler(f"file://{src}", clone_dir=clones, refresh=True).root / "stray.py").exists()  # --refresh clones afresh
