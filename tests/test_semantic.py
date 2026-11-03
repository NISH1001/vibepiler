"""Semantic tier (-O3): embedding-based passes. Uses a deterministic fake embedder so tests stay offline."""

import math
import os
import re
import zlib
from pathlib import Path

import pytest

from vibepiler.config import Config
from vibepiler.ir.builder import build_program
from vibepiler.passes.registry import all_passes, run_pass, run_passes
from vibepiler.semantic import embed

FIX = Path(__file__).parent / "fixtures" / "pyproj"


class FakeEmbedder:
    """Bag-of-tokens vectors: similar wording -> similar vectors. Enough to exercise the pass logic."""

    name = "fake"
    dim = 512

    def embed(self, texts):
        out = []
        for t in texts:
            vec = [0.0] * self.dim
            for tok in set(re.findall(r"[A-Za-z_]+|\d+|[%=<>+*/-]+", t)):
                vec[zlib.crc32(tok.encode()) % self.dim] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


@pytest.fixture(autouse=True)
def fake_embedder(monkeypatch, tmp_path):
    monkeypatch.setattr(embed, "EMBEDDER_FACTORY", lambda model: FakeEmbedder())
    monkeypatch.setattr(embed, "CACHE_DIR", tmp_path / "emb")
    yield


@pytest.fixture()
def program():
    cfg = Config.load(FIX)
    cfg.thresholds.semantic_similarity = 0.6
    cfg.thresholds.responsibility_similarity = 0.3
    prog = build_program(FIX, config=cfg)
    run_pass("entry-points", prog)
    return prog


def names(diags):
    return {d.data.get("qualname") or d.node_id for d in diags}


def test_semantic_passes_are_registered_at_o3():
    by_name = {p.name: p for p in all_passes()}
    assert by_name["semantic-duplicates"].cost == 3
    assert by_name["responsibilities"].cost == 3


def test_o2_does_not_run_semantic_passes(program):
    diags = run_passes(program, opt_level=2)
    assert not {d.pass_name for d in diags} & {"semantic-duplicates", "responsibilities"}


def test_semantic_duplicates_finds_same_job_different_structure(program):
    diags = run_pass("semantic-duplicates", program)
    pairs = [set(d.data["members"]) for d in diags if d.code == "semantic-duplicate"]
    assert {"sum_even", "even_total"} in pairs


def test_semantic_duplicates_skips_structural_duplicates_already_reported(program):
    run_pass("duplicates", program)
    diags = run_pass("semantic-duplicates", program)
    pairs = [set(d.data["members"]) for d in diags if d.code == "semantic-duplicate"]
    assert {"dup_a", "dup_b"} not in pairs


def test_semantic_duplicates_reports_similarity_and_locations(program):
    diags = run_pass("semantic-duplicates", program)
    d = next(x for x in diags if set(x.data["members"]) == {"sum_even", "even_total"})
    assert 0 < d.data["similarity"] <= 1
    assert len(d.data["locations"]) == 2
    assert program.metrics["semantic_duplicates"]["pairs"] >= 1
    assert program.metrics["semantic_duplicates"]["model"] == "fake"


def test_embeddings_are_cached_on_disk(program, tmp_path):
    run_pass("semantic-duplicates", program)
    files = list((tmp_path / "emb").glob("*.npz"))
    assert files, "embedding cache not written"
    calls = []
    orig = FakeEmbedder.embed

    def counting(self, texts):
        calls.append(len(texts))
        return orig(self, texts)

    FakeEmbedder.embed = counting
    try:
        prog2 = build_program(FIX, config=program.config)
        run_pass("entry-points", prog2)
        run_pass("semantic-duplicates", prog2)
    finally:
        FakeEmbedder.embed = orig
    assert sum(calls) == 0  # every text was a cache hit


def test_responsibilities_flags_module_mixing_concerns(program):
    diags = run_pass("responsibilities", program)
    mixed = [d for d in diags if d.code == "mixed-responsibilities"]
    assert "src/app/utils.py" in {d.file for d in mixed}
    d = next(x for x in mixed if x.file == "src/app/utils.py")
    assert len(d.data["clusters"]) >= 2
    assert all(len(c["members"]) >= 3 for c in d.data["clusters"])


def test_missing_backend_skips_gracefully(program, monkeypatch):
    monkeypatch.setattr(embed, "EMBEDDER_FACTORY", lambda model: None)
    diags = run_pass("semantic-duplicates", program)
    assert diags == []
    assert "skipped" in program.metrics["semantic_duplicates"]


