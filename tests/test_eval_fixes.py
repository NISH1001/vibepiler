"""Bugs the evaluation against vulture, ruff and deptry found (docs/eval/python/RESULTS.md, ids A-N).

Each test is the minimal project from scripts/eval/python/repro/, or the shape the report names, and fails without its fix.
"""

from vibepiler.ir.builder import build_program
from vibepiler.ir.model import EdgeKind
from vibepiler.passes.registry import run_passes

DEAD = ("dead-symbol", "unreachable-symbol", "unused-export")
MAIN = "\n\nif __name__ == '__main__':\n"


def compile_files(tmp_path, files: dict[str, str]):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    prog = build_program(tmp_path)
    run_passes(prog, opt_level=2)
    return prog


def found(prog, *codes: str) -> set[tuple[str, str]]:
    """(file, name) of every diagnostic with one of these codes."""
    return {
        (d.file, d.data.get("local_name") or d.data.get("package") or d.data.get("qualname") or "")
        for d in prog.diagnostics
        if d.code in codes
    }


# A ------------------------------------------------------------------------------------------------------------------
def test_a_name_other_modules_import_from_a_plain_module_is_used(tmp_path):
    prog = compile_files(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/compat.py": "from urllib.parse import quote\nimport json\n",
        "pkg/utils.py": "from .compat import quote\nfrom .compat import json as cj\n\n\ndef run(x):\n    return quote(x) + cj.dumps(x)" + MAIN + "    run('a')\n",
    })
    assert not found(prog, "unused-import")


# B ------------------------------------------------------------------------------------------------------------------
def test_b_a_fallback_def_for_a_failed_import_is_alive(tmp_path):
    prog = compile_files(tmp_path, {
        "main.py": "try:\n    from fastlib import speedy\nexcept ImportError:\n    def speedy(x):\n        return x\n\n\ndef run(x):\n    return speedy(x)" + MAIN + "    run(1)\n",
    })
    assert not found(prog, *DEAD) and not found(prog, "unused-import")


def test_b_a_platform_redefinition_of_an_imported_name_is_alive(tmp_path):
    prog = compile_files(tmp_path, {
        "compat.py": "from urllib.request import proxy_bypass\n",
        "utils.py": "import sys\n\nfrom compat import proxy_bypass\n\nif sys.platform == 'win32':\n    def proxy_bypass(host):\n        return host\n\n\ndef should_bypass(h):\n    return proxy_bypass(h)" + MAIN + "    should_bypass('x')\n",
    })
    assert not found(prog, *DEAD) and not found(prog, "unused-import")


# C ------------------------------------------------------------------------------------------------------------------
def test_c_an_exception_named_only_in_a_bare_except_is_used(tmp_path):
    prog = compile_files(tmp_path, {
        "errors.py": "class Boom(Exception):\n    pass\n",
        "main.py": "from errors import Boom\n\n\ndef run(f):\n    try:\n        return f()\n    except Boom:\n        return None" + MAIN + "    run(print)\n",
    })
    assert not found(prog, "unused-import")
    assert not found(prog, *DEAD)


# D ------------------------------------------------------------------------------------------------------------------
def test_d_a_subscripted_base_is_linked(tmp_path):
    prog = compile_files(tmp_path, {
        "main.py": "from textual.screen import ModalScreen\n\n\nclass Viewer(ModalScreen[None]):\n    def on_mount(self):\n        self._fit()\n\n    def _fit(self):\n        return 1" + MAIN + "    Viewer()\n",
    })
    assert not found(prog, "unused-import")
    assert prog.graph.has_external_base("sym:main.py::Viewer")
    assert not found(prog, *DEAD)


def test_d_a_subscripted_project_base_gets_an_inherits_edge(tmp_path):
    prog = compile_files(tmp_path, {
        "main.py": "from typing import Generic, TypeVar\n\nT = TypeVar('T')\n\n\nclass Box(Generic[T]):\n    pass\n\n\nclass IntBox(Box[int]):\n    pass" + MAIN + "    IntBox()\n",
    })
    assert prog.graph.has_edge_kind("sym:main.py::IntBox", "sym:main.py::Box", EdgeKind.INHERITS)


