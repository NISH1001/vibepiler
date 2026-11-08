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


# ------------------------------------------------------------------ the metric
from vibepiler.passes.liveness import Liveness  # noqa: E402
from vibepiler.passes.registry import all_metrics  # noqa: E402

KINDS0 = {"stores": 0, "params": 0, "attributes": 0, "symbols": 0, "imports": 0}


def node(prog, name):
    return next(x for x in prog.graph.nodes() if x.name == name)


def test_function_liveness_counts_params_and_stores():
    prog = run("def f(a, b):\n    x = a\n    y = 2\n    return x\n")
    lv = Liveness.of(node(prog, "f"))
    assert (lv.defined, lv.live, lv.dead["params"], lv.dead["stores"]) == (4, 2, 1, 1) and lv.value == 0.5


def test_module_liveness_contains_its_functions_symbols_and_imports():
    src = "import os\nimport sys\n\ndef f(a):\n    x = 1\n    return a\n\ndef g():\n    return sys.argv\n"
    prog = run(src)
    m = Liveness.of(next(x for x in prog.graph.nodes() if x.kind.value == "module"))
    # f, g, os, sys and f's a and x: 6 definitions. Dead: the os import, the x store, and f and g themselves, since a
    # snippet has no entry points and dead-code calls both dead symbols.
    assert (m.defined, m.live) == (6, 2) and m.dead == {**KINDS0, "stores": 1, "imports": 1, "symbols": 2}


def test_class_liveness_counts_methods_and_attributes():
    prog = run("class C:\n    def __init__(self):\n        self.cache = {}\n        self.hits = 0\n    def get(self):\n        return self.hits\n")
    lv = Liveness.of(node(prog, "C"))
    assert (lv.defined, lv.dead["attributes"]) == (4, 1)  # __init__, get, cache, hits


def test_repo_liveness_is_a_sum_not_a_mean_and_excludes_tests():
    prog = Compiler(FIX).run(opt_level=1, emit=()).program
    r = prog.metrics["liveness_score"]
    assert r["defined"] == sum(f["defined"] for f in r["folders"]) and r["live"] == sum(f["live"] for f in r["folders"])
    assert r["tests"]["defined"] > 0 and 0.0 <= r["value"] <= 1.0
    assert Liveness.of(prog).value == r["value"]


def test_the_fragment_names_only_nonzero_kinds():
    assert str(Liveness(defined=10, live=8, dead={**KINDS0, "stores": 1, "params": 1})) == "live 80.0% (8 of 10 definitions; dead: 1 stores, 1 params)"
    assert str(Liveness(defined=0, live=0, dead=dict(KINDS0))) == "live 100.0% (0 of 0 definitions; nothing dead)"


def test_all_metrics_lists_bloat_then_live_then_the_measures():
    assert [m.label for m in all_metrics()] == ["bloat", "live", "cognitive", "cyclomatic", "mi", "typed", "documented"]
    assert Liveness.better == "high" and Liveness(defined=10, live=8, dead=dict(KINDS0)).trips("<", 0.9)


# ------------------------------------------------------------------ the fixtures, and ruff as the oracle for Python
import json  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402

import pytest  # noqa: E402

RUFF = shutil.which("ruff")
JSFIX = Path(__file__).parent / "fixtures" / "jsproj"


@pytest.mark.skipif(RUFF is None, reason="ruff is a dev dependency")
def test_ruff_f841_is_contained_in_dead_store_and_every_extra_is_a_named_case():
    out = subprocess.run([RUFF, "check", "--select", "F841,ARG", "--output-format", "json", "--no-cache", "--isolated", str(FIX / "src")], capture_output=True, text=True)
    ruff = json.loads(out.stdout or "[]")
    rel = lambda r: Path(r["filename"]).resolve().relative_to(FIX.resolve()).as_posix()  # noqa: E731
    f841 = {(rel(r), r["location"]["row"]) for r in ruff if r["code"] == "F841"}
    arg = {(rel(r), re.search(r"`([^`]+)`", r["message"]).group(1)) for r in ruff if r["code"].startswith("ARG")}
    prog = Compiler(FIX).run(opt_level=1, emit=()).program
    src = [d for d in prog.diagnostics if (d.file or "").startswith("src/")]
    ours = {(d.file, d.line) for d in src if d.code == "dead-store"}
    params = {(d.file, n) for d in src if d.code == "unused-param" for n in d.data["names"]}
    assert f841 and f841 <= ours
    named = {(d.file, d.line) for d in src if d.code == "dead-store" and (d.data.get("overwritten_at") or len(d.data["names"]) > 1 or d.data.get("binding") == "loop")}
    assert ours - f841 <= named
    assert params and params <= arg