def test_backend_and_threshold_selection():
    assert embed.backend_for("minishlab/potion-code-16M-v2") == "model2vec"
    assert embed.backend_for("jinaai/jina-embeddings-v2-base-code") == "fastembed"
    assert embed.default_thresholds("jinaai/jina-embeddings-v2-base-code") == (0.82, 0.40)
    assert embed.default_thresholds("some/unknown-model") == (0.88, 0.70)


def test_config_thresholds_default_to_per_model_values(program):
    assert program.config.embedding_model == embed.DEFAULT_MODEL
    program.config.thresholds.semantic_similarity = None
    run_pass("semantic-duplicates", program)
    assert program.metrics["semantic_duplicates"]["threshold"] == embed.default_thresholds(embed.DEFAULT_MODEL)[0]


def test_similar_symbols_query(program):
    from vibepiler.semantic.similar import similar_symbols

    hits = similar_symbols(program, "sym:src/app/utils.py::sum_even", top=3)
    assert hits[0][0].qualname == "even_total"
    assert 0 < hits[0][1] <= 1


@pytest.mark.skipif(not os.environ.get("VIBEPILER_TEST_EMBED"), reason="set VIBEPILER_TEST_EMBED=1 to run the real model")
def test_real_model_end_to_end(monkeypatch):
    monkeypatch.setattr(embed, "EMBEDDER_FACTORY", embed.default_factory)
    cfg = Config.load(FIX)
    prog = build_program(FIX, config=cfg)
    run_passes(prog, opt_level=3)
    pairs = [set(d.data["members"]) for d in prog.diagnostics if d.code == "semantic-duplicate"]
    assert {"sum_even", "even_total"} in pairs


# --------------------------------------------------------------------------- default model, no truncation, batching
def test_default_model_is_coderankembed_with_its_own_thresholds():
    assert embed.DEFAULT_MODEL == "nomic-ai/CodeRankEmbed"
    assert Config().embedding_model == embed.DEFAULT_MODEL
    assert embed.backend_for(embed.DEFAULT_MODEL) == "fastembed"
    assert embed.default_thresholds(embed.DEFAULT_MODEL) == (0.72, 0.32)
    spec = embed.MODELS[embed.DEFAULT_MODEL]
    assert spec.hf == "nishparadox/CodeRankEmbed-onnx-int8" and spec.model_file.endswith("model_quantized.onnx")
    assert spec.pooling == "cls" and spec.max_length == 8192 and len(spec.revision or "") == 40


def test_symbol_text_is_not_truncated(tmp_path):
    body = "\n".join(f"    total += value_{i} * {i}  # line {i}" for i in range(120))
    (tmp_path / "big.py").write_text(f"def big(total):\n{body}\n    return total\n")
    prog = build_program(tmp_path)
    node = prog.graph.node("sym:big.py::big")
    text = embed.symbol_text(prog, node)
    assert len(text) > 3000
    assert text.rstrip().endswith("return total")


class RecordingEmbedder:
    name = "recording"

    def __init__(self):
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return [[float(len(t)), 1.0] for t in texts]  # unit-normalised later: v[0] / v[1] == len(t)


def test_embed_texts_batches_by_length_within_a_budget_and_keeps_order(tmp_path):
    texts = ["x" * n for n in (5000, 10, 30000, 300, 12, 900, 7, 2500)] + ["y" * 20 + str(i) for i in range(40)]
    rec = RecordingEmbedder()
    vecs = embed.embed_texts(rec, texts, cache_dir=tmp_path)
    assert [round(float(v[0] / v[1])) for v in vecs] == [len(t) for t in texts]  # rows in input order
    flat = [t for call in rec.calls for t in call]
    assert flat == sorted(flat, key=len)  # sorted by length across the whole run
    for call in rec.calls:
        assert len(call) <= embed.BATCH_MAX
        if len(call) > 1:
            assert max(map(len, call)) * len(call) <= embed.BATCH_CHAR_BUDGET
    assert ["x" * 30000] in rec.calls  # the giant function is embedded alone


def test_prepare_model_dir_downloads_once_into_the_cache(tmp_path):
    spec = embed.MODELS["nomic-ai/CodeRankEmbed"]
    calls = []

    def snapshot(spec, dest):
        calls.append(spec.hf)
        (dest / "onnx").mkdir(parents=True, exist_ok=True)
        (dest / spec.model_file).write_bytes(b"int8")

    d, model_file = embed.prepare_model_dir("nomic-ai/CodeRankEmbed", spec, cache_dir=tmp_path, snapshot=snapshot)
    assert model_file == "onnx/model_quantized.onnx" and (d / model_file).is_file() and d.parent == tmp_path
    assert embed.prepare_model_dir("nomic-ai/CodeRankEmbed", spec, cache_dir=tmp_path, snapshot=snapshot) == (d, model_file)
    assert calls == ["nishparadox/CodeRankEmbed-onnx-int8"]
