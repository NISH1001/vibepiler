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
