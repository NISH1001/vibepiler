"""Merge every raw output into docs/eval/semantic/results.json."""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from pathlib import Path

OUT = Path(sys.argv[1])
DOCS = Path(__file__).resolve().parents[3] / "docs" / "eval" / "semantic" / "results.json"


def jl(p: Path) -> list:
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip().startswith("{")] if p.exists() else []


def j(p: Path):
    return json.loads(p.read_text()) if p.exists() else None


def sh(c: str) -> str:
    return subprocess.run(c, shell=True, capture_output=True, text=True).stdout.strip()


res = {
    "machine": {"cpu": sh("sysctl -n machdep.cpu.brand_string"), "cores": sh("sysctl -n hw.ncpu"), "perf_cores": sh("sysctl -n hw.perflevel0.physicalcpu"),
                "ram_gb": int(sh("sysctl -n hw.memsize") or 0) // 2**30, "os": platform.platform(), "device": "CPU only"},
    "quality": j(OUT / "quality.json"),
    "structural": {"jscpd_min_tokens_50": j(OUT / "structural_mt50.json"), "jscpd_min_tokens_20": j(OUT / "structural_mt20.json")},
    "embed_runs_codenet": {p.stem: j(p) for p in sorted(OUT.glob("emb_*.json"))},
    "bench_repos": jl(OUT / "bench.jsonl"),
    "cli_cache": jl(OUT / "cli_cache.jsonl"),
    "footprint": j(OUT / "footprint.json"),
    "wild_pairs": j(OUT / "wild_pairs.json"),
    "wild_labels": j(Path(__file__).resolve().parent / "wild_labels.json"),  # hand labels, kept next to the scripts
    "wild_int8_vs_fp32": j(OUT / "wild_int8_fp32.json"),
    "caller_callee_pairs": j(OUT / "caller_pairs.json"),
    "closure_bug": jl(OUT / "closure_check.jsonl"),
    "notes": ["emb_cre_fp32 CodeNet timing (0.4/s over 4.8 h) is invalid: the machine most likely slept during that run; vectors are unaffected. Use bench_repos for fp32 speed.",
              "load_s in embed_runs_codenet for the first run of each model includes the model download."],
}
DOCS.write_text(json.dumps(res, indent=1))
print("wrote", DOCS)
