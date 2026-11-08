"""Verbose mode: what the slow passes are doing while they run, with memory, through loguru (`vibepiler` silent until enabled)."""

import os
import subprocess
import sys

import pytest
from loguru import logger
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


@pytest.fixture()
def logs():
    """Every vibepiler record while the test runs: (message, extra)."""
    seen = []
    logger.enable("vibepiler")
    sink = logger.add(lambda m: seen.append((m.record["message"], m.record["extra"])), level="DEBUG", filter="vibepiler")
    yield seen
    logger.remove(sink)
    logger.disable("vibepiler")


def test_vibepiler_is_silent_until_enabled():
    seen = []
    sink = logger.add(lambda m: seen.append(m), level="DEBUG")
    try:
        with timed("quiet step"):
            pass
    finally:
        logger.remove(sink)
    assert seen == []  # a library: `import vibepiler` prints nothing unless asked


def test_vibepiler_debug_prints_every_step_to_stderr():
    code = "from vibepiler.progress import timed\nwith timed('loud step'):\n    pass\n"
    out = subprocess.run([sys.executable, "-c", code], env={**os.environ, "VIBEPILER_DEBUG": "1"}, capture_output=True, text=True, check=True)
    assert "loud step" in out.stderr and "GB peak" in out.stderr


def test_memory_reports_a_peak_in_gigabytes():
    now, peak = memory()
    assert peak > 0 and (now is None or 0 < now <= peak * 1.5)


def test_timed_logs_the_step_with_seconds_and_memory(logs):
    with timed("similarity"):
        sum(range(1000))
    (msg,) = [m for m, _ in logs if m.startswith("similarity")]
    assert " s" in msg and "GB peak" in msg


def test_embedding_logs_the_cache_every_batch_and_progress(logs):
    texts = [f"def f{i}(x):\n    return x + {i}\n" * (1 + i % 5) for i in range(70)]
    embed.embed_texts(FakeEmbedder(), texts)
    msgs = [m for m, _ in logs]
    assert any(m.startswith("cache ") and "0 reused, 70 to embed" in m for m in msgs)
    batches = [m for m in msgs if m.startswith("batch ")]
    assert len(batches) >= 3 and "longest" in batches[0] and "GB peak" in batches[0]
    progress = [x["progress"] for _, x in logs if "progress" in x]
    assert progress[-1]["done"] == progress[-1]["total"] == 70
    logs.clear()
    embed.embed_texts(FakeEmbedder(), texts)
    assert any("70 reused, 0 to embed" in m for m, _ in logs)


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
    assert made.max_tokens == 4096  # the default
    nodes, _, _ = embed.symbol_vectors(prog)
    huge = next(n for n in nodes if n.name == "huge")
    assert len(embed.symbol_text(prog, huge, max_chars=embed.chars_for(4096))) == embed.chars_for(4096)


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
    assert Config.load(tmp_path).embedding_max_tokens == 1024 and Config().embedding_max_tokens == 4096


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


# ----- the cache survives an interrupted run --------------------------------------------------------------------------
class Dies(FakeEmbedder):
    """Embeds `allowed` batches, then fails like a killed run."""

    def __init__(self, allowed):
        self.allowed, self.calls = allowed, 0

    def embed(self, texts):
        self.calls += 1
        if self.calls > self.allowed:
            raise KeyboardInterrupt
        return super().embed(texts)


def test_an_interrupted_run_keeps_what_it_embedded(tmp_path):
    texts = [f"def f{i}(x):\n    return x * {i}\n" for i in range(100)]
    with pytest.raises(KeyboardInterrupt):
        embed.embed_texts(Dies(allowed=2), texts, cache_dir=tmp_path)
    counting = Dies(allowed=10**6)
    embed.embed_texts(counting, texts, cache_dir=tmp_path)
    assert counting.calls == len(embed.batches(texts)) - 2  # the two batches done before the stop are not redone
    assert not list(tmp_path.glob("*.tmp*"))  # written whole, then moved into place


def test_long_runs_save_the_cache_as_they_go(tmp_path, monkeypatch, logs):
    monkeypatch.setattr(embed, "CHECKPOINT_SECONDS", 0.0)
    texts = [f"def f{i}(x):\n    return x * {i}\n" for i in range(100)]
    embed.embed_texts(FakeEmbedder(), texts, cache_dir=tmp_path)
    assert sum(m.startswith("cache saved") for m, _ in logs) >= len(embed.batches(texts))


# ----- review of feature/semantic-scale --------------------------------------------------------------------------------
class _Tok:
    def __init__(self, limit):
        self.truncation = {"max_length": limit}

    def enable_truncation(self, max_length):
        self.truncation = {"max_length": max_length}


def _fastembed_without_spec(limit):
    """A FastEmbedEmbedder for a model outside MODELS, without loading anything."""
    from types import SimpleNamespace

    e = embed.FastEmbedEmbedder.__new__(embed.FastEmbedEmbedder)
    e.name, e._spec, e.max_tokens = "some/custom-model", None, None
    e._model = SimpleNamespace(model=SimpleNamespace(tokenizer=_Tok(limit)))
    return e


