"""The typed linker: a receiver whose class the source states links its method calls to that class."""

from vibepiler.ir.builder import build_program
from vibepiler.ir.model import EdgeKind
from vibepiler.passes.registry import run_passes

DB = """\
class Database:
    def query(self):
        return 1

    def close(self):
        return 2


class Cache:
    def query(self):
        return 3
"""


def build(tmp_path, main: str, extra: dict[str, str] | None = None, passes: bool = False):
    for rel, text in {"db.py": DB, "main.py": main, **(extra or {})}.items():
        (tmp_path / rel).write_text(text)
    prog = build_program(tmp_path)
    if passes:
        run_passes(prog, opt_level=2)
    return prog


def calls(prog, src: str) -> set[str]:
    return {prog.graph.node(e.dst).qualname for e in prog.graph.out_edges(src, EdgeKind.CALLS)}


def test_an_annotated_parameter_types_its_calls(tmp_path):
    prog = build(tmp_path, "from db import Database\n\ndef run(db: Database):\n    return db.query()\n")
    assert calls(prog, "sym:main.py::run") == {"Database.query"}
    assert prog.graph.meta["resolution"]["calls_typed"] == 1


def test_optional_union_and_string_annotations_name_one_class(tmp_path):
    prog = build(tmp_path, (
        "from typing import Optional\nimport db\n\n"
        "def a(x: Optional[db.Database]):\n    x.query()\n\n"
        "def b(x: 'db.Database | None'):\n    x.close()\n\n"
        "def c(x: db.Database | None = None):\n    x.query()\n"
    ))
    assert calls(prog, "sym:main.py::a") == {"Database.query"}
    assert calls(prog, "sym:main.py::b") == {"Database.close"}
    assert calls(prog, "sym:main.py::c") == {"Database.query"}


def test_a_container_or_a_choice_of_classes_is_not_typed(tmp_path):
    prog = build(tmp_path, "from db import Database, Cache\n\ndef a(x: list[Database], y: Database | Cache):\n    x.query()\n    y.query()\n")
    assert calls(prog, "sym:main.py::a") == set()


def test_a_constructor_call_and_a_return_annotation_type_a_local(tmp_path):
    prog = build(tmp_path, (
        "from db import Database\n\n"
        "def connect() -> Database:\n    return Database()\n\n"
        "def a():\n    d = Database()\n    d.close()\n\n"
        "async def b():\n    d = await aconnect()\n    d.query()\n\n"
        "async def aconnect() -> 'Database':\n    return Database()\n\n"
        "def c():\n    d = connect()\n    d.query()\n"
    ))
    assert calls(prog, "sym:main.py::a") == {"Database", "Database.close"}
    assert calls(prog, "sym:main.py::b") == {"aconnect", "Database.query"}
    assert calls(prog, "sym:main.py::c") == {"connect", "Database.query"}


def test_fields_set_on_self_type_calls_through_them(tmp_path):
    prog = build(tmp_path, (
        "from db import Database\n\n"
        "class Service:\n"
        "    cache: 'Database'\n\n"
        "    def __init__(self, db: Database):\n        self.db = db\n        self.other = None\n        self.other = Database()\n\n"
        "    def run(self):\n        self.db.query()\n        self.other.close()\n        self.cache.query()\n"
    ))
    assert calls(prog, "sym:main.py::Service.run") == {"Database.query", "Database.close"}


def test_a_property_types_by_its_return_annotation(tmp_path):
    prog = build(tmp_path, (
        "from db import Database\n\n"
        "class Service:\n"
        "    @property\n    def db(self) -> Database:\n        return Database()\n\n"
        "    def run(self):\n        self.db.close()\n"
    ))
    assert "Database.close" in calls(prog, "sym:main.py::Service.run")


def test_self_returning_methods_chain(tmp_path):
    prog = build(tmp_path, (
        "from typing import Self\n\n"
        "class Q:\n    def where(self) -> Self:\n        return self\n\n    def all(self):\n        return 1\n\n"
        "def run():\n    q = Q()\n    r = q.where()\n    r.all()\n"
    ))
    assert calls(prog, "sym:main.py::run") == {"Q", "Q.where", "Q.all"}


def test_two_different_classes_for_one_name_type_nothing(tmp_path):
    prog = build(tmp_path, "from db import Database, Cache\n\ndef a(flag):\n    x = Database()\n    if flag:\n        x = Cache()\n    x.query()\n")
    assert calls(prog, "sym:main.py::a") == {"Database", "Cache"}


def test_a_name_rebound_by_a_loop_or_with_types_nothing(tmp_path):
    prog = build(tmp_path, "from db import Database\n\ndef a(items):\n    x = Database()\n    for x in items:\n        x.query()\n\ndef b(f):\n    x = Database()\n    with f() as x:\n        x.close()\n")
    assert "Database.query" not in calls(prog, "sym:main.py::a")
    assert "Database.close" not in calls(prog, "sym:main.py::b")


def test_a_closure_sees_its_functions_typed_names(tmp_path):
    prog = build(tmp_path, "from db import Database\n\ndef a(d: Database):\n    def inner():\n        return d.close()\n    return inner\n")
    assert calls(prog, "sym:main.py::a.inner") == {"Database.close"}


def test_a_module_level_instance_types_calls_and_the_variable_stays_used(tmp_path):
    prog = build(tmp_path, "from db import Database\n\nclient = Database()\n\ndef run():\n    return client.query()\n\nif __name__ == '__main__':\n    run()\n", passes=True)
    assert calls(prog, "sym:main.py::run") == {"Database.query"}
    assert not any(d.node_id == "sym:main.py::client" for d in prog.diagnostics if d.code == "dead-symbol")


def test_typing_lets_an_uncalled_same_named_method_be_dead(tmp_path):
    prog = build(tmp_path, "from db import Cache, Database\n\ndef run(d: Database):\n    return d.query()\n\nif __name__ == '__main__':\n    run(Database())\n    Cache()\n", passes=True)
    dead = {d.node_id for d in prog.diagnostics if d.code == "dead-symbol"}
    assert "sym:db.py::Cache.query" in dead  # once, `query` used anywhere kept every `query` alive
    assert "sym:db.py::Database.query" not in dead


def test_through_a_protocol_the_name_stays_used(tmp_path):
    proto = "from typing import Protocol\n\nclass Store(Protocol):\n    def query(self): ...\n"
    prog = build(tmp_path, "from proto import Store\nfrom db import Cache\n\ndef run(s: Store):\n    return s.query()\n\nif __name__ == '__main__':\n    run(Cache())\n", {"proto.py": proto}, passes=True)
    assert calls(prog, "sym:main.py::run") == {"Store.query"}
    assert "sym:db.py::Cache.query" not in {d.node_id for d in prog.diagnostics if d.code == "dead-symbol"}