# E ------------------------------------------------------------------------------------------------------------------
def test_e_a_stdlib_import_does_not_resolve_to_a_same_named_subpackage(tmp_path):
    prog = compile_files(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/config.py": "import json\n\n\ndef load(x):\n    return json.loads(x)" + MAIN + "    load('1')\n",
        "pkg/json/__init__.py": "from ..config import load\n\n\ndef dumps(x):\n    return load(x)\n",
    })
    g = prog.graph
    assert g.has_edge_kind("mod:pkg/config.py", "pkg:json", EdgeKind.IMPORTS)
    assert not g.has_edge_kind("mod:pkg/config.py", "mod:pkg/json/__init__.py", EdgeKind.IMPORTS)
    assert not found(prog, "import-cycle")


def test_e_a_module_named_like_the_stdlib_does_not_import_itself(tmp_path):
    prog = compile_files(tmp_path, {"html.py": "import html\n\n\ndef page(s):\n    return html.escape(s)" + MAIN + "    page('x')\n"})
    assert not prog.graph.has_edge_kind("mod:html.py", "mod:html.py", EdgeKind.IMPORTS)
    assert prog.graph.has_edge_kind("mod:html.py", "pkg:html", EdgeKind.IMPORTS)


# F ------------------------------------------------------------------------------------------------------------------
def test_f_an_inherited_classmethod_called_on_a_subclass_keeps_the_subclass(tmp_path):
    prog = compile_files(tmp_path, {
        "lib/__init__.py": "",
        "lib/views.py": "class View:\n    @classmethod\n    def as_view(cls, name):\n        return cls\n",
        "main.py": "from lib.views import View\n\n\nclass Mine(View):\n    pass" + MAIN + "    Mine.as_view('z')\n",
        "tests/test_m.py": "import lib.views\n\n\ndef test_direct():\n    class Local(lib.views.View):\n        pass\n\n    return Local.as_view('y')\n",
    })
    assert not found(prog, *DEAD)


def test_f_self_calls_to_inherited_methods_add_no_reference_to_the_own_class(tmp_path):
    prog = compile_files(tmp_path, {
        "main.py": "class Base:\n    def ping(self):\n        return 1\n\n\nclass Child(Base):\n    def run(self):\n        return self.ping()\n",
    })
    assert ("main.py", "Child") in found(prog, "dead-symbol")  # nothing outside uses Child: dead, not "only used by dead code"


# G ------------------------------------------------------------------------------------------------------------------
def test_g_public_api_re_exported_through_chained_star_imports(tmp_path):
    prog = compile_files(tmp_path, {
        "pkg/__init__.py": "from ._transports import *\nfrom ._exceptions import *\n",
        "pkg/_transports/__init__.py": "from .mock import *\n\n__all__ = ['MockTransport']\n",
        "pkg/_transports/mock.py": "__all__ = ['MockTransport']\n\n\nclass MockTransport:\n    pass\n\n\nclass _Hidden:\n    pass\n",
        "pkg/_exceptions.py": "__all__ = ['CloseError']\n\n\nclass CloseError(Exception):\n    pass\n",
    })
    dead = {name for _, name in found(prog, *DEAD)}
    assert "MockTransport" not in dead and "CloseError" not in dead
    assert "_Hidden" in dead


def test_g_a_name_reached_through_chained_star_imports_is_linked(tmp_path):
    prog = compile_files(tmp_path, {
        "pkg/__init__.py": "from ._transports import *\n",
        "pkg/_transports/__init__.py": "from .mock import *\n",
        "pkg/_transports/mock.py": "class MockTransport:\n    pass\n",
        "tests/test_x.py": "import pkg\n\n\ndef test_mock():\n    pkg.MockTransport()\n",
    })
    assert prog.graph.has_edge_kind("sym:tests/test_x.py::test_mock", "sym:pkg/_transports/mock.py::MockTransport", EdgeKind.CALLS)


