"""Time vibepiler and the comparison tools on every corpus repo.

Each (repo, tool) is run once cold (first invocation, tool caches off) and then
3 warm runs (1 if the cold run took longer than SLOW seconds). Wall time is
measured around the subprocess; peak RSS comes from `/usr/bin/time -l`.
Outputs of every run are kept under out/<repo>/<tool>/run<N>/ so the analysis
step can read them. Results go to out/timings.json.

Usage: python3 -I scripts/eval/python/bench.py [repo ...] [--tools a,b]
"""

import json
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent  # scripts/eval/python
ROOT = HERE.parents[2]  # the vibepiler checkout
E = Path(os.environ.get("VIBEPILER_EVAL_WORK", ROOT / ".eval" / "python"))  # clones, tools, raw outputs (gitignored)
REPOS = ["requests", "flask", "httpx", "vibepiler", "gpython", "rich", "black", "django"]
T = E / "tools" / "bin"
VP = str(ROOT / ".venv" / "bin" / "vibepiler")  # this checkout's vibepiler
JSCPD = E / "node" / "node_modules" / ".bin" / "jscpd"
SLOW = 90.0
WARM = 3


def tools(R: Path, O: Path) -> dict[str, dict]:
    """Command, cwd and where stdout goes, per tool."""
    return {
        "vibepiler_O1": dict(cmd=[VP, "compile", R, "-O1", "-q", "--lang", "python", "--emit", "json", "-o", O]),
        "vibepiler_O2": dict(cmd=[VP, "compile", R, "-O2", "-q", "--lang", "python", "--emit", "json", "-o", O]),
        "vulture60": dict(cmd=[T / "vulture", R], stdout="vulture.txt"),
        "vulture100": dict(cmd=[T / "vulture", "--min-confidence", "100", R], stdout="vulture.txt"),
        "deadcode": dict(cmd=[T / "deadcode", "--no-color", R], stdout="deadcode.txt"),
        "ruff_unused": dict(cmd=[T / "ruff", "check", "--isolated", "--no-cache", "--exit-zero",
                                 "--select", "F401,F841", "--output-format", "json", R], stdout="ruff.json"),
        "ruff_exc": dict(cmd=[T / "ruff", "check", "--isolated", "--no-cache", "--exit-zero",
                              "--select", "BLE001,S110,SIM105", "--output-format", "json", R], stdout="ruff.json"),
        "ruff_graph": dict(cmd=[T / "ruff", "analyze", "graph", "--isolated", R], stdout="graph.json"),
        # deptry looks for the manifest in its working directory; it only reads files.
        "ruff_graph_notc": dict(cmd=[T / "ruff", "analyze", "graph", "--isolated", "--no-type-checking-imports", R], stdout="graph.json"),
        "deptry": dict(cmd=[T / "deptry", ".", "--json-output", O / "deptry.json"], cwd=R),
        "pylint_dup": dict(cmd=[T / "pylint", "--disable=all", "--enable=R0801", "--recursive=y", "--persistent=n",
                                "--output-format=json", "-j1", R], stdout="pylint.json"),
        "pylint_exc": dict(cmd=[T / "pylint", "--disable=all", "--enable=W0702,W0718", "--recursive=y",
                                "--persistent=n", "--output-format=json", "-j1", R], stdout="pylint.json"),
        "jscpd": dict(cmd=[JSCPD, "--format", "python", "--reporters", "json", "--output", O, "--silent", R]),
        "radon_cc": dict(cmd=[T / "radon", "cc", "-s", "-j", R], stdout="radon.json"),
        "complexipy": dict(cmd=[T / "complexipy", R, "--output-format", "json", "--output", O / "complexipy.json",
                                "-q", "--cache-dir", O / "cxcache"]),
    }


def run_once(spec: dict, O: Path) -> dict:
    O.mkdir(parents=True, exist_ok=True)
    tfile = O / "time.txt"
    cmd = ["/usr/bin/time", "-l", "-o", str(tfile)] + [str(c) for c in spec["cmd"]]
    out = open(O / spec["stdout"], "w") if "stdout" in spec else subprocess.DEVNULL
    env = dict(os.environ, NO_COLOR="1", PYTHONHASHSEED="random")
    t0 = time.perf_counter()
    p = subprocess.run(cmd, cwd=spec.get("cwd", E / "out"), stdout=out, stderr=open(O / "stderr.txt", "w"), env=env)
    wall = time.perf_counter() - t0
    if out is not subprocess.DEVNULL:
        out.close()
    m = re.search(r"(\d+)\s+maximum resident set size", tfile.read_text())
    return dict(wall=wall, rss=int(m.group(1)) if m else None, exit=p.returncode)


def main() -> None:
    args = sys.argv[1:]
    only = None
    if "--tools" in args:
        i = args.index("--tools")
        only = args[i + 1].split(",")
        del args[i:i + 2]
    repos = args or REPOS
    tfile = E / "out" / "timings.json"
    results = json.loads(tfile.read_text()) if tfile.exists() else {}
    for repo in repos:
        R = E / "repos" / repo
        for name in tools(R, Path("/x")):
            if only and name not in only:
                continue
            base = E / "out" / repo / name
            runs = []
            cold = run_once(tools(R, base / "run0")[name], base / "run0")
            n = 1 if cold["wall"] > SLOW else WARM
            for i in range(1, n + 1):
                runs.append(run_once(tools(R, base / f"run{i}")[name], base / f"run{i}"))
            rec = dict(cold=cold, warm=runs,
                       median_wall=statistics.median(r["wall"] for r in runs),
                       max_rss=max((r["rss"] or 0) for r in [cold] + runs),
                       exits=sorted({r["exit"] for r in [cold] + runs}))
            results.setdefault(repo, {})[name] = rec
            print(f"{repo:10} {name:14} cold {cold['wall']:7.2f}s  warm-median {rec['median_wall']:7.2f}s  "
                  f"rss {rec['max_rss'] / 2**20:7.0f} MB  exit {rec['exits']}", flush=True)
            tfile.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
