"""Structural baselines on the dense CodeNet sample: how many same-problem (type-4) pairs do structural tools find?

usage: structural.py OUT_DIR [JSCPD_JSON]
1. vibepiler's own duplicates pass logic, applied to whole programs: tree-sitter type sequence (identifiers and
   literals normalised), exact shape hash, and 4-gram shingle Jaccard >= 0.85 with the 1.3 length-ratio rule
   (vibepiler.passes.duplicates.similar_pairs / type_sequence, imported, not re-implemented). Also a sweep of lower
   Jaccard thresholds to show the precision/recall a structural rule can reach at all.
2. jscpd (token-based copy/paste detector), if its JSON report is given: file pairs with any clone between them.
Also writes OUT_DIR/dense_files/ for jscpd to scan.
"""

from __future__ import annotations

import hashlib
import json
import sys
from itertools import combinations
from pathlib import Path

from vibepiler.frontend.languages import get_language
from vibepiler.frontend.parser import parse
from vibepiler.passes.astutil import type_sequence
from vibepiler.passes.duplicates import MAX_LENGTH_RATIO, similar_pairs

OUT = Path(sys.argv[1])
data = json.loads((OUT / "samples.json").read_text())
dense = data["dense"]
labels = [x["label"] for x in dense]
npos = sum(1 for i, j in combinations(range(len(dense)), 2) if labels[i] == labels[j])

files = OUT / "dense_files"
files.mkdir(exist_ok=True)
for k, x in enumerate(dense):
    (files / f"{k:04d}__{x['id'].replace('/', '__')}.py").write_text(x["text"])

leaf = get_language("python").leaf_types
seqs = [type_sequence(parse("python", x["text"].encode()).root_node, leaf) for x in dense]
shingles = [{" ".join(s[i : i + 4]) for i in range(max(1, len(s) - 3))} for s in seqs]


def score(found: set[tuple[int, int]]) -> dict:
    tp = sum(labels[i] == labels[j] for i, j in found)
    return {"reported": len(found), "tp": tp, "precision": round(tp / len(found), 4) if found else None, "recall": round(tp / npos, 6)}


res = {"positives": npos, "n": len(dense)}
shape = {}
for i, s in enumerate(seqs):
    shape.setdefault(hashlib.sha1(" ".join(s).encode()).hexdigest(), []).append(i)
exact = {(a, b) for g in shape.values() if len(g) > 1 for a, b in combinations(sorted(g), 2)}
res["exact_shape_hash"] = score(exact)
sweep = {}
for t in (0.85, 0.7, 0.5, 0.3):
    found = {(i, j) for i, j, _ in similar_pairs(shingles, t) if max(len(seqs[i]), len(seqs[j])) <= min(len(seqs[i]), len(seqs[j])) * MAX_LENGTH_RATIO}
    sweep[str(t)] = score(found | exact)
res["shingle_jaccard_4gram"] = sweep  # 0.85 is vibepiler's near_duplicate_similarity default

if len(sys.argv) > 2 and Path(sys.argv[2]).exists():
    rep = json.loads(Path(sys.argv[2]).read_text())
    idx = {p.name: int(p.name[:4]) for p in files.iterdir()}
    found = set()
    for d in rep.get("duplicates", []):
        a, b = idx[Path(d["firstFile"]["name"]).name], idx[Path(d["secondFile"]["name"]).name]
        if a != b:
            found.add((min(a, b), max(a, b)))
    res["jscpd"] = score(found)
print(json.dumps(res, indent=1))
(OUT / "structural.json").write_text(json.dumps(res, indent=1))
