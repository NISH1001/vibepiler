# /// script
# requires-python = ">=3.12"
# dependencies = ["matplotlib>=3.8"]
# ///
"""The evaluation figure: vibepiler against the standard Python and JS/TS tools, speed and hand-checked precision.

Run:  uv run scripts/eval_figure.py
Reads docs/eval/{python,javascript}/results.json and writes docs/eval/img/eval.{svg,pdf,png}.
Same palette and conventions as bootstrap_figure.py: validated categorical slots, hatching so colour is never the only cue.
"""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "docs" / "eval"

BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"  # categorical slots 1, 2, 3 of the reference palette
INK, INK2, GRID = "#0b0b0b", "#52514e", "#dcdbd5"
VERDICTS = ("TP", "FP", "unclear")


def python_speed() -> list[tuple[str, int, float, float, float]]:
    """(repo, lines, vibepiler -O2, vulture, lean toolchain sum) per repo, smallest first."""
    data = json.loads((EVAL / "python" / "results.json").read_text())
    rows = []
    for repo, sums in data["toolchain_sum"].items():
        vulture = data["timings"][repo]["vulture60"]["median_wall"]
        rows.append((repo, data["corpus"][repo]["nonblank_py_lines"], sums["vibepiler_O2"], vulture, sums["lean"]))
    return sorted(rows, key=lambda r: r[1])


def js_speed() -> list[tuple[str, int, float, float, float]]:
    """(repo, lines, vibepiler -O2, knip, sum of the other tools) per repo, smallest first."""
    data = json.loads((EVAL / "javascript" / "results.json").read_text())
    rows = []
    for repo, info in data["repos"].items():
        medians = {tool: run["median"] for tool, run in info["speed"].items() if isinstance(run, dict) and run.get("median") is not None}
        toolchain = sum(t for tool, t in medians.items() if not tool.startswith("vibepiler"))
        rows.append((repo, info["loc"], medians["vibepiler-O2"], medians["knip"], toolchain))
    return sorted(rows, key=lambda r: r[1])


def verdict(row: list) -> str:
    """The TP/FP/unclear label wherever it sits in a hand-label row (the two harnesses order them differently)."""
    flat = [*row[0], *row[1:]] if isinstance(row[0], list) else row
    return next(v for v in flat if v in VERDICTS)


def precision(language: str, pool: str) -> tuple[int, int]:
    rows = json.loads((EVAL / language / "results.json").read_text())["labels"][pool]
    labels = [verdict(r) for r in rows]
    return labels.count("TP"), len(labels)


def speed_panel(ax, rows, title: str, single: str) -> None:
    width = 0.26
    xs = range(len(rows))
    series = [(2, "vibepiler -O2", BLUE, ""), (3, f"{single} alone", AQUA, ".."), (4, "toolchain, one after another", ORANGE, "///")]
    for k, (col, label, colour, hatch) in enumerate(series):
        ax.bar([x + (k - 1) * width for x in xs], [r[col] for r in rows], width=width - 0.03, color=colour,
               hatch=hatch, edgecolor="white", linewidth=0, label=label, zorder=3)
    ax.set_yscale("log")
    ticks = [t for t in (0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50) if min(min(r[2:]) for r in rows) / 1.5 <= t <= max(max(r[2:]) for r in rows) * 1.5]
    ax.set_yticks(ticks)
    ax.set_yticklabels([f"{t:g}" for t in ticks])
    ax.minorticks_off()
    ax.set_xticks(list(xs))
    ax.set_xticklabels([f"{r[0].replace('-', '-\n', 1) if len(r[0]) > 9 else r[0]}\n{r[1] / 1000:.0f}k" for r in rows], fontsize=6.5)
    ax.set_xlabel("repo, thousands of lines")
    ax.set_ylabel("seconds, warm median (log)")
    ax.set_title(title)
    ax.grid(axis="y", color=GRID, lw=0.6, zorder=0)
    ax.legend(frameon=False, loc="upper left", fontsize=7)


def precision_panel(ax) -> None:
    pools = [  # (label, (language, vibepiler-only pool), (language, other-tool-only pool), other tool)
        ("Python\ndead code", ("python", "dead_vibepiler_only"), ("python", "dead_vulture_only"), "vulture"),
        ("JS/TS\ndead code", ("javascript", "dead_vibepiler_only"), ("javascript", "dead_knip_only"), "knip"),
        ("JS/TS\ndependencies", ("javascript", "deps_vibepiler_only"), ("javascript", "deps_tool_only"), "knip/depcheck"),
    ]
    height = 0.36
    for i, (label, ours, theirs, tool) in enumerate(pools):
        for k, (pool, colour, hatch, who) in enumerate([(ours, BLUE, "", "vibepiler"), (theirs, ORANGE, "///", tool)]):
            tp, n = precision(*pool)
            y = i + (k - 0.5) * height
            ax.barh(y, tp / n, height=height - 0.04, color=colour, hatch=hatch, edgecolor="white", linewidth=0, zorder=3)
            ax.annotate(f"{who} only: {tp}/{n}", (tp / n, y), xytext=(4, 0), textcoords="offset points", va="center", fontsize=7, color=INK)
    ax.set_yticks(range(len(pools)))
    ax.set_yticklabels([p[0] for p in pools], fontsize=8)
    ax.invert_yaxis()
    ax.set_xlim(0, 1.25)
    ax.set_xticks([0, 0.5, 1])
    ax.set_xticklabels(["0%", "50%", "100%"])
    ax.set_xlabel("findings confirmed by reading the code")
    ax.set_title("Where the tools disagree: who is right")
    ax.grid(axis="x", color=GRID, lw=0.6, zorder=0)


def main() -> None:
    plt.rcParams.update({
        "font.family": "sans-serif", "font.size": 9, "axes.titlesize": 10, "axes.titleweight": "semibold", "axes.labelsize": 8,
        "axes.edgecolor": INK2, "axes.labelcolor": INK, "xtick.color": INK2, "ytick.color": INK2, "text.color": INK,
        "axes.spines.top": False, "axes.spines.right": False, "svg.fonttype": "none", "pdf.fonttype": 42,
    })
    fig, (ax, bx, cx) = plt.subplots(1, 3, figsize=(15, 3.6), dpi=300, gridspec_kw={"wspace": 0.34, "width_ratios": [1.35, 1.05, 0.9]})
    fig.patch.set_facecolor("white")
    speed_panel(ax, python_speed(), "Python: vibepiler vs the tools it covers", "vulture")
    speed_panel(bx, js_speed(), "JS/TS: vibepiler vs the tools it covers", "knip")
    precision_panel(cx)
    out = EVAL / "img"
    out.mkdir(parents=True, exist_ok=True)
    for ext in ("svg", "pdf", "png"):
        fig.savefig(out / f"eval.{ext}", bbox_inches="tight", facecolor="white")


if __name__ == "__main__":
    main()
