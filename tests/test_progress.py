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


# ----- the token cap: one long function must not take the machine's memory ----------------------------------------
class CappableEmbedder(FakeEmbedder):
    max_tokens = None

    def truncate(self, tokens):
        self.max_tokens = tokens


def test_the_embedder_is_capped_and_long_text_is_cut_before_it(monkeypatch, tmp_path):
    from vibepiler.compiler import Compiler

    made = CappableEmbedder()
    monkeypatch.setattr(embed, "EMBEDDER_FACTORY", lambda model: made)
    body = "".join(f"    x{i} = {i}\n" for i in range(5000))
    prog = Compiler(f"def huge():\n{body}    return 1\n\n\ndef small():\n    return 2\n", language="python").run(opt_level=3, emit=()).program
    assert made.max_tokens == 2048
    nodes, _, _ = embed.symbol_vectors(prog)
    huge = next(n for n in nodes if n.name == "huge")
    assert len(embed.symbol_text(prog, huge, max_chars=embed.chars_for(2048))) == embed.chars_for(2048)


def test_the_cap_is_part_of_the_cache_key(tmp_path):
    e = CappableEmbedder()
    e.truncate(2048)
    embed.embed_texts(e, ["def f():\n    return 1\n"], cache_dir=tmp_path)
    e.truncate(512)
    embed.embed_texts(e, ["def f():\n    return 1\n"], cache_dir=tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["fake__2048t.npz", "fake__512t.npz"]


def test_the_cap_is_a_setting(tmp_path):
    from vibepiler.config import Config

    (tmp_path / "pyproject.toml").write_text("[tool.vibepiler]\nembedding_max_tokens = 1024\n")
    assert Config.load(tmp_path).embedding_max_tokens == 1024 and Config().embedding_max_tokens == 2048


def test_zero_lifts_the_cap_to_the_models_own_context(monkeypatch):
    from vibepiler.compiler import Compiler
    from vibepiler.config import Config

    made = CappableEmbedder()
    monkeypatch.setattr(embed, "EMBEDDER_FACTORY", lambda model: made)
    body = "".join(f"    x{i} = {i}\n" for i in range(5000))
    cfg = Config(embedding_max_tokens=0)
    prog = Compiler(f"def huge():\n{body}    return 1\n\n\ndef small():\n    return 2\n", cfg, language="python").run(opt_level=3, emit=()).program
    assert made.max_tokens is None  # not cut by us: the model keeps its own context
    nodes, _, _ = embed.symbol_vectors(prog)
    assert embed.text_cap(prog.config) is None


def test_max_tokens_is_a_command_line_option(monkeypatch, tmp_path):
    made = CappableEmbedder()
    monkeypatch.setattr(embed, "EMBEDDER_FACTORY", lambda model: made)
    res = CliRunner().invoke(app, ["compile", FIX, "-O3", "--max-tokens", "512", "--emit", "json", "-o", str(tmp_path), "-q"])
    assert res.exit_code == 0, res.output
    assert made.max_tokens == 512


def test_max_tokens_is_a_compiler_keyword_over_the_projects_config(monkeypatch, tmp_path):
    import vibepiler
    from vibepiler.compiler import Compiler

    made = CappableEmbedder()
    monkeypatch.setattr(embed, "EMBEDDER_FACTORY", lambda model: made)
    (tmp_path / "pyproject.toml").write_text("[tool.vibepiler]\nembedding_max_tokens = 1024\nexclude = ['skip/']\n")
    (tmp_path / "m.py").write_text("def f(a):\n    return a + 1\n\n\ndef g(b):\n    return b * 2\n")
    c = Compiler(tmp_path, max_tokens=512)
    assert c.config.embedding_max_tokens == 512 and "skip/" in c.config.exclude  # only the one setting changes
    c.run(opt_level=3, emit=())
    assert made.max_tokens == 512
    assert Compiler(tmp_path).config.embedding_max_tokens == 1024
    assert vibepiler.compile(tmp_path, max_tokens=0, opt_level=1, emit=()).program.config.embedding_max_tokens == 0
