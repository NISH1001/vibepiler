"""The metrics round: Halstead, the maintainability index, and the Metric roll-ups built on them."""

import math

import pytest

from vibepiler.compiler import Compiler
from vibepiler.passes.halstead import maintainability


def fn(code, language="python"):
    prog = Compiler(code, language=language).run(opt_level=1, emit=()).program
    (n,) = [n for n in prog.graph.nodes() if n.kind.value in ("function", "method")]
    return n


def test_halstead_counts_tokens_operands_are_names_and_literals():
    h = fn("def f(a):\n    return a + 1\n").attrs["halstead"]
    # operators: def ( ) : return +   operands: f a a 1
    assert (h["n1"], h["n2"], h["N1"], h["N2"]) == (6, 3, 6, 4)
    assert h["volume"] == round(10 * math.log2(9), 1)
    assert h["difficulty"] == 4.0 and h["effort"] == round(4.0 * 10 * math.log2(9), 1)


def test_a_string_is_one_operand_and_comments_are_not_tokens():
    a = fn("def f():\n    return 'a b c'  # note\n").attrs["halstead"]
    b = fn("def f():\n    return 'x'\n").attrs["halstead"]
    assert (a["N1"], a["N2"]) == (b["N1"], b["N2"])


def test_halstead_works_the_same_in_javascript():
    h = fn("function f(a) { return a + 1; }\n", "javascript").attrs["halstead"]
    assert h["n2"] == 3 and h["N2"] == 4


def test_maintainability_is_visual_studios_index():
    assert maintainability(31.7, 1, 2) == round((171 - 5.2 * math.log(31.7) - 0.23 - 16.2 * math.log(2)) * 100 / 171, 1)
    assert maintainability(0, 1, 0) == round((171 - 0.23) * 100 / 171, 1)  # empty: logs of 1, never a domain error
    assert maintainability(1e9, 200, 5000) == 0.0
    assert fn("def f(a):\n    return a + 1\n").attrs["mi"] > 70
