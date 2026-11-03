"""The bootstrap figure for the README and for papers: bloat and tangle over the four rounds, and the removable lines.

Run:  uv run --with matplotlib python scripts/bootstrap_figure.py
Writes docs/img/bootstrap.{svg,pdf,png}. Colour-blind safe (validated adjacent pairs), hatched bars, vector output.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROUNDS = ["start", "r1\ndelete", "r2\nsplit", "r3\nsplit", "r4\nfold"]
BLOAT = [0.31, 0.31, 0.28, 0.26, 0.25]
TANGLE = [0.30, 0.31, 0.27, 0.26, 0.25]
WASTE_LINES = [71, 34, 41, 31, 18]

BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"  # categorical slots 1, 2, 3 of the reference palette
INK, INK2, GRID = "#0b0b0b", "#52514e", "#dcdbd5"

def main() -> None:
    plt.rcParams.update({
        "font.family": "sans-serif", "font.size": 9, "axes.titlesize": 10, "axes.titleweight": "semibold", "axes.labelsize": 9,
        "axes.edgecolor": INK2, "axes.labelcolor": INK, "xtick.color": INK2, "ytick.color": INK2, "text.color": INK,
        "axes.spines.top": False, "axes.spines.right": False, "svg.fonttype": "none", "pdf.fonttype": 42,
    })

    fig, (ax, bx) = plt.subplots(1, 2, figsize=(7.6, 2.9), dpi=300, gridspec_kw={"wspace": 0.42})
    fig.patch.set_facecolor("white")
    x = range(len(ROUNDS))

    # A. two unitless scores on one axis: solid vs dashed and circle vs square, so colour is never the only cue
    ax.plot(x, BLOAT, color=BLUE, lw=2, marker="o", ms=5, label="bloat", zorder=3)
    ax.plot(x, TANGLE, color=ORANGE, lw=2, ls="--", marker="s", ms=4.5, label="tangle", zorder=3)
    ax.annotate(f"bloat {BLOAT[-1]:.0%}", (4, BLOAT[-1]), xytext=(6, 6), textcoords="offset points", color=INK, fontsize=8)
    ax.annotate(f"tangle {TANGLE[-1]:.2f}", (4, TANGLE[-1]), xytext=(6, -11), textcoords="offset points", color=INK, fontsize=8)
    ax.annotate(f"{BLOAT[0]:.0%}", (0, BLOAT[0]), xytext=(-4, 7), textcoords="offset points", color=INK, fontsize=8, ha="center")
    ax.set_xlim(-0.35, 5.45)  # room for the end labels inside the panel
    ax.set_ylim(0.20, 0.34)
    ax.set_yticks([0.20, 0.25, 0.30])
    ax.set_yticklabels(["0.20", "0.25", "0.30"])
    ax.set_xticks(list(x))
    ax.set_xticklabels(ROUNDS, fontsize=7.5)
    ax.set_title("Bloat and tangle, vibepiler on itself")
    ax.set_ylabel("share of the code")
    ax.grid(axis="y", color=GRID, lw=0.6, zorder=0)
    ax.legend(frameon=False, loc="lower left", fontsize=8)

    # B. the lines that could go, in lines: one series, hatched so it survives print and colour vision deficiency
    bars = bx.bar(x, WASTE_LINES, width=0.62, color=AQUA, edgecolor="white", linewidth=0, hatch="///", zorder=3)
    for b in bars:
        b.set_edgecolor("#0b6b4a")
        b.set_linewidth(0.6)
        bx.annotate(f"{int(b.get_height())}", (b.get_x() + b.get_width() / 2, b.get_height()), xytext=(0, 3), textcoords="offset points", ha="center", fontsize=8, color=INK)
    bx.set_ylim(0, 84)
    bx.set_yticks([0, 20, 40, 60, 80])
    bx.set_xticks(list(x))
    bx.set_xticklabels(ROUNDS, fontsize=7.5)
    bx.set_title("Removable lines shown by analysis")
    bx.set_ylabel("lines (of ~6,400)", labelpad=4)
    bx.grid(axis="y", color=GRID, lw=0.6, zorder=0)
    bx.set_axisbelow(True)
    ax.set_axisbelow(True)

    fig.text(0.01, -0.06, "Each round: apply the top remarks of `vibepiler compile . -R`, re-run with --baseline. Round 1 deleted waste; rounds 2–3 split the most tangled functions; round 4 folded duplicates and accepted the judgment calls.\n"
             "Tangle is the share-weighted noisy OR of per-function terms; bloat = 1 − (1 − waste)(1 − tangle). Measured at -O3, 54% of call sites linked.", fontsize=7, color=INK2, va="top")

    out = Path(__file__).resolve().parents[1] / "docs" / "img"
    out.mkdir(parents=True, exist_ok=True)
    for ext in ("svg", "pdf", "png"):
        fig.savefig(out / f"bootstrap.{ext}", bbox_inches="tight", facecolor="white")
    print("wrote", out / "bootstrap.{svg,pdf,png}")


if __name__ == "__main__":
    main()
