"""The JavaScript and TypeScript adapters classify each statement into a flow shape."""

from flowutil import body

from vibepiler.frontend.flow import Branch, Jump, Loop, Plain, Try


def js(code, name="f"):
    return body(code, name=name, language="javascript")


def test_const_is_plain_with_a_local_def_and_rhs_uses():
    spec, sf, _, stmts = js("function f(a) { const x = a + 1 }")
    p = spec.classify(stmts[0], sf)
    assert isinstance(p, Plain) and [(d.name, d.kind, d.rhs_has_call) for d in p.defs] == [("x", "local", False)] and p.uses == {"a"}


def test_call_and_new_mark_rhs_has_call():
    spec, sf, _, stmts = js("function f() { let x = g(); let y = new Y() }")
    assert spec.classify(stmts[0], sf).defs[0].rhs_has_call and spec.classify(stmts[1], sf).defs[0].rhs_has_call


def test_a_declaration_without_a_value_defines_nothing():
    spec, sf, _, stmts = js("function f() { let x }")
    assert spec.classify(stmts[0], sf).defs == []


def test_assignment_and_augmented_assignment():
    spec, sf, _, stmts = js("function f(x) { x = 2; x += 1 }")
    a, b = spec.classify(stmts[0], sf), spec.classify(stmts[1], sf)
    assert a.defs[0].name == "x" and a.uses == set() and b.uses == {"x"} and b.defs[0].name == "x"


def test_const_destructuring_shares_a_group():
    spec, sf, _, stmts = js("function f(o) { const { a, b: c } = o; return a }")
    defs = spec.classify(stmts[0], sf).defs
    assert [d.name for d in defs] == ["a", "c"] and defs[0].group == defs[1].group != 0 and defs[0].kind == "destructure"


def test_this_assignment_is_an_attribute_def():
    spec, sf, _, stmts = js("class C { m() { this.x = 1 } }", name="m")
    d = spec.classify(stmts[0], sf).defs[0]
    assert (d.name, d.kind, d.attr_of) == ("x", "attribute", "this")


def test_else_if_chain_flattens_into_arms():
    spec, sf, _, stmts = js("function f(a, b) { if (a) { return 1 } else if (b) { return 2 } else { return 3 } }")
    br = spec.classify(stmts[0], sf)
    assert isinstance(br, Branch) and len(br.arms) == 3 and br.has_else and br.uses == {"a", "b"}


def test_if_false_is_constant():
    spec, sf, _, stmts = js("function f() { if (false) { g() } if (!0) { g() } }")
    assert spec.classify(stmts[0], sf).constant is False and spec.classify(stmts[1], sf).constant is True


def test_switch_is_a_branch_with_a_default():
    spec, sf, _, stmts = js("function f(a) { switch (a) { case 1: return 1; default: return 2 } }")
    br = spec.classify(stmts[0], sf)
    assert isinstance(br, Branch) and len(br.arms) == 2 and br.has_else and br.uses == {"a"}


def test_for_of_and_c_style_for_are_loops():
    spec, sf, _, stmts = js("function f(xs) { for (const x of xs) {} for (let i = 0; i < 3; i++) {} }")
    a, b = spec.classify(stmts[0], sf), spec.classify(stmts[1], sf)
    assert isinstance(a, Loop) and [d.name for d in a.defs] == ["x"] and a.defs[0].kind == "loop" and a.uses == {"xs"}
    assert isinstance(b, Loop) and [d.name for d in b.defs] == ["i"] and b.uses == {"i"}


def test_labelled_break_names_its_loop():
    spec, sf, _, stmts = js("function f(xs) { outer: for (const x of xs) { for (const y of xs) { break outer } } }")
    lp = spec.classify(stmts[0], sf)
    inner = spec.classify(lp.body[0], sf)
    jump = spec.classify(inner.body[0], sf)
    assert lp.label == "outer" and isinstance(jump, Jump) and (jump.kind, jump.label) == ("break", "outer")


def test_a_break_inside_a_switch_is_not_a_loop_jump():
    spec, sf, _, stmts = js("function f(xs, a) { for (const x of xs) { switch (a) { case 1: g(); break; default: h() } } }")
    sw = spec.classify(spec.classify(stmts[0], sf).body[0], sf)
    assert isinstance(spec.classify(sw.arms[0][1][1], sf), Plain)


def test_try_catch_finally():
    spec, sf, _, stmts = js("function f() { try { g() } catch (e) { h() } finally { k() } }")
    t = spec.classify(stmts[0], sf)
    assert isinstance(t, Try) and [d.name for d in t.handlers[0][0]] == ["e"] and len(t.final) == 1


def test_throw_is_a_raise_jump():
    spec, sf, _, stmts = js("function f(a) { throw new Error(a) }")
    j = spec.classify(stmts[0], sf)
    assert (j.kind, j.uses) == ("raise", {"Error", "a"})


def test_object_shorthand_reads_its_names():
    spec, sf, _, stmts = js("function f(a) { return { a } }")
    assert spec.classify(stmts[0], sf).uses == {"a"}


def test_params_mark_rest_and_destructured():
    spec, sf, node, _ = js("function f(a, { b }, c = 1, ...rest) {}")
    assert spec.function_params(node, sf) == [("a", "plain"), ("{ b }", "rest"), ("c", "plain"), ("rest", "rest")]


def test_typescript_typed_params_are_plain():
    spec, sf, node, _ = body("function f(a: number, ...rest: string[]): number { return a }", language="typescript")
    assert spec.function_params(node, sf) == [("a", "plain"), ("rest", "rest")]


def test_captured_names_and_dynamic_scope():
    spec, sf, node, _ = js("function f(a, b) { const g = () => a; function h() { return b } return [g, h] }")
    assert spec.captured_names(node, sf) == {"a", "b"}
    spec, sf, node, _ = js("function f() { return arguments.length }")
    assert spec.has_dynamic_scope(node, sf)
