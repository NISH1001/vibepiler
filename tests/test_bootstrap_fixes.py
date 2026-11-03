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


def test_a_function_made_of_named_steps_is_not_indirection(tmp_path):
    steps = "".join(f"def _step{i}(x):\n    return x + {i}\n\n\n" for i in range(3))
    (tmp_path / "main.py").write_text(f"{steps}def _lonely(x):\n    return x * 2\n\n\ndef build(x):\n    x = _step0(x)\n    x = _step1(x)\n    x = _step2(x)\n    return x\n\n\ndef other(x):\n    return _lonely(x)\n\n\nif __name__ == '__main__':\n    build(1)\n    other(1)\n")
    prog = build_program(tmp_path)
    run_passes(prog, opt_level=2)
    helpers = {d.data["qualname"] for d in prog.diagnostics if d.code == "single-use-helper"}
    assert helpers == {"_lonely"}  # the three steps of build() are decomposition, not indirection


def test_a_method_called_through_its_base_keeps_its_overrides_alive(tmp_path):
    (tmp_path / "shapes.py").write_text("class Shape:\n    def area(self):\n        return 0\n\n\nclass Square(Shape):\n    def area(self):\n        return 4\n\n    def perimeter(self):\n        return 8\n")
    (tmp_path / "main.py").write_text("from shapes import Shape, Square\n\n\ndef total(shapes):\n    return sum(Shape.area(s) for s in shapes)\n\n\nif __name__ == '__main__':\n    total([Square()])\n")
    prog = build_program(tmp_path)
    run_passes(prog, opt_level=2)
    dead = {d.data["qualname"] for d in prog.diagnostics if d.code in ("dead-symbol", "unreachable-symbol")}
    assert "Square.area" not in dead and "Square.perimeter" in dead  # virtual dispatch, not blanket exemption


def test_accepted_findings_are_counted_not_reported(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.vibepiler]\naccept = ["single-use-helper:_tiny*", "dead-symbol"]\n')
    (tmp_path / "main.py").write_text("def _tiny(x):\n    return x + 1\n\n\ndef dead():\n    return 0\n\n\ndef run(x):\n    return _tiny(x)\n\n\nif __name__ == '__main__':\n    run(1)\n")
    from vibepiler.config import Config

    prog = build_program(tmp_path, config=Config.load(tmp_path))
    run_passes(prog, opt_level=2)
    assert not [d for d in prog.diagnostics if d.code in ("single-use-helper", "dead-symbol")]
    assert {a["code"] for a in prog.metrics["accepted"]} == {"single-use-helper", "dead-symbol"}
