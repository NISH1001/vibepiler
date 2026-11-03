"""Fixes found by running vibepiler on itself: every self-finding the owner rejects is a bug to fix."""

from vibepiler.ir.builder import build_program
from vibepiler.passes.bloat_score import Bloat
from vibepiler.passes.registry import run_passes


def test_a_constant_used_only_as_a_default_argument_is_alive(tmp_path):
    (tmp_path / "main.py").write_text("LIMIT = 3\n\n\ndef take(xs, k=LIMIT):\n    return xs[:k]\n\n\nif __name__ == '__main__':\n    take([1, 2])\n")
    prog = build_program(tmp_path)
    run_passes(prog, opt_level=2)
    assert not [d for d in prog.diagnostics if d.code == "dead-symbol" and d.data.get("qualname") == "LIMIT"]


def test_a_decorated_stub_is_a_registered_callback_not_a_placeholder(tmp_path):
    (tmp_path / "main.py").write_text("import click\n\n\n@click.group()\ndef cli():\n    pass\n\n\ndef forgotten():\n    pass\n\n\nif __name__ == '__main__':\n    cli()\n    forgotten()\n")
    prog = build_program(tmp_path)
    run_passes(prog, opt_level=2)
    stubs = {d.data["qualname"] for d in prog.diagnostics if d.code == "stub-function"}
    assert stubs == {"forgotten"}


def test_external_base_is_followed_through_the_whole_chain(tmp_path):
    (tmp_path / "main.py").write_text("import django\n\n\nclass Base(django.Model):\n    pass\n\n\nclass Mid(Base):\n    pass\n\n\nclass Leaf(Mid):\n    def save(self):\n        return 1\n\n\nif __name__ == '__main__':\n    Leaf()\n")
    prog = build_program(tmp_path)
    g = prog.graph
    assert g.has_external_base("sym:main.py::Leaf") and g.has_external_base("sym:main.py::Base")
    run_passes(prog, opt_level=2)
    assert not [d for d in prog.diagnostics if d.code == "dead-symbol" and d.data.get("qualname") == "Leaf.save"]  # the framework may call it


def test_bloat_is_also_a_value(tmp_path):
    (tmp_path / "main.py").write_text("def used():\n    return 1\n\n\ndef dead():\n    return 2\n\n\nif __name__ == '__main__':\n    used()\n")
    prog = build_program(tmp_path)
    assert Bloat.of(prog) is None  # nothing scored yet
    run_passes(prog, opt_level=2)
    repo = Bloat.of(prog)
    assert repo is not None and 0 < repo.waste <= 1 and repo.score == 1 - (1 - repo.waste) * (1 - repo.tangle)
    dead = Bloat.of(prog.graph.node("sym:main.py::dead"))
    assert dead.waste == 1.0 and dead.score == 1.0 and "bloat 100%" in str(dead)
