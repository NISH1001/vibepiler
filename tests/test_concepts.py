"""Concept layer (-O3): functions grouped by meaning across the whole codebase, laid out as a map."""

import pytest

from test_semantic import FIX, FakeEmbedder
from vibepiler.backend import emit_html, emit_markdown
from vibepiler.config import Config
from vibepiler.ir.builder import build_program
from vibepiler.passes.registry import all_passes, run_pass
from vibepiler.semantic import embed
from vibepiler.semantic.concepts import build_concepts, identifier_words, project_2d


@pytest.fixture(autouse=True)
def fake_embedder(monkeypatch, tmp_path):
    monkeypatch.setattr(embed, "EMBEDDER_FACTORY", lambda model: FakeEmbedder())
    monkeypatch.setattr(embed, "CACHE_DIR", tmp_path / "emb")


@pytest.fixture()
def program():
    prog = build_program(FIX, config=Config.load(FIX))
    run_pass("entry-points", prog)
    return prog


def test_concepts_pass_is_registered_at_o3():
    by_name = {p.name: p for p in all_passes()}
    assert by_name["concepts"].cost == 3


def test_identifier_words_split_and_drop_noise():
    assert identifier_words("RenderEmailBody") == ["render", "email", "body"]
    assert identifier_words("get_user_id_v2") == ["user", "id"]  # 'get' and bare version tags are noise


def test_build_concepts_groups_functions_by_meaning_with_labels_and_positions(program):
    result = build_concepts(program)
    concepts = result["concepts"]
    assert concepts and all(c["size"] >= 3 for c in concepts)
    by_member = {m: c for c in concepts for m in c["members"]}
    email = [by_member.get(f"sym:src/app/utils.py::{n}") for n in ("render_email", "send_email", "email_subject")]
    assert email[0] is not None and all(c is email[0] for c in email)  # the email trio share one concept
    assert "email" in email[0]["label"]
    for c in concepts:
        assert c["label"] and c["exemplar"] in c["members"]
        assert 0 <= c["x"] <= 1 and 0 <= c["y"] <= 1
        assert sum(c["modules"].values()) == c["size"]
    assert all(e["src"] != e["dst"] and e["calls"] >= 1 for e in result["edges"])


def test_scattered_concept_is_reported(tmp_path):
    for mod in ("a", "b", "c"):
        (tmp_path / f"{mod}.py").write_text(
            f"def send_email_{mod}(user, subject):\n    body = 'Hello ' + user + ', about ' + subject\n    return '<html>' + body + '</html>'\n\n"
            f"def email_subject_{mod}(user, subject):\n    return 'Hello ' + user + ': ' + subject\n\n"
            f"def render_email_{mod}(user):\n    body = 'Hello ' + user\n    return '<html>' + body + '</html>'\n"
        )
    (tmp_path / "math_stuff.py").write_text(
        "".join(f"def sum_{i}(xs):\n    total = 0\n    for x in xs:\n        if x % {i + 2} == 0:\n            total += x\n    return total\n\n" for i in range(4))
    )
    prog = build_program(tmp_path)
    diags = run_pass("concepts", prog)
    scattered = [d for d in diags if d.code == "scattered-concept"]
    assert scattered, [c["label"] for c in prog.metrics["concepts"]["concepts"]]
    assert {"a.py", "b.py", "c.py"} <= set(scattered[0].data["modules"])


def test_responsibility_groups_are_labelled(program):
    program.config.thresholds.responsibility_similarity = 0.3
    diags = run_pass("responsibilities", program)
    mixed = [d for d in diags if d.code == "mixed-responsibilities"]
    assert mixed and all(g["label"] for d in mixed for g in d.data["clusters"])


def test_reports_include_the_concept_layer(program):
    run_pass("concepts", program)
    assert "## Concepts" in emit_markdown(program)
    html = emit_html(program)
    assert 'data-view="concepts"' in html and '"concepts":{' in html


def test_concepts_skip_without_backend(program, monkeypatch):
    monkeypatch.setattr(embed, "EMBEDDER_FACTORY", lambda model: None)
    assert run_pass("concepts", program) == []
    assert "skipped" in program.metrics["concepts"]


