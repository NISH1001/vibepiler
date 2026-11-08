"""Render results.json into markdown tables (out/tables.md); RESULTS.md quotes them.

usage: python -I report.py
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DOCS, HERE, OUT  # noqa: E402

R = json.loads((DOCS / "results.json").read_text())
REPOS = list(R["repos"])
TOOLS = ["knip", "ts-prune", "ts-unused-exports", "depcheck", "jscpd", "madge", "eslint"]

# kinds a tool in this study covers (compared), kinds a standard linter rule covers but was not run, the rest are novel
COMPARED = {"unused-export", "unreachable-module", "unused-dependency", "undeclared-dependency", "duplicate-function",
            "near-duplicate-function", "import-cycle", "high-complexity", "high-cognitive-complexity",
            "swallowed-exception", "unused-import"}
STANDARD = {"dead-symbol": "no-unused-vars (non-exported) / knip (exported)", "long-function": "max-lines-per-function",
            "deep-nesting": "max-depth", "too-many-params": "max-params", "dead-store": "no-useless-assignment (partial)",
            "unused-param": "no-unused-vars args", "redundant-try": "no-useless-catch", "stub-function": "no-empty-function",
            "unreachable-code": "no-unreachable", "dead-branch": "no-constant-condition"}


def t(rows, head):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def f(x, nd=2):
    return "–" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def speed():
    rows, sums = [], []
    for repo in REPOS:
        s = R["repos"][repo]["speed"]
        cells = [repo, f"{R['repos'][repo]['loc']:,}"]
        for tool in ["vibepiler-O1", "vibepiler-O2"] + TOOLS:
            x = s.get(tool, {})
            cells.append("skip" if "skipped" in x else f"{x['median']:.2f}")
        chain = sum(s[tl]["median"] for tl in TOOLS if "median" in s.get(tl, {}))
        o2 = s["vibepiler-O2"]["median"]
        cells += [f"{chain:.2f}", f"{chain / o2:.1f}×"]
        rows.append(cells)
        sums.append((repo, chain, o2))
    a = t(rows, ["repo", "LOC", "vp -O1", "vp -O2", *TOOLS, "toolchain Σ", "Σ / vp-O2"])
    rows = []
    for repo in REPOS:
        s = R["repos"][repo]["speed"]
        rows.append([repo] + [f"{s[tl]['peak_rss_mb']:.0f}" if "peak_rss_mb" in s.get(tl, {}) else "skip"
                              for tl in ["vibepiler-O1", "vibepiler-O2"] + TOOLS])
    b = t(rows, ["repo (peak RSS, MB)", "vp -O1", "vp -O2", *TOOLS])
    rows = []
    for repo in REPOS:
        s, loc = R["repos"][repo]["speed"], R["repos"][repo]["loc"]
        rows.append([repo, R["repos"][repo]["files"], f"{loc:,}", f"{s['vibepiler-O2']['median']:.2f}",
                     f"{loc / s['vibepiler-O2']['median'] / 1000:.1f}", f"{s['knip']['median']:.2f}",
                     f"{loc / s['knip']['median'] / 1000:.1f}", f"{s['vibepiler-O2']['cold']:.2f}"])
    c = t(rows, ["repo", "files", "LOC", "vp -O2 s", "vp kLOC/s", "knip s", "knip kLOC/s", "vp -O2 cold s"])
    return a, b, c


def determinism():
    rows = []
    for repo in REPOS:
        d = R["repos"][repo]["determinism"]
        rows.append([repo] + [f"{d[l]['runs']} runs, {d[l]['diagnostics']} diags, "
                              f"{'identical' if d[l]['sorted_identical'] and d[l]['order_identical'] and d[l]['graph_identical'] else 'DIFFERS'}"
                              f" ({d[l]['hashes'][0]})" for l in ("O1", "O2")])
    return t(rows, ["repo", "-O1", "-O2"])


def agreement(keys):
    rows = []
    for key in keys:
        tot = Counter()
        for repo in REPOS:
            a = R["repos"][repo]["agreement"].get(key)
            if not a:
                continue
            rows.append([key, repo, a["vibepiler"], a["tool"], a["both"], a["vibepiler_only"], a["tool_only"], f(a["jaccard"])])
            tot.update({k: a[k] for k in ("vibepiler", "tool", "both", "vibepiler_only", "tool_only")})
        u = tot["vibepiler"] + tot["tool"] - tot["both"]
        rows.append([f"**{key}**", "**all**", tot["vibepiler"], tot["tool"], tot["both"], tot["vibepiler_only"],
                     tot["tool_only"], f"**{tot['both'] / u:.3f}**" if u else "–"])
    return t(rows, ["comparison", "repo", "vibepiler", "tool", "both", "vp only", "tool only", "Jaccard"])


def complexity():
    rows = []
    for repo in REPOS:
        c = R["repos"][repo].get("complexity")
        if not c:
            continue
        for k in ("cyclomatic", "cognitive"):
            x = c[k]
            rows.append([repo, k, x["matched"], f"{x['exact_pct']}%", f"{x['within1_pct']}%", f(x["mean_diff"]), f(x["pearson"], 3),
                         f"{x['flags_on_matched']['both']}/{x['flags_on_matched']['vibepiler']}/{x['flags_on_matched']['tool']}",
                         f(x["flags_on_matched"]["jaccard"], 3)])
    return t(rows, ["repo", "metric", "functions matched", "exact", "±1", "mean vp−tool", "Pearson",
                    "flags both/vp/tool (matched fns)", "flag Jaccard"])


def cycles():
    rows = []
    for repo in REPOS:
        c = R["repos"][repo].get("cycles")
        a = R["repos"][repo]["agreement"]
        rows.append([repo, c["vibepiler_sizes"], c["madge_scc_sizes"], c["madge_elementary"], c["madge_graph_scc_sizes"],
                     f(a["cycle_modules/madge"]["jaccard"]), f(a["cycle_modules/madge graph SCC"]["jaccard"])])
    return t(rows, ["repo", "vp SCC sizes", "madge --circular (merged) sizes", "madge cycles listed",
                    "madge full-graph SCC sizes", "J(modules) vs --circular", "J(modules) vs graph SCC"])


def novelty():
    codes = sorted({c for repo in REPOS for c in R["repos"][repo]["kinds"]})
    rows = []
    for c in codes:
        status = "compared" if c in COMPARED else (f"standard rule, not run: {STANDARD[c]}" if c in STANDARD else "**none**")
        rows.append([c, status] + [R["repos"][r]["kinds"].get(c, 0) for r in REPOS])
    rows.sort(key=lambda r: (r[1] != "**none**", r[1] != "compared", r[0]))
    return t(rows, ["code", "covered by", *REPOS])


def precision():
    labels = R["labels"]
    rows = []
    for k, items in labels.items():
        if k.startswith("_"):
            continue
        c = Counter(lbl for _, lbl, _ in items)
        n = len(items)
        rows.append([k, R["samples"][k]["pool"], n, c["TP"], c["FP"], c["unclear"], f"{c['TP'] / n:.0%}"])
    return t(rows, ["sample", "pool", "verified", "TP", "FP", "unclear", "precision"])


def labels():
    out = []
    for k, items in R["labels"].items():
        if k.startswith("_"):
            continue
        out.append(f"\n**{k}**\n")
        out.append(t([[r, kind, f"`{file}`" + (f" `{sym}`" if sym else ""), lbl, why] for (r, kind, file, sym), lbl, why in items],
                     ["repo", "kind", "finding", "label", "reason"]))
    return "\n".join(out)


def main():
    a, b, c = speed()
    parts = [
        "## Speed (median of 3 warm runs, seconds)\n", a, "\n\n### Peak RSS\n", b, "\n\n### Time vs size\n", c,
        "\n\n## Determinism\n", determinism(),
        "\n\n## Dead code vs knip / ts-prune / ts-unused-exports\n",
        agreement(["unused_files/knip", "unused_exports/knip", "unused_exports_broad/knip",
                   "unused_exports_broad/knip (files neither flags)", "unused_exports_broad/ts-prune",
                   "unused_exports_broad/ts-unused-exports", "knip/ts-prune (tool vs tool)", "knip/ts-unused-exports (tool vs tool)"]),
        "\n\n## Dependencies vs knip / depcheck (workspace manifests)\n",
        agreement(["unused_deps/knip", "unused_deps/depcheck", "undeclared_deps/knip", "undeclared_deps/depcheck"]),
        "\n\n## Duplicates vs jscpd (unit: vibepiler function nodes; jscpd hit = clone covers ≥50% of the function)\n",
        agreement(["duplicate_functions/jscpd", "duplicate_functions/jscpd --ignore-identifiers --ignore-literals"]),
        "\n\n## Cycles vs madge\n", cycles(),
        "\n\n## Complexity vs ESLint `complexity` / sonarjs `cognitive-complexity`\n", complexity(),
        "\n\n## Swallowed exceptions vs ESLint `no-empty` (catch blocks), unused imports vs `no-unused-vars`\n",
        agreement(["swallowed_exceptions/eslint no-empty", "unused_imports/eslint no-unused-vars"]),
        "\n\n## Finding kinds and who covers them (vibepiler -O2 counts)\n", novelty(),
        "\n\n## Hand-verified precision on disagreements\n", precision(),
        "\n\n### Every verified item\n", labels(),
    ]
    tables = "".join(parts) + "\n"
    (OUT / "tables.md").write_text(tables)
    head = (HERE / "RESULTS.head.md").read_text()
    (DOCS / "RESULTS.md").write_text(head + tables.replace("\n## ", "\n### ").replace("\n### Every", "\n#### Every"))
    print("wrote", OUT / "tables.md", "and", DOCS / "RESULTS.md")


if __name__ == "__main__":
    main()
