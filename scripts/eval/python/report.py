"""Fold out/timings.json, out/analysis.json and the hand labels into results.json and print the markdown tables.

Usage: python3 -I scripts/eval/python/report.py > out/tables.md
"""

import json
import platform
import subprocess
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent  # scripts/eval/python
ROOT = HERE.parents[2]  # the vibepiler checkout
E = Path(os.environ.get("VIBEPILER_EVAL_WORK", ROOT / ".eval" / "python"))  # clones, tools, raw outputs (gitignored)
OUT = E / "out"
REPOS = ["requests", "flask", "httpx", "vibepiler", "gpython", "rich", "black", "django"]
LEAN = ["vulture60", "ruff_unused", "ruff_exc", "ruff_graph", "deptry", "jscpd", "radon_cc", "complexipy"]
HEAVY = ["vulture60", "ruff_unused", "ruff_exc", "ruff_graph", "deptry", "pylint_dup", "pylint_exc", "radon_cc", "complexipy"]
TOOLS = ["vibepiler_O1", "vibepiler_O2", "vulture60", "vulture100", "deadcode", "ruff_unused", "ruff_exc", "ruff_graph", "deptry",
         "pylint_dup", "pylint_exc", "jscpd", "radon_cc", "complexipy"]


def loc(repo: str) -> dict:
    files, lines = 0, 0
    for p in (E / "repos" / repo).rglob("*.py"):
        if ".git" in p.parts:
            continue
        files += 1
        lines += sum(1 for l in p.read_text(errors="replace").splitlines() if l.strip())
    return dict(py_files=files, nonblank_py_lines=lines)


def sh(cmd: str) -> str:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()


def main() -> None:
    timings = json.loads((OUT / "timings.json").read_text())
    analysis = json.loads((OUT / "analysis.json").read_text())
    labels = json.loads((HERE / "labels.json").read_text()) if (HERE / "labels.json").exists() else {}
    machine = dict(cpu=sh("sysctl -n machdep.cpu.brand_string"), cores=int(sh("sysctl -n hw.ncpu")),
                   ram_gb=round(int(sh("sysctl -n hw.memsize")) / 2**30), os=platform.platform())
    sizes = {r: loc(r) for r in REPOS}
    shas = {r: sh(f"git -C {E / 'repos' / r} rev-parse HEAD") for r in REPOS}
    sums = {}
    for r in REPOS:
        t = timings.get(r, {})
        sums[r] = dict(
            lean=round(sum(t[k]["median_wall"] for k in LEAN if k in t), 2),
            with_pylint=round(sum(t[k]["median_wall"] for k in HEAVY if k in t), 2),
            vibepiler_O2=round(t["vibepiler_O2"]["median_wall"], 2) if "vibepiler_O2" in t else None,
        )
    res = dict(machine=machine, corpus={r: dict(sha=shas[r], **sizes[r]) for r in REPOS}, timings=timings, toolchain_sum=sums,
               analysis=analysis, labels=labels, toolchain_definition=dict(lean=LEAN, with_pylint=HEAVY))
    (ROOT / "docs" / "eval" / "python" / "results.json").write_text(json.dumps(res, indent=1))

    p = print
    p(f"Machine: {machine}\n")
    p("| repo | sha | .py files | non-blank lines |\n|---|---|---|---|")
    for r in REPOS:
        p(f"| {r} | `{shas[r][:10]}` | {sizes[r]['py_files']} | {sizes[r]['nonblank_py_lines']:,} |")
    p("\n### Wall time, warm median (s)\n")
    p("| tool | " + " | ".join(REPOS) + " |\n|---|" + "---|" * len(REPOS))
    for tool in TOOLS:
        cells = []
        for r in REPOS:
            x = timings.get(r, {}).get(tool)
            cells.append(f"{x['median_wall']:.2f}" + ("*" if len(x["warm"]) == 1 else "") if x else "n/a")
        p(f"| {tool} | " + " | ".join(cells) + " |")
    p("\n### Peak RSS (MB, max over runs)\n")
    p("| tool | " + " | ".join(REPOS) + " |\n|---|" + "---|" * len(REPOS))
    for tool in TOOLS:
        p(f"| {tool} | " + " | ".join(f"{timings[r][tool]['max_rss'] / 2**20:.0f}" if tool in timings.get(r, {}) else "n/a" for r in REPOS) + " |")
    p("\n### Cold (first) run wall (s)\n")
    p("| tool | " + " | ".join(REPOS) + " |\n|---|" + "---|" * len(REPOS))
    for tool in TOOLS:
        p(f"| {tool} | " + " | ".join(f"{timings[r][tool]['cold']['wall']:.2f}" if tool in timings.get(r, {}) else "n/a" for r in REPOS) + " |")
    p("\n### Time vs size\n")
    p("| repo | non-blank lines | vibepiler -O1 s | -O2 s | -O2 kLOC/s | toolchain sum (lean) s | toolchain sum (pylint) s | -O2 / lean |\n|---|---|---|---|---|---|---|---|")
    for r in REPOS:
        t = timings[r]
        o2 = t["vibepiler_O2"]["median_wall"]
        p(f"| {r} | {sizes[r]['nonblank_py_lines']:,} | {t['vibepiler_O1']['median_wall']:.2f} | {o2:.2f} | "
          f"{sizes[r]['nonblank_py_lines'] / 1000 / o2:.1f} | {sums[r]['lean']:.2f} | {sums[r]['with_pylint']:.2f} | {o2 / sums[r]['lean']:.2f}x |")


if __name__ == "__main__":
    main()
