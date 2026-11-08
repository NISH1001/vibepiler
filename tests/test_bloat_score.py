"""bloat-score: one number in [0, 1] per function, class, module, folder and repo. 1 means it could all go."""

from pathlib import Path

from vibepiler.config import Config
from vibepiler.ir.builder import build_program
from vibepiler.ir.model import NodeKind
from vibepiler.passes.registry import all_passes, run_passes

FIX = Path(__file__).parent / "fixtures" / "pyproj"


def scored(opt=2):
    prog = build_program(FIX, config=Config.load(FIX))
    run_passes(prog, opt_level=opt)
    return prog


def test_bloat_score_runs_last_and_at_every_level():
    passes = all_passes()
    assert passes[-1].name == "bloat-score" and passes[-1].cost == 1


def test_every_unit_gets_a_score_in_range():
    prog = scored()
    g = prog.graph
    units = [n for n in g.nodes((NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS, NodeKind.MODULE))]
    assert units
    for n in units:
        b = n.attrs["bloat"]
        assert 0.0 <= b["score"] <= 1.0 and 0.0 <= b["waste"] <= 1.0 and 0.0 <= b["tangle"] <= 1.0
        assert abs(b["score"] - (1 - (1 - b["waste"]) * (1 - b["tangle"]))) < 1e-6
        assert 0 <= b["removable_loc"] <= max(n.loc, 1)
    repo = prog.metrics["bloat_score"]
    assert 0.0 <= repo["score"] <= 1.0 and repo["loc"] > 0 and repo["formula"]
    assert {f["path"] for f in repo["folders"]} and all(0 <= f["score"] <= 1 for f in repo["folders"])


def test_dead_code_is_pure_waste_and_clean_code_scores_zero():
    prog = scored()
    g = prog.graph
    dead = g.node("sym:src/app/utils.py::unused_fn")
    assert dead.attrs["bloat"]["waste"] == 1.0 and dead.attrs["bloat"]["score"] == 1.0
    mentioned = {q for d in prog.diagnostics for q in d.data.get("members", [])}
    clean = [n for n in g.nodes((NodeKind.FUNCTION, NodeKind.METHOD)) if not [d for d in prog.diagnostics if d.node_id == n.id] and n.qualname not in mentioned]
    assert clean and all(n.attrs["bloat"]["score"] == 0.0 for n in clean if n.attrs["bloat"]["tangle"] == 0.0)


def test_duplicate_groups_keep_one_copy_and_charge_the_rest_whole(tmp_path):
    body = "    total = 0\n    for item in items:\n        if item.active and item.price > 0:\n            total += item.price * item.qty\n        else:\n            total -= 1\n    return total\n"
    (tmp_path / "shop.py").write_text(f"def total_a(items):\n{body}\n\ndef total_b(items):\n{body}")
    (tmp_path / "main.py").write_text("from shop import total_a, total_b\n\nif __name__ == '__main__':\n    print(total_a([]), total_b([]))\n")
    prog = build_program(tmp_path)
    run_passes(prog, opt_level=2)
    dup = next(d for d in prog.diagnostics if d.code == "duplicate-function")
    assert dup.data["confidence"] == "proven" and dup.data["action"] == "fold"
    a, b = prog.graph.node("sym:shop.py::total_a"), prog.graph.node("sym:shop.py::total_b")
    assert a.attrs["bloat"]["removable_loc"] == 0  # the first copy is the keeper
    assert b.attrs["bloat"]["waste"] == 1.0 and b.attrs["bloat"]["removable_loc"] == b.loc
    assert dup.data["lines_freed"] == b.loc


