"""Backward liveness over the flow shapes, solved to a fixpoint."""

from flowutil import body

from vibepiler.passes.dataflow import analyze


def fl(code, name="f", language="python"):
    spec, sf, node, _ = body(code, name=name, language=language)
    return analyze(node, spec, sf)


def dead(r):
    return sorted((s.d.name, s.d.node.start_point[0] + 1) for s in r.stores if not s.live)


def lines(pairs):
    return [(a.start_point[0] + 1, b.end_point[0] + 1) for a, b in pairs]


def test_a_store_never_read_is_dead():
    assert dead(fl("def f():\n    x = 1\n    return 2\n")) == [("x", 2)]


def test_a_store_overwritten_before_read_is_dead_and_names_the_line():
    r = fl("def f():\n    x = 1\n    x = 2\n    return x\n")
    assert dead(r) == [("x", 2)] and next(s for s in r.stores if not s.live).overwritten_at == 3


def test_a_store_in_one_arm_read_after_the_if_is_live():
    assert dead(fl("def f(a):\n    x = 0\n    if a:\n        x = 1\n    return x\n")) == []


def test_a_store_before_an_if_that_always_overwrites_is_dead():
    assert dead(fl("def f(a):\n    x = 0\n    if a:\n        x = 1\n    else:\n        x = 2\n    return x\n")) == [("x", 2)]


def test_a_store_at_the_end_of_a_loop_read_at_the_next_head_is_live():
    assert dead(fl("def f(xs):\n    prev = None\n    for x in xs:\n        if prev:\n            print(prev)\n        prev = x\n")) == []


def test_a_store_before_try_read_only_in_the_handler_is_live():
    assert dead(fl("def f():\n    x = 1\n    try:\n        g()\n    except ValueError:\n        return x\n")) == []


def test_a_store_read_only_in_finally_is_live():
    assert dead(fl("def f():\n    x = 1\n    try:\n        return g()\n    finally:\n        print(x)\n")) == []


def test_a_store_captured_by_a_closure_is_live_everywhere():
    assert dead(fl("def f():\n    x = 1\n    def g():\n        return x\n    x = 2\n    return g\n")) == []


def test_break_and_continue_follow_the_loop():
    assert dead(fl("def f(xs):\n    for x in xs:\n        y = 1\n        if x:\n            continue\n        print(y)\n")) == []
    assert dead(fl("def f(xs):\n    for x in xs:\n        y = 1\n        if x:\n            break\n    return 0\n")) == [("y", 3)]


def test_a_store_read_after_a_while_true_break_is_live():
    assert dead(fl("def f():\n    while True:\n        y = g()\n        if y:\n            break\n    return y\n")) == []


def test_both_arms_terminating_makes_the_rest_unreachable():
    r = fl("def f(a):\n    if a:\n        return 1\n    else:\n        return 2\n    x = 3\n    print(x)\n")
    assert lines(r.unreachable) == [(6, 7)] and dead(r) == []


def test_statements_after_return_are_unreachable_once_per_block():
    r = fl("def f():\n    return 1\n    a = 2\n    b = 3\n")
    assert lines(r.unreachable) == [(3, 4)]


def test_if_false_body_is_a_dead_branch_and_its_stores_are_not_counted():
    r = fl("def f():\n    if False:\n        x = 1\n    return 0\n")
    assert lines(r.dead_branches) == [(2, 3)] and r.stores == []


def test_if_true_else_is_a_dead_branch():
    r = fl("def f():\n    if True:\n        return 1\n    else:\n        return 2\n")
    assert lines(r.dead_branches) == [(4, 5)]


def test_while_false_body_is_dead():
    assert len(fl("def f():\n    while False:\n        g()\n").dead_branches) == 1


def test_params_are_read_when_live_at_entry_or_captured():
    r = fl("def f(a, b, c, d):\n    g = lambda: d\n    b = 0\n    return a + b + g()\n")
    assert [(n, read) for n, _, read in r.params] == [("a", True), ("b", False), ("c", False), ("d", True)]


def test_dynamic_scope_skips_the_function():
    r = fl("def f(a):\n    x = 1\n    return locals()\n")
    assert r.skipped and r.stores == [] and r.params == []


def test_global_names_are_never_stores():
    assert dead(fl("def f():\n    global x\n    x = 1\n")) == []


def test_attribute_stores_are_recorded_live_for_the_pass_to_judge():
    r = fl("class C:\n    def m(self):\n        self.x = 1\n", name="m")
    assert [(s.d.name, s.d.kind, s.live) for s in r.stores] == [("x", "attribute", True)]


def test_javascript_let_overwritten_before_read():
    assert dead(fl("function f() {\n  let x = 1\n  x = 2\n  return x\n}", language="javascript")) == [("x", 2)]


def test_javascript_catch_binding_unused_is_a_dead_store():
    assert dead(fl("function f() { try { g() } catch (e) { return 0 } }", language="javascript")) == [("e", 1)]


def test_javascript_switch_break_does_not_make_the_rest_unreachable():
    r = fl("function f(a) {\n  let y = 0\n  switch (a) { case 1: y = 1; break; default: return 2 }\n  return y\n}", language="javascript")
    assert r.unreachable == [] and dead(r) == [("y", 2)]  # the default returns, case 1 overwrites: y = 0 is dead
    r = fl("function f(a) {\n  let y = 0\n  switch (a) { case 1: y = 1; break }\n  return y\n}", language="javascript")
    assert dead(r) == []
