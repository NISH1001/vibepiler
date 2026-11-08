"""Our numbers against the reference tools: radon for cyclomatic complexity (and later Halstead and MI), complexipy for
cognitive complexity. The tools are dev-only; vibepiler computes everything itself on the tree-sitter tree."""

from pathlib import Path

import pytest

from vibepiler.config import Config
from vibepiler.ir.builder import build_program
from vibepiler.ir.model import CALLABLE_KINDS
from vibepiler.passes.registry import run_passes

radon_cc = pytest.importorskip("radon.complexity")

ROOTS = [Path(__file__).parents[1] / "src", Path(__file__).parent / "fixtures"]


def compiled(root: Path):
    prog = build_program(root, config=Config.load(root))
    run_passes(prog, opt_level=1)
    return {(n.file, n.start_line): n for n in prog.graph.nodes(CALLABLE_KINDS) if (n.file or "").endswith(".py")}


def radon_blocks(src: str):
    def walk(bs):
        for b in bs:
            if type(b).__name__ != "Class":
                yield b
            yield from walk(getattr(b, "methods", None) or [])
            yield from walk(getattr(b, "closures", None) or [])

    return walk(radon_cc.cc_visit(src))


@pytest.mark.parametrize("root", ROOTS, ids=lambda p: p.name)
def test_cyclomatic_agrees_with_radon_on_every_function(root):
    ours = compiled(root)
    checked, wrong = 0, []
    for f in sorted({k[0] for k in ours}):
        for b in radon_blocks((root / f).read_text()):
            if (n := ours.get((f, b.lineno))) is not None:
                checked += 1
                if n.attrs["complexity"] != b.complexity:
                    wrong.append((f, b.lineno, n.qualname, n.attrs["complexity"], b.complexity))
    assert checked > 30 and wrong == []


CASES = {
    "with adds nothing": ("def f(s):\n    with x:\n        pass\n", 1),
    "assert is one check": ("def f(s):\n    assert a and b\n", 2),
    "comprehension filters count": ("def f(s):\n    return [c for c in s if c]\n", 3),
    "loop else counts": ("def f(s):\n    while a:\n        pass\n    else:\n        pass\n", 3),
    "wildcard case and guards add nothing": ("def f(s):\n    match s:\n        case 1 if y: pass\n        case _: pass\n", 2),
}


@pytest.mark.parametrize("src,expected", CASES.values(), ids=CASES.keys())
def test_cyclomatic_rules_match_radon(tmp_path, src, expected):
    (tmp_path / "m.py").write_text(src)
    (n,) = compiled(tmp_path).values()
    assert n.attrs["complexity"] == expected == next(radon_blocks(src)).complexity
