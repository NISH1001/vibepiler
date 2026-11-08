"""Speed harness: per repo and tool, 1 cold + N warm runs; wall time (perf_counter) and peak RSS (/usr/bin/time -l).

"Cold" = the first run in this harness (no tool caches; knip's cache is off by default; OS file cache not flushed,
which needs root). The median is over the warm runs. Outputs of every vibepiler run are kept for the determinism check;
for the other tools the last run's output is kept for the agreement analysis.

usage: python -I bench.py [--warm 3] [--tools knip,eslint] [repo ...]
"""
from __future__ import annotations

import json
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CORPUS, ENV, RAW, OUT, tool_commands  # noqa: E402

RSS = re.compile(r"(\d+)\s+maximum resident set size")


def run_once(spec: dict) -> dict:
    stdout = open(spec["out"], "w") if spec["out"] else subprocess.DEVNULL
    t0 = time.perf_counter()
    p = subprocess.run(["/usr/bin/time", "-l", *spec["cmd"]], cwd=spec["cwd"], stdout=stdout,
                       stderr=subprocess.PIPE, text=True, env={**ENV, **spec['env']})
    wall = time.perf_counter() - t0
    if spec["out"]:
        stdout.close()
    m = RSS.search(p.stderr)
    return dict(wall=wall, rss_mb=int(m.group(1)) / 2**20 if m else None, exit=p.returncode,
                stderr_tail=p.stderr.strip().splitlines()[-25:-18] if p.returncode not in (0,) else [])


def main() -> None:
    args = sys.argv[1:]
    warm = 3
    if args[:1] == ["--warm"]:
        warm, args = int(args[1]), args[2:]
    only = None
    if args[:1] == ["--tools"]:
        only, args = set(args[1].split(",")), args[2:]
    repos = args or list(CORPUS)
    path = OUT / "bench.json"
    results = json.loads(path.read_text()) if path.exists() else {}
    for repo in repos:
        outdir = RAW / repo
        outdir.mkdir(parents=True, exist_ok=True)
        results.setdefault(repo, {})
        for tool, spec in tool_commands(repo, outdir).items():
            if only and tool not in only:
                continue
            if spec["skip"]:
                results[repo][tool] = dict(skipped=spec["skip"])
                print(f"{repo:14} {tool:18} skipped: {spec['skip']}", flush=True)
                continue
            runs = []
            for i in range(warm + 1):
                runs.append(run_once(spec))
                if tool.startswith("vibepiler"):  # keep each run's IR for determinism
                    src = outdir / tool / "ir.json"
                    if src.exists():
                        shutil.copy(src, outdir / f"{tool}.run{i}.ir.json")
            w = [r["wall"] for r in runs[1:]]
            rss = [r["rss_mb"] for r in runs if r["rss_mb"] is not None]
            results[repo][tool] = dict(cold=runs[0]["wall"], warm=w, median=statistics.median(w),
                                       peak_rss_mb=max(rss) if rss else None, exits=[r["exit"] for r in runs],
                                       stderr_tail=runs[-1]["stderr_tail"])
            print(f"{repo:14} {tool:18} median {statistics.median(w):7.2f}s  cold {runs[0]['wall']:7.2f}s  "
                  f"rss {max(rss) if rss else float('nan'):7.0f} MB  exit {runs[-1]['exit']}", flush=True)
            path.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