def test_the_python_fixture_leftovers_are_each_found():
    prog = Compiler(FIX).run(opt_level=1, emit=()).program
    got = {(d.code, tuple(d.data.get("names") or ())) for d in prog.diagnostics if d.file == "src/app/leftovers.py" and d.pass_name == "liveness"}
    assert got == {
        ("dead-store", ("items",)), ("dead-store", ("data",)), ("dead-store", ("result",)), ("unused-param", ("channel",)),
        ("dead-store", ("first", "second")), ("dead-store", ("fh",)), ("dead-store", ("exc",)), ("dead-branch", ()), ("unreachable-code", ()),
        ("unused-attribute", ("cache",)),
    }


def test_the_typescript_fixture_leftovers_are_each_found():
    prog = Compiler(JSFIX).run(opt_level=1, emit=()).program
    got = {(d.code, tuple(d.data.get("names") or ())) for d in prog.diagnostics if d.file == "src/leftovers.ts" and d.pass_name == "liveness"}
    assert got == {("dead-store", ("items",)), ("dead-store", ("data",)), ("dead-store", ("result",)), ("unused-param", ("channel",)), ("unused-attribute", ("lastSeen",))}


# ------------------------------------------------------------------ final review: never `delete` working code
def test_review_js_switch_with_a_return_per_case_has_no_unreachable_code():
    assert codes('function kind(k) {\n  switch (k) {\n    case "a": return 1\n    case "b": return 2\n    default: return 3\n  }\n}', language="javascript") == []
    assert codes("function f(xs) {\n  let last\n  for (const x of xs) {\n    switch (x) {\n      case 1: continue\n      default: last = x\n    }\n  }\n  return last\n}", language="javascript") == []


def test_review_js_function_declarations_after_return_are_hoisted_not_unreachable():
    assert codes("function parse(s) {\n  return clean(trim(s))\n  function trim(x) { return x }\n  function clean(x) { return x }\n}", language="javascript") == []


def test_review_a_store_in_finally_read_after_the_try_is_live():
    assert codes("def f():\n    try:\n        g()\n    finally:\n        x = 1\n    return x\n") == []
    assert codes("function f() {\n  let y\n  try { g() } finally { y = 1 }\n  return y\n}", language="javascript") == []


def test_review_attribute_reads_through_any_receiver_count():
    src = (
        "class A:\n    def __init__(self):\n        self.x = 1\n        self.y = 2\n        self.label = 3\n        self.k = 4\n        self.z = 5\n\n"
        "def use(items, kids):\n    return get().x, items[0].y, kids[0].label, getattr(items, 'z')\n\n"
        "class B(A):\n    def m(self):\n        return super().k\n"
    )
    assert [c for c in codes(src) if c[0] == "unused-attribute"] == []
    js = "class A { constructor() { this.x = 1; this.y = 2; this.w = 3 } m() { const { w } = this; return w } }\nfunction use(arr) { return mk().x + arr[0].y }"
    assert [c for c in codes(js, language="javascript") if c[0] == "unused-attribute"] == []


def test_review_a_with_body_may_be_cut_short_by_a_suppressing_context_manager():
    assert codes("def f(d, k):\n    value = None\n    with contextlib.suppress(KeyError):\n        value = d[k]\n    return value\n") == []


def test_review_a_base_method_overridden_below_keeps_its_parameters():
    src = "class Visitor:\n    def visit(self, node):\n        return None\n\nclass Printer(Visitor):\n    def visit(self, node):\n        print(node)\n"
    assert [c for c in codes(src) if c[0] == "unused-param"] == []
    assert [c for c in codes("class X implements H {\n  handle(req, res) { return req }\n}", language="typescript") if c[0] == "unused-param"] == []


def test_review_a_dead_branch_holding_a_yield_is_not_reported():
    assert codes("def gen():\n    if False:\n        yield\n    return\n") == []


def test_review_an_annotation_reads_its_names():
    assert codes("def f():\n    T = int\n    v: T = 3\n    return v\n") == []
