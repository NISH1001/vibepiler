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


# ----- the metrics ---------------------------------------------------------------------------------------------------
from vibepiler.passes.measures import Cognitive, Documented, Maintainability, Typed  # noqa: E402
from vibepiler.passes.registry import parse_gate  # noqa: E402


def prog(code, language="python"):
    return Compiler(code, language=language).run(opt_level=1, emit=()).program


def test_over_limit_is_zero_under_the_limit_and_half_at_twice_it():
    assert Cognitive.rescale(15, 15) == 0.0 and Cognitive.rescale(30, 15) == 0.5
    assert Maintainability.rescale(25, 20) == 0.0 and Maintainability.rescale(10, 20) == 0.5


def test_over_limit_rolls_up_line_weighted_and_keeps_the_worst():
    a, b = Cognitive.measured(30, 15, 10), Cognitive.measured(5, 15, 30)
    total = a + b
    assert total.value == 0.5 * 10 / 40 and (total.functions, total.over, total.worst) == (2, 1, 30)
    assert str(total) == "cognitive 12.5% (1 of 2 functions over 15; worst 30)"
    worst_mi = (Maintainability.measured(12, 20, 5) + Maintainability.measured(40, 20, 5)).worst
    assert worst_mi == 12  # for a score where high is good, the worst is the lowest


def test_every_function_and_the_repo_carry_each_metric():
    p = prog("def f(a: int) -> int:\n    '''Doc.'''\n    return a\n\n\ndef g(b):\n    return b\n")
    f, g = sorted((n for n in p.graph.nodes() if n.kind.value == "function"), key=lambda n: n.name)
    assert f.attrs["typed"] == {"value": 1.0, "have": 2, "total": 2} and g.attrs["typed"]["have"] == 0
    assert f.attrs["documented"]["have"] == 1 and g.attrs["documented"]["have"] == 0
    assert f.attrs["cognitive"]["worst"] == 0 and Typed.of(p).value == 0.5
    assert {"value", "folders", "tests", "formula"} <= set(p.metrics["cognitive_score"])


def test_typed_skips_self_and_init_returns_and_plain_javascript():
    p = prog("class C:\n    def __init__(self, a: int):\n        self.a = a\n")
    assert Typed.of(p).as_dict() == {"value": 1.0, "have": 1, "total": 1}
    assert Typed.of(prog("function f(a) { return a; }\n", "javascript")).total == 0


def test_typed_reads_typescript_annotations():
    p = prog("export function f(a: number, b): number { return a; }\n", "typescript")
    assert (Typed.of(p).have, Typed.of(p).total) == (2, 3)


def test_documented_counts_public_units_only():
    p = prog('"""Module."""\n\n\ndef f():\n    """Doc."""\n\n\ndef _g():\n    pass\n\n\nclass C:\n    def m(self):\n        pass\n')
    assert (Documented.of(p).have, Documented.of(p).total) == (2, 4)  # module, f documented; C, C.m not; _g private


def test_documented_reads_jsdoc_above_an_export():
    p = prog("/** Adds. */\nexport function add(a, b) { return a + b; }\n\n// not a doc\nexport function sub(a, b) { return a - b; }\n", "javascript")
    assert (Documented.of(p).have, Documented.of(p).total) == (1, 2)


@pytest.mark.parametrize("gate,label", [("cognitive>0.1", "cognitive"), ("mi>0.05", "mi"), ("typed<0.8", "typed"), ("documented<0.5", "documented")])
def test_every_metric_is_a_gate(gate, label):
    cls, _, _ = parse_gate(gate)
    assert cls.label == label