def test_projection_falls_back_to_pca_and_uses_umap_when_installed(monkeypatch):
    import sys
    import types

    import numpy as np

    M = np.random.default_rng(0).normal(size=(12, 8))
    xy, method = project_2d(M, prefer_umap=False)
    assert method == "pca" and xy.shape == (12, 2) and xy.min() >= 0 and xy.max() <= 1
    xy2, method2 = project_2d(M, prefer_umap=False)
    assert np.allclose(xy, xy2)  # deterministic

    class FakeUMAP:
        def __init__(self, **kw):
            assert kw["random_state"] == 0 and kw["n_neighbors"] < M.shape[0]

        def fit_transform(self, X):
            return X[:, :2] * 3 + 1

    monkeypatch.setitem(sys.modules, "umap", types.SimpleNamespace(UMAP=FakeUMAP))
    xy3, method3 = project_2d(M)
    assert method3 == "umap" and xy3.shape == (12, 2) and xy3.min() >= 0 and xy3.max() <= 1


# ---------------------------------------------------------------- the meaning map


def test_every_embedded_function_gets_a_point_on_the_map(program):
    from vibepiler.semantic.embed import symbol_vectors

    result = build_concepts(program)
    nodes, _, _ = symbol_vectors(program)
    points = result["points"]
    assert [p["id"] for p in points] == [n.id for n in nodes]
    assert all(0 <= p["x"] <= 1 and 0 <= p["y"] <= 1 for p in points)
    owner = {m: ci for ci, c in enumerate(result["concepts"]) for m in c["members"]}
    assert all(p["c"] == owner.get(p["id"], -1) for p in points)


def test_concepts_nest_in_themes_and_sit_on_a_member(program):
    result = build_concepts(program)
    concepts, themes = result["concepts"], result["themes"]
    assert themes and sorted(ci for t in themes for ci in t["concepts"]) == list(range(len(concepts)))
    for ti, t in enumerate(themes):
        assert t["label"] and t["size"] == sum(concepts[ci]["size"] for ci in t["concepts"])
        assert all(concepts[ci]["theme"] == ti for ci in t["concepts"])
    at = {p["id"]: (p["x"], p["y"]) for p in result["points"]}
    for c in concepts:
        assert (c["x"], c["y"]) in {at[m] for m in c["members"]}  # labelled on a function it has, not between clumps


def test_labels_do_not_repeat_a_word_as_its_plural():
    from vibepiler.semantic.concepts import label_for

    docs = [["tool", "call"], ["tool", "calls"], ["tools", "call"]]
    label, words = label_for(docs, {"tool": 1.0, "tools": 1.0, "call": 1.0, "calls": 1.0})
    assert sorted(words) == ["call", "tool"]


def test_react_hook_prefix_is_not_a_concept_word():
    assert identifier_words("useValidationStream") == ["validation", "stream"]


def test_migrations_are_not_embedded(tmp_path):
    from vibepiler.semantic.embed import symbol_vectors

    (tmp_path / "alembic" / "versions").mkdir(parents=True)
    (tmp_path / "alembic" / "versions" / "0001_init.py").write_text("def upgrade():\n    op = 1\n    return op\n\ndef downgrade():\n    op = 2\n    return op\n")
    (tmp_path / "app.py").write_text("def handler(x):\n    y = x + 1\n    return y\n")
    prog = build_program(tmp_path)
    nodes, _, _ = symbol_vectors(prog)
    assert [n.name for n in nodes] == ["handler"]


def test_duplicate_findings_name_their_nodes(program):
    for name in ("duplicates", "semantic-duplicates"):
        for d in run_pass(name, program):
            ids = d.data.get("ids")
            assert ids and all(program.graph.has_node(i) for i in ids) and len(ids) == len(d.data["members"])


def test_report_draws_the_meaning_map(program):
    run_pass("concepts", program)
    html = emit_html(program)
    assert 'id="meaning"' in html and "<canvas" in html and "cy2" not in html


def test_hidden_overlays_never_cover_the_map():
    """The map's empty-state layer covers the canvas; a class `display` rule beats the `hidden` attribute unless one rule
    says otherwise, and then the invisible layer swallows every hover, click, drag and scroll."""
    from importlib import resources

    viewer = resources.files("vibepiler.backend").joinpath("viewer.html").read_text(encoding="utf-8")
    assert "[hidden] { display: none !important; }" in viewer


def test_map_colours_by_any_measure_with_a_live_threshold():
    """Health is the overview; every per-function measure the compiler stores can colour the map instead, with a
    threshold slider the viewer applies on the spot, no recompile."""
    from importlib import resources

    viewer = resources.files("vibepiler.backend").joinpath("viewer.html").read_text(encoding="utf-8")
    for mode in ("health", "bloat", "complexity", "live", "size", "nesting", "callers", "folder", "theme"):
        assert f'<option value="{mode}"' in viewer
    assert '<option value="health" selected>' in viewer
    assert 'id="mm-thresh"' in viewer and 'type="range"' in viewer
