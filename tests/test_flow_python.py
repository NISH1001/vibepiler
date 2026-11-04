"""The Python adapter classifies each statement into a flow shape for the dataflow engine."""

from flowutil import body

from vibepiler.frontend.flow import Branch, Jump, Loop, Plain, Try, With


def test_assignment_is_plain_with_a_local_def_and_rhs_uses():
    spec, sf, _, stmts = body("def f(a):\n    x = a + 1\n")
    p = spec.classify(stmts[0], sf)
    assert isinstance(p, Plain) and [(d.name, d.kind, d.rhs_has_call) for d in p.defs] == [("x", "local", False)] and p.uses == {"a"}


def test_call_on_the_right_marks_rhs_has_call():
    spec, sf, _, stmts = body("def f():\n    x = g()\n")
    assert spec.classify(stmts[0], sf).defs[0].rhs_has_call


def test_augmented_assignment_is_a_use_and_never_a_store():
    spec, sf, _, stmts = body("def f(x):\n    x += 1\n")
    p = spec.classify(stmts[0], sf)
    assert p.uses == {"x"} and p.defs == []


def test_tuple_target_shares_a_group():
    spec, sf, _, stmts = body("def f():\n    a, b = g()\n")
    defs = spec.classify(stmts[0], sf).defs
    assert [d.name for d in defs] == ["a", "b"] and defs[0].group == defs[1].group != 0 and defs[0].kind == "destructure"


def test_self_attribute_store_is_an_attribute_def():
    spec, sf, _, stmts = body("class C:\n    def m(self):\n        self.x = 1\n", name="m")
    d = spec.classify(stmts[0], sf).defs[0]
    assert (d.name, d.kind, d.attr_of) == ("x", "attribute", "self")


def test_if_elif_else_is_a_branch_with_three_arms():
    spec, sf, _, stmts = body("def f(a):\n    if a:\n        return 1\n    elif a > 1:\n        return 2\n    else:\n        return 3\n")
    b = spec.classify(stmts[0], sf)
    assert isinstance(b, Branch) and len(b.arms) == 3 and b.has_else and b.uses == {"a"} and b.constant is None


def test_if_false_is_a_constant_branch_and_type_checking_is_not():
    spec, sf, _, stmts = body("def f():\n    if False:\n        return 1\n    if TYPE_CHECKING:\n        return 2\n")
    assert spec.classify(stmts[0], sf).constant is False and spec.classify(stmts[1], sf).constant is None


def test_for_is_a_loop_with_target_defs_and_iterable_uses():
    spec, sf, _, stmts = body("def f(xs):\n    for i, x in enumerate(xs):\n        pass\n    else:\n        pass\n")
    lp = spec.classify(stmts[0], sf)
    assert isinstance(lp, Loop) and [d.name for d in lp.defs] == ["i", "x"] and lp.defs[0].kind == "loop" and lp.uses == {"enumerate", "xs"} and lp.else_body


def test_try_has_handlers_with_except_defs_and_a_finally():
    spec, sf, _, stmts = body("def f():\n    try:\n        g()\n    except ValueError as e:\n        pass\n    finally:\n        h()\n")
    t = spec.classify(stmts[0], sf)
    assert isinstance(t, Try) and [d.name for d in t.handlers[0][0]] == ["e"] and t.handlers[0][0][0].kind == "except" and len(t.final) == 1


def test_with_defines_its_target():
    spec, sf, _, stmts = body("def f(p):\n    with open(p) as fh:\n        pass\n")
    w = spec.classify(stmts[0], sf)
    assert isinstance(w, With) and w.defs[0].name == "fh" and w.defs[0].kind == "with" and "p" in w.uses


def test_return_raise_break_continue_are_jumps():
    spec, sf, _, stmts = body("def f(x):\n    while True:\n        if x:\n            break\n        continue\n    raise ValueError(x)\n    return x\n")
    assert [spec.classify(s, sf).kind for s in stmts[1:]] == ["raise", "return"] and spec.classify(stmts[2], sf).uses == {"x"}
    loop = spec.classify(stmts[0], sf)
    assert [spec.classify(s, sf).kind for s in loop.body[1:]] == ["continue"] and isinstance(spec.classify(loop.body[1], sf), Jump)


def test_function_params_mark_self_and_rest():
    spec, sf, node, _ = body("class C:\n    def m(self, a, *args, b=1, **kw):\n        pass\n", name="m")
    assert spec.function_params(node, sf) == [("self", "self"), ("a", "plain"), ("args", "rest"), ("b", "plain"), ("kw", "rest")]


def test_captured_names_are_reads_inside_nested_scopes():
    spec, sf, node, _ = body("def f(a, b):\n    g = lambda: a\n    def inner():\n        return b\n    return g, inner\n")
    assert spec.captured_names(node, sf) == {"a", "b"}


def test_dynamic_scope_is_detected():
    spec, sf, node, _ = body("def f():\n    return locals()\n")
    assert spec.has_dynamic_scope(node, sf)


def test_global_is_a_marker_def():
    spec, sf, _, stmts = body("def f():\n    global x\n    x = 1\n")
    assert [d.name for d in spec.classify(stmts[0], sf).defs] == ["global:x"]
