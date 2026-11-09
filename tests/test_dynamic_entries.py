"""Code the program runs without a direct call: the four dynamic patterns left in the evaluation's false positives
(docs/eval/python/RESULTS.md). Each rule also has the case it must not swallow."""

from vibepiler.ir.model import EdgeKind
from vibepiler.passes.registry import run_passes
from vibepiler.ir.builder import build_program

DEAD = ("dead-symbol", "unreachable-symbol", "unused-export", "unreachable-module")


def compile_files(tmp_path, files: dict[str, str]):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    prog = build_program(tmp_path)
    run_passes(prog, opt_level=2)
    return prog


def dead(prog) -> set[str]:
    """Dead symbols by qualname, unreachable modules by file."""
    return {d.file if d.code == "unreachable-module" else d.data.get("qualname") for d in prog.diagnostics if d.code in DEAD}


# 1. scripts ------------------------------------------------------------------------------------------------------
def test_a_script_outside_any_package_that_runs_code_is_an_entry_point(tmp_path):
    prog = compile_files(tmp_path, {
        "examples/exception.py": "def divide_all(xs):\n    return [1 / x for x in xs]\n\n\nprint(divide_all([1, 2]))\n",
        "examples/celery/make_celery.py": "from task_app import create_app\n\nflask_app = create_app()\ncelery_app = flask_app.extensions['celery']\n",
        "examples/celery/task_app/__init__.py": "class FlaskTask:\n    pass\n\n\ndef create_app():\n    return FlaskTask\n",
    })
    assert not dead(prog)


def test_a_package_module_nothing_imports_is_still_unreachable(tmp_path):
    prog = compile_files(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/main.py": "def run():\n    return 1\n\n\nif __name__ == '__main__':\n    run()\n",
        "pkg/old.py": "import logging\n\nlog = logging.getLogger(__name__)\nprint('loaded')\n",
    })
    assert "pkg/old.py" in dead(prog)


def test_a_script_of_only_definitions_is_not_an_entry_point(tmp_path):
    prog = compile_files(tmp_path, {"main.py": "def run():\n    return 1\n\n\nrun()\n", "leftover.py": "X = 1\n\n\ndef unused():\n    return X\n"})
    assert "leftover.py" in dead(prog)


# 2. getattr dispatch -------------------------------------------------------------------------------------------------
VISITOR = (
    "class Visitor:\n"
    "    def visit(self, node):\n"
    "        f = getattr(self, f'visit_{node.type}', None)\n"
    "        return f(node) if f else self.visit_default(node)\n\n"
    "    def visit_default(self, node):\n        return None\n\n\n"
    "class Lines(Visitor):\n"
    "    def visit_power(self, node):\n        return helper(node)\n\n"
    "    def other(self):\n        return 1\n\n\n"
    "def helper(n):\n    return n\n\n\n"
    "if __name__ == '__main__':\n    Lines().visit(None)\n"
)


def test_getattr_with_a_name_prefix_reaches_the_prefixed_methods_of_subclasses(tmp_path):
    prog = compile_files(tmp_path, {"main.py": VISITOR})
    gone = dead(prog)
    assert "Lines.visit_power" not in gone and "helper" not in gone
    assert "Lines.other" in gone  # only the prefix is dispatched
    assert prog.graph.has_edge_kind("sym:main.py::Visitor.visit", "sym:main.py::Lines.visit_power", EdgeKind.CALLS)


def test_getattr_with_a_concatenated_prefix_dispatches_too(tmp_path):
    prog = compile_files(tmp_path, {
        "main.py": "class Shell:\n    def onecmd(self, line):\n        return getattr(self, 'do_' + line)()\n\n    def do_quit(self):\n        return 0" + "\n\n\nif __name__ == '__main__':\n    Shell().onecmd('quit')\n",
    })
    assert "Shell.do_quit" not in dead(prog)


# 3. modules named in strings -----------------------------------------------------------------------------------------
def test_a_module_named_in_a_string_is_loaded_and_its_public_names_are_alive(tmp_path):
    prog = compile_files(tmp_path, {
        "db/__init__.py": "",
        "db/base.py": "from importlib import import_module\n\n\nclass Ops:\n    compiler_module = 'db.mysql'\n\n    def compiler(self, name):\n        return getattr(import_module(self.compiler_module), name)\n",
        "db/mysql.py": "class SQLUpdateCompiler:\n    pass\n\n\nclass _Private:\n    pass\n",
        "main.py": "from db.base import Ops\n\nif __name__ == '__main__':\n    Ops().compiler('SQLUpdateCompiler')\n",
    })
    gone = dead(prog)
    assert "SQLUpdateCompiler" not in gone and "db/mysql.py" not in gone
    assert "_Private" in gone


def test_a_module_name_in_a_docstring_or_message_text_is_not_a_load(tmp_path):
    prog = compile_files(tmp_path, {
        "db/__init__.py": "",
        "db/mysql.py": "class SQLUpdateCompiler:\n    pass\n",
        "main.py": "def run():\n    \"\"\"db.mysql\"\"\"\n    raise ValueError('see db.mysql for details')\n\n\nif __name__ == '__main__':\n    run()\n",
    })
    assert "db/mysql.py" in dead(prog)


def test_a_dotted_string_naming_no_project_module_is_not_a_dependency(tmp_path):
    prog = compile_files(tmp_path, {
        "pyproject.toml": "[project]\nname = 'app'\nversion = '0'\ndependencies = []\n",
        "main.py": "LOGGER = 'acme.server'\n\nif __name__ == '__main__':\n    print(LOGGER)\n",
    })
    assert not [d for d in prog.diagnostics if d.code == "undeclared-dependency"]
    assert not prog.graph.has_node("pkg:acme")


# 4. framework folders ------------------------------------------------------------------------------------------------
def test_django_templatetags_and_management_commands_are_loaded_by_the_framework(tmp_path):
    prog = compile_files(tmp_path, {
        "flatpages/__init__.py": "",
        "flatpages/templatetags/__init__.py": "",
        "flatpages/templatetags/flatpages.py": "class FlatpageNode:\n    def __init__(self, name):\n        self.name = name\n",
        "flatpages/management/__init__.py": "",
        "flatpages/management/commands/__init__.py": "",
        "flatpages/management/commands/sync.py": "from django.core.management.base import BaseCommand\n\n\nclass Command(BaseCommand):\n    def handle(self):\n        return 1\n",
    })
    gone = dead(prog)
    assert not {x for x in gone if "templatetags" in x or "management" in x or x.startswith(("FlatpageNode", "Command"))}
