"""Draw the hand-verification samples from out/disagreements.json, round-robin over repos, seed 0.

Usage: python3 -I scripts/eval/python/sample.py > out/samples.json
"""

import json
import random
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent  # scripts/eval/python
ROOT = HERE.parents[2]  # the vibepiler checkout
E = Path(os.environ.get("VIBEPILER_EVAL_WORK", ROOT / ".eval" / "python"))  # clones, tools, raw outputs (gitignored)
pools = json.loads((E / "out" / "disagreements.json").read_text())


def draw(section: str, key: str, n: int) -> list:
    per = {}
    for repo, p in pools.items():
        items = list(p[section].get(key, []))
        random.Random(f"{repo}:{key}").shuffle(items)
        per[repo] = items
    out = []
    while len(out) < n and any(per.values()):
        for repo in pools:
            if per[repo] and len(out) < n:
                out.append([repo] + (per[repo].pop() if isinstance(per[repo][-1], list) else [per[repo].pop()]))
    return out


print(json.dumps({
    "dead_vibepiler_only": draw("dead", "vibepiler_only_defs", 15),
    "dead_vulture_only": draw("dead", "vulture_only_defs", 15),
    "deps_unused_vp_only": draw("deps", "unused_vp_only", 10),
    "deps_unused_deptry_only": draw("deps", "unused_deptry_only", 10),
    "deps_undecl_vp_only": draw("deps", "undecl_vp_only", 10),
    "deps_undecl_deptry_only": draw("deps", "undecl_deptry_only", 10),
}, indent=1))