# I ------------------------------------------------------------------------------------------------------------------
def test_i_virtual_dispatch_reaches_overrides_in_grandchildren(tmp_path):
    prog = compile_files(tmp_path, {
        "main.py": (
            "def helper(x):\n    return x + 1\n\n\n"
            "class Base:\n    def do_transform(self, x):\n        raise NotImplementedError\n\n    def __call__(self, x):\n        return self.do_transform(x)\n\n\n"
            "class Mid(Base):\n    pass\n\n\n"
            "class Leaf(Mid):\n    def do_transform(self, x):\n        return helper(x)"
            + MAIN + "    Leaf()(1)\n"
        ),
    })
    dead = {name for _, name in found(prog, *DEAD)}
    assert "Leaf.do_transform" not in dead and "helper" not in dead


# J ------------------------------------------------------------------------------------------------------------------
PIP_COMPILE = "#\n# This file is autogenerated by pip-compile with Python 3.12\n#\n#    pip-compile docs.in\n#\nsphinx==7.2.6\n    # via -r docs.in\nsphinxcontrib-applehelp==1.0.4\n    # via sphinx\n"


def test_j_a_pip_compile_lock_file_is_not_a_manifest(tmp_path):
    prog = compile_files(tmp_path, {
        "pyproject.toml": "[project]\nname = 'app'\nversion = '0'\ndependencies = ['click']\n",
        "requirements/docs.txt": PIP_COMPILE,
        "app/main.py": "import click" + MAIN + "    click.echo('x')\n",
    })
    assert not {p for f, p in found(prog, "unused-dependency", "undeclared-dependency") if f.startswith("requirements")}


# K ------------------------------------------------------------------------------------------------------------------
def test_k_markdown_it_py_is_imported_as_markdown_it(tmp_path):
    prog = compile_files(tmp_path, {
        "pyproject.toml": "[project]\nname = 'app'\nversion = '0'\ndependencies = ['markdown-it-py>=2']\n",
        "app/main.py": "import markdown_it" + MAIN + "    print(markdown_it)\n",
    })
    assert not found(prog, "unused-dependency", "undeclared-dependency")


# L ------------------------------------------------------------------------------------------------------------------
def test_l_a_wildcard_version_specifier_declares_the_dependency(tmp_path):
    prog = compile_files(tmp_path, {
        "pyproject.toml": "[project]\nname = 'app'\nversion = '0'\ndependencies = ['httpcore==1.*', 'idna']\n",
        "app/main.py": "import httpcore\nimport idna" + MAIN + "    print(httpcore, idna)\n",
    })
    assert not found(prog, "unused-dependency", "undeclared-dependency")


# M ------------------------------------------------------------------------------------------------------------------
def test_m_a_mixed_case_stdlib_module_is_not_a_dependency(tmp_path):
    prog = compile_files(tmp_path, {
        "pyproject.toml": "[project]\nname = 'app'\nversion = '0'\ndependencies = ['click']\n",
        "scripts/bench.py": "import cProfile\nimport click" + MAIN + "    print(cProfile, click)\n",
    })
    assert not found(prog, "undeclared-dependency")


# N ------------------------------------------------------------------------------------------------------------------
def test_n_test_requirements_txt_is_a_manifest(tmp_path):
    prog = compile_files(tmp_path, {
        "pyproject.toml": "[project]\nname = 'app'\nversion = '0'\ndependencies = ['click']\n",
        "test_requirements.txt": "pytest\n",
        "app/main.py": "import click" + MAIN + "    click.echo('x')\n",
        "tests/test_a.py": "import pytest\n\n\ndef test_a():\n    assert pytest\n",
    })
    assert not found(prog, "undeclared-dependency")
