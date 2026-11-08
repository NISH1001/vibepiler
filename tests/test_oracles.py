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


# ----- cognitive complexity ----------------------------------------------------------------------------------------
COGNITIVE = {  # each number is what complexipy says and what SonarSource's paper says
    "if elif else": ("def f(a):\n    if a:\n        pass\n    elif b:\n        pass\n    else:\n        pass\n", 3),
    "nesting costs": ("def f(a):\n    for x in a:\n        if x:\n            pass\n", 3),
    "an elif body nests": ("def f(a):\n    if a:\n        pass\n    elif b:\n        if c:\n            pass\n", 4),
    "a loop else is free": ("def f(a):\n    for x in a:\n        pass\n    else:\n        if a: pass\n", 2),
    "a try nests nothing, except does": ("def f(a):\n    try:\n        if a: pass\n    except E:\n        if a: pass\n", 4),
    "one run of one operator": ("def f(a):\n    return a and b and c\n", 1),
    "a change of operator": ("def f(a):\n    return a and b or c\n", 2),
    "parentheses do not split a run": ("def f(a):\n    return a and (b and c)\n", 1),
    "a ternary in an if": ("def f(a):\n    if a:\n        x = 1 if a else 2\n", 3),
    "a comprehension in an if": ("def f(a):\n    if a:\n        return [x for x in a if x]\n", 4),
    "a comprehension element nests": ("def f(a):\n    return [1 if c else 2 for c in a]\n", 3),
    "a lambda nests": ("def f(a):\n    return lambda: 1 if a else 2\n", 2),
    "recursion": ("def f(a):\n    return f(a - 1)\n", 1),
    "match nests its cases": ("def f(a):\n    match a:\n        case 1:\n            if a: pass\n", 3),
    "with is free": ("def f(a):\n    with a:\n        if a: pass\n", 1),
}


@pytest.mark.parametrize("src,expected", COGNITIVE.values(), ids=COGNITIVE.keys())
def test_cognitive_rules_match_complexipy(tmp_path, src, expected):
    complexipy = pytest.importorskip("complexipy")
    (tmp_path / "m.py").write_text(src)
    (n,) = compiled(tmp_path).values()
    assert n.attrs["cognitive"] == expected == complexipy.code_complexity(src).functions[0].complexity


def test_cognitive_never_falls_below_complexipy():
    """complexipy skips some expressions (a ternary inside `+`, a starred argument); it never counts what Sonar does
    not. So on every function it can see whole (no nested function or class), ours is at least its number."""
    complexipy = pytest.importorskip("complexipy")
    root = ROOTS[0]
    prog = build_program(root, config=Config.load(root))
    run_passes(prog, opt_level=1)
    g = prog.graph
    whole = {(n.file, n.start_line): n for n in g.nodes(CALLABLE_KINDS) if (n.file or "").endswith(".py") and not g.children(n.id)}
    checked, lower = 0, []
    for f in sorted({k[0] for k in whole}):
        for fn in complexipy.file_complexity(str(root / f)).functions:
            if (n := whole.get((f, fn.line_start))) is not None and n.name == fn.name.rsplit("::", 1)[-1]:
                checked += 1
                if n.attrs["cognitive"] < fn.complexity:
                    lower.append((f, fn.line_start, n.qualname, n.attrs["cognitive"], fn.complexity))
    assert checked > 300 and lower == []


SONAR_JS = {  # G. Ann Campbell, "Cognitive Complexity", SonarSource 2017, the paper's own examples
    "sumOfPrimes": ("function sumOfPrimes(max) {\n  let total = 0;\n  OUT: for (let i = 1; i <= max; ++i) {\n    for (let j = 2; j < i; ++j) {\n      if (i % j == 0) {\n        continue OUT;\n      }\n    }\n    total += i;\n  }\n  return total;\n}\n", 7),
    "getWords": ("function getWords(n) {\n  switch (n) {\n    case 1: return 'one';\n    case 2: return 'a couple';\n    default: return 'lots';\n  }\n}\n", 1),
    "else if is flat": ("function f(a, b) {\n  if (a) {\n    return 1;\n  } else if (b) {\n    return 2;\n  } else {\n    return 3;\n  }\n}\n", 3),
    "mixed operators": ("function f(a, b, c, d) {\n  return a && b && c || d;\n}\n", 2),
    "nested ternary": ("function f(a, b) {\n  return a ? 1 : b ? 2 : 3;\n}\n", 3),
    "catch nests": ("function f(a) {\n  try {\n    if (a) { g(); }\n  } catch (e) {\n    if (a) { g(); }\n  }\n}\n", 4),
}


@pytest.mark.parametrize("ext", ["js", "ts"])
@pytest.mark.parametrize("src,expected", SONAR_JS.values(), ids=SONAR_JS.keys())
def test_cognitive_follows_the_sonar_paper_in_javascript(tmp_path, src, expected, ext):
    (tmp_path / f"m.{ext}").write_text(src)
    prog = build_program(tmp_path, config=Config.load(tmp_path))
    run_passes(prog, opt_level=1)
    (n,) = [n for n in prog.graph.nodes(CALLABLE_KINDS)]
    assert n.attrs["cognitive"] == expected