def test_plan_sums_exactly_to_removable_lines_and_never_double_charges(tmp_path):
    (tmp_path / "main.py").write_text("from util import used\n\nif __name__ == '__main__':\n    used()\n")
    (tmp_path / "util.py").write_text("def used():\n    return 1\n\n\ndef dead_stub(x):\n    raise NotImplementedError\n\n\ndef dead_long(x):\n    y = x + 1\n    z = y * 2\n    return z\n")
    prog = build_program(tmp_path)
    run_passes(prog, opt_level=2)
    stub = prog.graph.node("sym:util.py::dead_stub")
    assert stub.attrs["bloat"]["removable_loc"] == stub.loc  # dead AND a stub: charged once, not twice
    repo = prog.metrics["bloat_score"]
    assert abs(sum(p["lines"] for p in repo["plan"]) - (repo["removable_loc"] + repo["model_loc"])) < 1e-6
    assert all(p["confidence"] in ("proven", "inferred", "model") for p in repo["plan"])
    assert abs(sum(repo["waste_by_confidence"].values()) - (repo["removable_loc"] + repo["model_loc"])) < 1e-6
    assert abs(repo["waste_by_confidence"].get("model", 0.0) - repo["model_loc"]) < 1e-6  # model lines never enter the score


def test_module_waste_is_its_symbols_waste_over_its_lines():
    prog = scored()
    g = prog.graph
    mod = g.node("mod:src/app/utils.py")
    b = mod.attrs["bloat"]
    own = sum(n.attrs["bloat"]["removable_loc"] for n in g.nodes((NodeKind.FUNCTION, NodeKind.CLASS)) if n.module == mod.id and g.parent(n.id).kind == NodeKind.MODULE)
    assert b["removable_loc"] >= own > 0
    assert abs(b["waste"] - min(1.0, b["removable_loc"] / mod.loc)) < 1e-6


def test_repo_score_aggregates_source_modules_and_keeps_tests_apart():
    prog = scored()
    repo = prog.metrics["bloat_score"]
    mods = prog.graph.nodes(NodeKind.MODULE)
    src = [m for m in mods if not m.attrs.get("is_test")]
    assert abs(repo["removable_loc"] - sum(m.attrs["bloat"]["removable_loc"] for m in src)) < 1e-6
    assert repo["loc"] == sum(m.loc for m in src) and repo["tests"]["loc"] == sum(m.loc for m in mods if m.attrs.get("is_test"))
    assert repo["modules_top"][0]["removable_loc"] >= repo["modules_top"][-1]["removable_loc"]  # ranked by lines, not density
    assert repo["conditions"]["opt_level"] == 2 and "linked" in repo["conditions"]
    assert isinstance(repo["tangle_reasons"], list)


def test_a_mostly_dead_module_is_reported_as_bloated(tmp_path):
    (tmp_path / "main.py").write_text("from util import used\n\nif __name__ == '__main__':\n    used()\n")
    dead = "".join(f"def dead_{i}(x):\n    y = x + {i}\n    z = y * 2\n    w = z - 1\n    v = w + 3\n    u = v * 4\n    return u\n\n\n" for i in range(4))
    (tmp_path / "util.py").write_text(f"def used():\n    return 1\n\n\n{dead}")
    prog = build_program(tmp_path)
    run_passes(prog, opt_level=2)
    bloated = [d for d in prog.diagnostics if d.code == "bloated-module"]
    assert bloated and bloated[0].file == "util.py"
    assert prog.graph.node("mod:util.py").attrs["bloat"]["score"] >= 0.6
    assert "removable" in bloated[0].message


def test_tangle_terms_come_from_the_findings_own_numbers(tmp_path):
    params = ", ".join(f"p{i}" for i in range(9))
    (tmp_path / "main.py").write_text(f"def wide({params}):\n    return p0\n\nif __name__ == '__main__':\n    wide(1, 2, 3, 4, 5, 6, 7, 8, 9)\n")
    prog = build_program(tmp_path)
    run_passes(prog, opt_level=2)
    wide = prog.graph.node("sym:main.py::wide")
    assert abs(wide.attrs["bloat"]["tangle"] - (9 - prog.config.thresholds.params) / 9) < 1e-6
    assert wide.attrs["bloat"]["waste"] == 0.0


