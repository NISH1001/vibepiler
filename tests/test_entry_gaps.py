"""Gaps O–V from the 0.4.8 evaluation: code a library user, a framework or a runner reaches that no project call does."""

from vibepiler.ir.builder import build_program
from vibepiler.passes.registry import run_passes


def dead(tmp_path, files: dict[str, str]) -> set[str]:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    prog = build_program(tmp_path)
    run_passes(prog, opt_level=2)
    return {d.node_id.split("::")[1] for d in prog.diagnostics if d.node_id and d.code in ("dead-symbol", "unreachable-symbol")}


def test_o_public_methods_of_a_public_api_class_are_library_api(tmp_path):
    out = dead(tmp_path, {
        "pkg/__init__.py": "from .sessions import Session\n",
        "pkg/sessions.py": (
            "class Base:\n    def close(self):\n        return 0\n\n"
            "class Session(Base):\n    def post(self):\n        return self._send()\n\n"
            "    def _send(self):\n        return 1\n\n    def _unused(self):\n        return 2\n"
        ),
    })
    assert {"Session.post", "Session._send", "Base.close"}.isdisjoint(out)
    assert "Session._unused" in out  # private and uncalled: still dead


def test_p_what_a_framework_hook_calls_is_live(tmp_path):
    out = dead(tmp_path, {"app.py": (
        "from textual.app import App\n\n"
        "def widget():\n    return 1\n\n"
        "class Viewer(App):\n    def on_mount(self):\n        self._show()\n\n    def _show(self):\n        return widget()\n\n"
        "if __name__ == '__main__':\n    Viewer().run()\n"
    )})
    assert "widget" not in out


def test_q_a_testcase_subclass_runs_whatever_its_file_is_called(tmp_path):
    out = dead(tmp_path, {"scripts/release_checks.py": (
        "import unittest\n\n"
        "class FakeClock:\n    pass\n\n"
        "class ReleaseChecks(unittest.TestCase):\n    def test_clock(self):\n        FakeClock()\n"
    )})
    assert {"FakeClock", "ReleaseChecks", "ReleaseChecks.test_clock"}.isdisjoint(out)


def test_r_django_loads_a_backends_base_module(tmp_path):
    out = dead(tmp_path, {
        **{f"db/{d}__init__.py": "" for d in ("", "backends/", "backends/base/", "backends/mysql/")},
        "db/backends/base/operations.py": "class BaseOperations:\n    from_text = False\n\n    def sql(self):\n        return self.from_text\n",
        "db/backends/mysql/operations.py": (
            "from functools import cached_property\nfrom ..base.operations import BaseOperations\n\n"
            "class MySQLOperations(BaseOperations):\n    @cached_property\n    def from_text(self):\n        return 'x'\n"
        ),
        "db/backends/mysql/base.py": "from .operations import MySQLOperations\n\nclass DatabaseWrapper:\n    ops_class = MySQLOperations\n\n    def ops(self):\n        return self.ops_class().sql()\n",
    })
    assert {"MySQLOperations", "MySQLOperations.from_text"}.isdisjoint(out)


def test_s_a_dotted_string_naming_a_function_uses_it(tmp_path):
    out = dead(tmp_path, {
        "proj/__init__.py": "",
        "proj/processors.py": "def debug(request):\n    return {}\n\ndef unused(request):\n    return {}\n",
        "run.py": "import proj.processors\n\nSETTINGS = {'processors': ['proj.processors.debug']}\nprint(SETTINGS, proj.processors)\n",
    })
    assert "debug" not in out
    assert "unused" in out


def test_t_asv_runs_benchmark_methods_and_their_setup(tmp_path):
    out = dead(tmp_path, {"benchmarks/bench.py": (
        "def _table():\n    return 1\n\n"
        "class Suite:\n    def setup(self):\n        self.n = 1\n\n    def time_table(self):\n        _table()\n\n"
        "def mem_list():\n    return [0]\n"
    )})
    assert {"_table", "Suite", "Suite.setup", "Suite.time_table", "mem_list"}.isdisjoint(out)


def test_u_a_registering_decorator_keeps_the_function(tmp_path):
    out = dead(tmp_path, {
        "util.py": "def timed(f):\n    return f\n",
        "app.py": (
            "import util\nfrom flask import Flask\n\napp = Flask(__name__)\nhandlers = []\n\n"
            "@app.template_filter('x')\ndef top_filter(s):\n    return s\n\n"
            "@util.timed\ndef wrapped():\n    return 0\n\n"
            "def build():\n    @handlers.append\n    def inner():\n        return 1\n    return app\n\n"
            "if __name__ == '__main__':\n    build()\n"
        ),
    })
    assert {"top_filter", "build.inner"}.isdisjoint(out)
    assert "wrapped" in out  # a module's decorator wraps: the function is still unused


def test_v_a_script_inside_try_else_is_a_script(tmp_path):
    out = dead(tmp_path, {"examples/listdir.py": (
        "def make_text(name):\n    return name\n\n"
        "try:\n    import sys\nexcept ImportError:\n    raise SystemExit(1)\nelse:\n    print(make_text(sys.argv[0]))\n"
    )})
    assert "make_text" not in out
