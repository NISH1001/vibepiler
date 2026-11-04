"""liveness: stores no read reaches, parameters never read, attributes never read, unreachable code, dead branches."""

from pathlib import Path

from vibepiler.compiler import Compiler
from vibepiler.passes.registry import all_passes

FIX = Path(__file__).parent / "fixtures" / "pyproj"


def run(code, language="python", opt=1):
    return Compiler(code, language=language).run(opt_level=opt, emit=()).program


def found(code, language="python"):
    return [d for d in run(code, language).diagnostics if d.pass_name == "liveness"]


def codes(code, language="python"):
    return [(d.code, d.data.get("names"), d.line) for d in found(code, language)]


def test_pass_is_registered_after_dead_code():
    names = [p.name for p in all_passes()]
    assert names.index("liveness") == names.index("dead-code") + 1


def test_dead_store_charges_its_lines_and_names_the_overwrite():
    [d] = found("def f():\n    x = []\n    x = [1]\n    return x\n")
    assert (d.code, d.data["names"], d.line) == ("dead-store", ["x"], 2)
    assert (d.data["loc"], d.data["overwritten_at"], d.data["action"]) == (1, 3, "delete")


def test_dead_store_with_a_call_charges_nothing_and_says_simplify():
    [d] = found("def f():\n    x = g()\n    return 1\n")
    assert d.data["loc"] == 0 and d.data["action"] == "simplify"


def test_underscore_global_and_read_sibling_are_exempt():
    assert codes("def f():\n    _x = 1\n    global g\n    g = 2\n    a, b = h()\n    return a\n") == []


def test_destructuring_with_no_read_sibling_reports_once():
    assert codes("def f():\n    a, b = h()\n    return 0\n") == [("dead-store", ["a", "b"], 2)]


def test_loop_target_with_a_read_sibling_and_underscore_are_exempt_but_a_lone_unread_target_is_not():
    assert codes("def f(xs):\n    for i, x in enumerate(xs):\n        print(x)\n    for _ in range(3):\n        pass\n") == []
    assert codes("def f(xs):\n    for x in xs:\n        pass\n") == [("dead-store", ["x"], 2)]


def test_with_and_except_targets_are_reported_and_charge_nothing():
    ds = found("def f(p):\n    with open(p) as fh:\n        pass\n    try:\n        g()\n    except Exception as e:\n        pass\n")
    assert [(d.code, d.data["names"], d.data["loc"]) for d in ds] == [("dead-store", ["fh"], 0), ("dead-store", ["e"], 0)]


def test_a_partly_dead_statement_charges_nothing():
    [d] = found("def f():\n    a = b = 1\n    return b\n")
    assert d.data["names"] == ["a"] and d.data["loc"] == 0


def test_unused_param_is_trailing_only_and_skips_self_rest_and_underscore():
    assert codes("class C:\n    def m(self, a, b, _c, *args):\n        return b\n") == []
    assert codes("def f(a, b, c):\n    return a\n") == [("unused-param", ["b", "c"], 1)]


def test_unused_param_exempts_overrides_decorated_entries_dunders_and_stubs():
    src = (
        "class B:\n    def m(self, a):\n        return a\n\nclass C(B):\n    def m(self, a):\n        return 1\n\n"
        "class D(External):\n    def m(self, a):\n        return 1\n\n"
        "class E:\n    def __exit__(self, kind, exc, tb):\n        return None\n\n"
        "@route\ndef h(request):\n    return 1\n\ndef stub(a):\n    ...\n"
    )
    assert codes(src) == []


def test_unused_attribute_is_reported_on_the_class_with_its_lines():
    ds = [d for d in found("class C:\n    def __init__(self):\n        self.cache = {}\n        self.hits = 0\n    def get(self):\n        return self.hits\n") if d.code == "unused-attribute"]
    assert len(ds) == 1 and ds[0].data["names"] == ["cache"] and ds[0].data["lines"] == [3] and ds[0].data["loc"] == 1 and ds[0].data["kind"] == "class"


def test_unused_attribute_exempts_dunders_data_classes_getattr_and_unknown_bases():
    src = (
        "class A(Unknown):\n    def __init__(self):\n        self.x = 1\n\n"
        "class B:\n    def __getattr__(self, k):\n        return k\n    def __init__(self):\n        self.y = 1\n\n"
        "@dataclass\nclass D:\n    def __post_init__(self):\n        self.z = 1\n\n"
        "class E:\n    def __init__(self):\n        self.__dunder__ = 1\n"
    )
    assert codes(src) == []


def test_unreachable_code_and_dead_branch_charge_their_spans():
    ds = {d.code: d for d in found("def f(a):\n    if False:\n        g()\n        h()\n    return a\n    k()\n")}
    assert ds["dead-branch"].data["span"] == [2, 4] and ds["dead-branch"].data["loc"] == 3
    assert ds["unreachable-code"].data["span"] == [6, 6] and ds["unreachable-code"].data["loc"] == 1


def test_findings_are_counted():
    prog = run("def f():\n    x = 1\n    return 0\n\ndef g(a):\n    return 0\n")
    assert prog.metrics["liveness"]["dead_stores"] == 1 and prog.metrics["liveness"]["unused_params"] == 1


def test_javascript_findings_come_through_the_same_pass():
    assert sorted(codes("function f(a, b) { const x = 1; return a }", language="javascript")) == [("dead-store", ["x"], 1), ("unused-param", ["b"], 1)]


def test_javascript_unused_this_attribute():
    assert [c for c, _, _ in codes("class C { constructor() { this.cache = {}; this.n = 0 } get() { return this.n } }", language="javascript")] == ["unused-attribute"]


def test_augmented_assignment_mutates_in_place_and_is_never_a_dead_store():
    assert codes("def f(acc, x):\n    acc |= x\n") == []
    assert codes("function f(acc, x) { acc += x }", language="javascript") == []