def test_dead_module_constants_count_toward_the_module(tmp_path):
    (tmp_path / "main.py").write_text("from util import used\n\nif __name__ == '__main__':\n    used()\n")
    (tmp_path / "util.py").write_text("UNUSED_LIMIT = 10\n\n\ndef used():\n    return 1\n")
    prog = build_program(tmp_path)
    run_passes(prog, opt_level=2)  # must not crash on a dead variable
    mod = prog.graph.node("mod:util.py")
    dead_vars = [d for d in prog.diagnostics if d.code == "dead-symbol" and d.data.get("kind") == "variable"]
    assert mod.attrs["bloat"]["removable_loc"] >= (1 if dead_vars else 0)


def test_a_tangled_module_with_nothing_to_delete_says_so(tmp_path):
    body = "".join(f"    if x == {i}:\n        return {i}\n" for i in range(30)) + "    return -1\n"  # cognitive 30: tangle (30-15)/30 = 0.5
    (tmp_path / "knot.py").write_text(f"def classify(x):\n{body}")
    (tmp_path / "main.py").write_text("from knot import classify\n\nif __name__ == '__main__':\n    classify(3)\n")
    prog = build_program(tmp_path)
    run_passes(prog, opt_level=2)
    d = next(d for d in prog.diagnostics if d.code == "bloated-module" and d.file == "knot.py")
    assert "nothing to delete" in d.message and "high-cognitive-complexity" in d.message and "removable lines" not in d.message
    assert d.severity.label == "info" and d.data["action"] == "simplify"


def test_symbols_alive_only_by_name_are_counted_as_the_unverified_band(tmp_path):
    (tmp_path / "shapes.py").write_text("class Box:\n    def render(self):\n        return 1\n\n    def unused(self):\n        return 2\n")
    (tmp_path / "main.py").write_text("from shapes import Box\n\n\ndef draw(obj):\n    return obj.render()\n\n\nif __name__ == '__main__':\n    draw(Box())\n")
    prog = build_program(tmp_path)
    run_passes(prog, opt_level=2)
    assert prog.graph.node("sym:shapes.py::Box.render").attrs.get("alive_by_name") is True  # obj's type is unknown; only the name matched
    assert prog.metrics["entry_points"]["kept_by_name"] == {"symbols": 1, "loc": 2}
    assert prog.metrics["bloat_score"]["unverified_loc"] == 2.0
    assert "Box.unused" in {d.data["qualname"] for d in prog.diagnostics if d.code == "dead-symbol"}


# ------------------------------------------------------------------ liveness findings claim lines through the same budget
def _snippet(src):
    from vibepiler.compiler import Compiler

    return Compiler(src).run(opt_level=1, emit=()).program


def test_liveness_findings_claim_their_lines_once():
    prog = _snippet("def f():\n    x = []\n    y = []\n    return 0\n\ndef dead():\n    z = 1\n    z = 2\n    return z\n\nif __name__ == '__main__':\n    f()\n")
    by = {}
    for p in prog.metrics["bloat_score"]["plan"]:
        by.setdefault(p["qualname"], []).append((p["code"], p["lines"]))
    assert sorted(by["f"]) == [("dead-store", 1.0), ("dead-store", 1.0)]
    assert by["dead"] == [("dead-symbol", 4.0)]  # dead whole; its inner dead store adds nothing


def test_unused_param_is_in_findings_but_not_in_the_plan():
    prog = _snippet("def f(a, b):\n    return a\n")
    assert any(d.code == "unused-param" for d in prog.diagnostics)
    assert not any(p["code"] == "unused-param" for p in prog.metrics["bloat_score"]["plan"])


def test_dead_store_with_a_call_frees_no_lines():
    prog = _snippet("def f():\n    x = g()\n    return 0\n")
    assert not any(p["code"] == "dead-store" for p in prog.metrics["bloat_score"]["plan"])


def test_liveness_findings_are_proven_and_attributes_inferred():
    prog = _snippet("class C:\n    def __init__(self):\n        self.cache = []\n\ndef f():\n    x = []\n    return C()\n\nif __name__ == '__main__':\n    f()\n")
    conf = {p["code"]: p["confidence"] for p in prog.metrics["bloat_score"]["plan"]}
    assert conf["dead-store"] == "proven" and conf["unused-attribute"] == "inferred"
