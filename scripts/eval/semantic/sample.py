"""Build the evaluation samples from CodeNet Python800 (data only: files are read as text, never executed).

dense : 100 problems x 20 accepted solutions (seed 0) -> retrieval (MAP@R, P@1) and dense pair detection
sparse: 200 problems contribute a twin pair + 600 other problems contribute one distractor each (seeds 0-4):
        a repo-like setting where few functions have a twin, which is what the mutual-NN rule is built for

Texts are whitespace-normalised exactly as vibepiler's symbol_text does (runs of spaces/tabs -> one space).
Exact duplicates after normalisation are dropped before sampling.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])  # .../Project_CodeNet_Python800
OUT = Path(sys.argv[2])
DENSE_PROBLEMS, DENSE_PER = 100, 20
SPARSE_TWINS, SPARSE_SEEDS = 200, [0, 1, 2, 3, 4]


def norm(src: str) -> str:
    return re.sub(r"[ \t]+", " ", src)


def load() -> dict[str, list[tuple[str, str]]]:
    probs: dict[str, list[tuple[str, str]]] = {}
    for d in sorted(p for p in ROOT.iterdir() if p.is_dir()):
        seen, items = set(), []
        for f in sorted(d.glob("*.py")):
            try:
                t = norm(f.read_bytes().decode("utf-8"))
            except UnicodeDecodeError:
                continue
            h = hashlib.sha1(t.strip().encode()).hexdigest()
            if not t.strip() or h in seen:
                continue
            seen.add(h)
            items.append((f"{d.name}/{f.stem}", t))
        probs[d.name] = items
    return probs


def main() -> None:
    probs = load()
    names = sorted(probs)
    rng = random.Random(0)
    dense_p = sorted(rng.sample([p for p in names if len(probs[p]) >= DENSE_PER], DENSE_PROBLEMS))
    dense = []
    for p in dense_p:
        for sid, t in random.Random(f"dense-{p}").sample(probs[p], DENSE_PER):
            dense.append({"id": sid, "label": p, "text": t})
    sparse = {}
    for seed in SPARSE_SEEDS:
        r = random.Random(1000 + seed)
        order = names[:]
        r.shuffle(order)
        items = []
        for i, p in enumerate(order):
            k = 2 if i < SPARSE_TWINS else 1
            for sid, t in r.sample(probs[p], k):
                items.append({"id": sid, "label": p, "text": t})
        sparse[str(seed)] = items
    OUT.write_text(json.dumps({"source": str(ROOT), "dense": dense, "sparse": sparse,
                               "params": {"dense_problems": DENSE_PROBLEMS, "dense_per": DENSE_PER, "dense_seed": 0,
                                          "sparse_twins": SPARSE_TWINS, "sparse_seeds": SPARSE_SEEDS,
                                          "problems_total": len(names), "solutions_total": sum(len(v) for v in probs.values())}}))
    lens = sorted(len(x["text"]) for x in dense)
    print(f"dense {len(dense)} programs, median {lens[len(lens)//2]} chars, p95 {lens[int(len(lens)*.95)]}; sparse {[len(v) for v in sparse.values()]}")


if __name__ == "__main__":
    main()