def test_a_custom_model_is_never_raised_past_its_own_context():
    e = _fastembed_without_spec(512)
    e.truncate(4096)
    assert e._model.model.tokenizer.truncation["max_length"] == 512 and e.max_tokens == 512
    e.truncate(256)
    assert e.max_tokens == 256


def test_a_damaged_cache_is_rebuilt_not_fatal(tmp_path):
    e = CappableEmbedder()
    e.truncate(4096)
    embed.embed_texts(e, ["def f():\n    return 1\n"], cache_dir=tmp_path)
    (cache,) = tmp_path.glob("*.npz")
    cache.write_bytes(cache.read_bytes()[: cache.stat().st_size // 2])
    with pytest.warns(RuntimeWarning, match="unreadable"):
        out = embed.embed_texts(e, ["def f():\n    return 1\n"], cache_dir=tmp_path)
    assert out.shape[0] == 1


def test_checkpoints_hold_only_this_runs_vectors_and_a_killed_runs_are_merged(tmp_path, monkeypatch):
    import numpy as np

    e = CappableEmbedder()
    e.truncate(4096)
    old = [f"def old{i}():\n    return {i}\n" for i in range(200)]
    embed.embed_texts(e, old, cache_dir=tmp_path)  # a big shared cache already on disk
    monkeypatch.setattr(embed, "CHECKPOINT_SECONDS", 0.0)
    new = [f"def new{i}(x):\n    return x * {i}\n" for i in range(100)]
    dies = Dies(allowed=2)
    dies.max_tokens = 4096
    monkeypatch.setattr(embed, "_finish", lambda *a, **k: None)  # as if killed before the final merge
    with pytest.raises(KeyboardInterrupt):
        embed.embed_texts(dies, new, cache_dir=tmp_path)
    (part,) = tmp_path.glob("*.part-*.npz")
    assert len(np.load(part)["hashes"]) == 2 * 32  # only what this run embedded, not the 200 already cached
    monkeypatch.undo()
    counting = Dies(allowed=10**6)
    counting.max_tokens = 4096
    embed.embed_texts(counting, new, cache_dir=tmp_path)
    assert counting.calls == len(embed.batches(new)) - 2 and not list(tmp_path.glob("*.part-*"))


def test_vectors_cached_under_another_cap_are_reused_where_the_cap_cannot_matter(tmp_path):
    import numpy as np

    e = CappableEmbedder()
    texts = ["def short():\n    return 1\n", "def long():\n" + "    x = 1\n" * 400]
    embed.embed_texts(e, texts, cache_dir=tmp_path)  # uncapped: the cache from before the cap
    assert [p.name for p in tmp_path.glob("*.npz")] == ["fake.npz"]
    seen = []

    class Recording(FakeEmbedder):
        max_tokens = 1024

        def embed(self, batch):
            seen.extend(batch)
            return super().embed(batch)

    embed.embed_texts(Recording(), texts, cache_dir=tmp_path)
    assert seen == [texts[1]]  # only the long one: the short one fits under any cap, so its old vector stands
    assert np.load(tmp_path / "fake__1024t.npz")["hashes"].shape == (2,)


def test_batches_bound_the_quadratic_cost_of_long_functions():
    texts = ["x" * 100] * 64 + ["y" * 5000] * 4
    plan = embed.batches(texts)
    long_batches = [b for b in plan if len(texts[b[0]]) == 5000]
    assert all(len(b) * max(len(texts[i]) for i in b) ** 2 <= max(embed.BATCH_SQUARE_BUDGET, max(len(texts[i]) for i in b) ** 2) for b in plan)
    assert len(long_batches) == 4  # one long function a batch: its attention dominates
    assert sorted(i for b in plan for i in b) == list(range(len(texts)))


def test_the_cli_leaves_vibepiler_silent_again_and_verbose_beats_quiet(tmp_path):
    res = CliRunner().invoke(app, ["compile", FIX, "-O3", "-v", "-q", "--emit", "json", "-o", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert "batch 1/" in res.output
    test_vibepiler_is_silent_until_enabled()  # the CLI's sink is gone and the library is quiet again


# ----- the cache off, the files written, the whole pipeline under -v ---------------------------------------------------


def test_no_cache_neither_reads_nor_writes_the_embedding_cache(tmp_path):
    texts = [f"def f{i}(x):\n    return x - {i}\n" for i in range(40)]
    embed.embed_texts(FakeEmbedder(), texts, cache_dir=tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    counting = Dies(allowed=10**6)
    fresh = embed.embed_texts(counting, [*texts, "def g():\n    return 0\n"], cache_dir=tmp_path, use_cache=False)
    assert counting.calls == len(embed.batches([*texts, "x"]))  # every text embedded again, none read back
    assert fresh.shape[0] == 41
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before  # and nothing written


def test_the_cache_switch_reaches_the_config_from_the_cli_and_python(tmp_path):
    from vibepiler.compiler import Compiler
    from vibepiler.config import Config

    assert Config().embedding_cache is True
    assert Compiler(FIX, use_cache=False).config.embedding_cache is False
    (tmp_path / "vibepiler.toml").write_text("embedding_cache = false\n")
    assert Config.load(tmp_path).embedding_cache is False
    res = CliRunner().invoke(app, ["compile", FIX, "-O3", "--no-cache", "-q", "--emit", "json", "-o", str(tmp_path / "out")])
    assert res.exit_code == 0, res.output
    assert not list((tmp_path / "emb").glob("*.npz"))  # the fixture points the cache here


def test_the_cli_ends_with_the_files_it_wrote(tmp_path):
    out = tmp_path / "out"
    res = CliRunner().invoke(app, ["compile", FIX, "-q", "--emit", "html,json,md", "-o", str(out)])
    assert res.exit_code == 0, res.output
    tail = res.output.strip().splitlines()[-5:]
    assert any(str(out.resolve() / "report.html") in line for line in tail), tail
    assert any("ir.json" in line for line in tail) and any("report.md" in line for line in tail)


def test_verbose_shows_the_whole_pipeline(tmp_path):
    res = CliRunner().invoke(app, ["compile", FIX, "-v", "--emit", "html", "-o", str(tmp_path)])
    assert res.exit_code == 0, res.output
    for line in ("config:", "discover:", "parse:", "link:", "emit report.html"):
        assert line in res.output, line
    assert "GB peak" in next(x for x in res.output.splitlines() if "pass duplicates" in x)  # every pass with its memory


def test_discovery_says_which_files_it_skipped_as_too_large(tmp_path, monkeypatch, logs):
    from vibepiler.frontend import loader

    monkeypatch.setattr(loader, "MAX_FILE_BYTES", 100)
    (tmp_path / "small.py").write_text("x = 1\n")
    (tmp_path / "huge.py").write_text("y = 2\n" * 100)
    files = loader.discover(tmp_path)
    assert [f.rel for f in files] == ["small.py"]
    assert any(m.startswith("discover:") and "huge.py" in m for m, _ in logs)


class _Msg:
    def __init__(self, message, **extra):
        self.record = {"message": message, "extra": extra}


@pytest.mark.parametrize("verbose", [False, True])
def test_the_pass_log_draws_a_progress_bar_with_an_eta(verbose):
    from rich.progress import Progress

    from vibepiler.backend.terminal import PassLog

    passlog = PassLog(verbose)
    passlog(_Msg("embedding 32 / 100", progress={"what": "embedding", "done": 32, "total": 100, "batch": 1, "batches": 4, "rate": 40.0}))
    passlog(_Msg("embedding 64 / 100", progress={"what": "embedding", "done": 64, "total": 100, "batch": 2, "batches": 4, "rate": 40.0}))
    assert isinstance(passlog.bar, Progress)
    (task,) = passlog.bar.tasks
    assert (task.completed, task.total) == (64, 100) and "embedding" in task.description
    passlog.finish()
    assert passlog.bar is None  # gone with its pass


def test_verbose_from_python_prints_every_step_and_is_quiet_again_after():
    import vibepiler
    from vibepiler.compiler import Compiler

    seen = []
    sink = logger.add(lambda m: seen.append(m.record["message"]), level="DEBUG", filter="vibepiler")  # any sink: stderr by default
    try:
        Compiler(FIX, verbose=True).run(emit=())
        assert any(m.startswith("config:") for m in seen) and any(m.startswith("discover:") for m in seen) and any(m.startswith("link:") for m in seen)
        seen.clear()
        vibepiler.compile(FIX, emit=())
        assert seen == []  # off unless asked, and the last run left nothing on
        vibepiler.compile(FIX, verbose=True, emit=())
        assert any(m.startswith("discover:") for m in seen)
    finally:
        logger.remove(sink)
    test_vibepiler_is_silent_until_enabled()


def test_languages_and_model_reach_the_compiler_from_python_and_the_cli(tmp_path):
    import vibepiler
    from vibepiler.compiler import Compiler

    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    (tmp_path / "b.js").write_text("function g() { return 2 }\n")
    (tmp_path / "c.ts").write_text("export function h(): number { return 3 }\n")
    files = {f.rel for f in Compiler(tmp_path, languages=["python", "typescript"]).run(emit=()).program.files}  # --lang python,typescript
    assert files == {"a.py", "c.ts"}
    with pytest.raises(ValueError, match="unknown language"):
        Compiler(tmp_path, languages=["cobol"])
    assert Compiler(tmp_path, model="minishlab/potion-code-16M-v2").config.embedding_model == "minishlab/potion-code-16M-v2"
    assert vibepiler.compile(tmp_path, model="minishlab/potion-code-16M-v2", emit=()).program.config.embedding_model.startswith("minishlab")
    res = CliRunner().invoke(app, ["compile", str(tmp_path), "-q", "-v", "--model", "minishlab/potion-code-16M-v2", "--emit", "json", "-o", str(tmp_path / "out")])
    assert res.exit_code == 0, res.output
    assert "model minishlab/potion-code-16M-v2" in res.output
    for cmd in ("size", "similar"):
        assert "--model" in CliRunner().invoke(app, [cmd, "--help"]).output
