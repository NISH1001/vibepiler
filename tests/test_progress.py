"""Verbose mode: what the slow passes are doing while they run, with memory, through the `vibepiler` logger."""

import logging

import pytest
from typer.testing import CliRunner

from test_semantic import FakeEmbedder
from vibepiler.cli import app
from vibepiler.progress import memory, timed
from vibepiler.semantic import embed

FIX = "tests/fixtures/pyproj"


@pytest.fixture(autouse=True)
def fake_embedder(monkeypatch, tmp_path):
    monkeypatch.setattr(embed, "EMBEDDER_FACTORY", lambda model: FakeEmbedder())
    monkeypatch.setattr(embed, "CACHE_DIR", tmp_path / "emb")


def test_memory_reports_a_peak_in_gigabytes():
    now, peak = memory()
    assert peak > 0 and (now is None or 0 < now <= peak * 1.5)


def test_timed_logs_the_step_with_seconds_and_memory(caplog):
    with caplog.at_level(logging.DEBUG, logger="vibepiler"), timed("similarity"):
        sum(range(1000))
    (rec,) = [r for r in caplog.records if r.getMessage().startswith("similarity")]
    assert " s" in rec.getMessage() and "GB peak" in rec.getMessage()


def test_embedding_logs_the_cache_every_batch_and_progress(caplog):
    texts = [f"def f{i}(x):\n    return x + {i}\n" * (1 + i % 5) for i in range(70)]
    with caplog.at_level(logging.DEBUG, logger="vibepiler"):
        embed.embed_texts(FakeEmbedder(), texts)
    msgs = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("cache ") and "0 reused, 70 to embed" in m for m in msgs)
    batches = [m for m in msgs if m.startswith("batch ")]
    assert len(batches) >= 3 and "longest" in batches[0] and "GB peak" in batches[0]
    progress = [r.progress for r in caplog.records if hasattr(r, "progress")]
    assert progress[-1]["done"] == progress[-1]["total"] == 70
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="vibepiler"):
        embed.embed_texts(FakeEmbedder(), texts)
    assert any("70 reused, 0 to embed" in r.getMessage() for r in caplog.records)


def test_verbose_cli_prints_what_the_semantic_passes_do(tmp_path):
    res = CliRunner().invoke(app, ["compile", FIX, "-O3", "-v", "--emit", "json", "-o", str(tmp_path / "a"), "-W", "error"])
    assert res.exit_code == 0, res.output
    assert "cache " in res.output and "batch 1/" in res.output and "GB peak" in res.output
    quiet = CliRunner().invoke(app, ["compile", FIX, "-O3", "--emit", "json", "-o", str(tmp_path / "b"), "-W", "error"])
    assert "batch 1/" not in quiet.output
