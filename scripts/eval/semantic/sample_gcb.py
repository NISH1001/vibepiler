"""GPTCloneBench (ICSME 2023) Python Type-4 standalone clones -> labelled groups (data only, never executed).

usage: sample_gcb.py GCB_ROOT OUT.json
Each file standalone/true_semantic_clones/py/prompt_*/T4/Clone_N.py holds one pair: a SemanticCloneBench function,
two or more blank lines, then a GPT-3 rewrite. The split is on the first run of >= 2 blank lines before a
non-indented line; files that do not split into two non-empty parts are skipped and counted. Every rewrite of the
same original shares the original's label, so a group is {original, its rewrites}. Whitespace is normalised like
vibepiler's symbol_text. All groups are used (no sampling).
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

root, out = Path(sys.argv[1]), Path(sys.argv[2])
groups: dict[str, set[str]] = {}
skipped = files = 0
for f in sorted(root.glob("standalone/true_semantic_clones/py/prompt_*/T4/*.py")):
    files += 1
    t = f.read_text(errors="replace").strip("\n")
    parts = re.split(r"\n[ \t]*\n[ \t]*\n+[ ]?(?=\S)", t, maxsplit=1)
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        skipped += 1
        continue
    a = re.sub(r"[ \t]+", " ", parts[0].strip())
    b = re.sub(r"[ \t]+", " ", re.sub(r"^(#[^\n]*\n)+", "", parts[1].strip()))
    if a != b:
        groups.setdefault(a, set()).add(b)
# The same SemanticCloneBench original appears in several near-identical spellings (print x vs print(x), a trailing
# line), which would split one task into groups that count each other as negatives. Merge originals that define the
# same top-level name and share >= 0.6 of their token 3-grams (union-find); the merged label is the smallest member's.
def _name(t: str) -> str | None:
    m = re.search(r"(?:def|class)\s+(\w+)", t)
    return m.group(1) if m else None


def _grams(t: str) -> set[str]:
    toks = re.findall(r"\w+|[^\w\s]", t)
    return {" ".join(toks[i : i + 3]) for i in range(max(1, len(toks) - 2))}


origs = sorted(groups)
parent = list(range(len(origs)))


def _find(i: int) -> int:
    while parent[i] != i:
        parent[i] = parent[parent[i]]
        i = parent[i]
    return i


by_name: dict[str | None, list[int]] = {}
for i, a in enumerate(origs):
    by_name.setdefault(_name(a), []).append(i)
grams = [_grams(a) for a in origs]
merges = 0
for idx in by_name.values():
    for x in range(len(idx)):
        for y in range(x + 1, len(idx)):
            i, j = idx[x], idx[y]
            if len(grams[i] & grams[j]) / len(grams[i] | grams[j]) >= 0.6 and _find(i) != _find(j):
                parent[_find(j)] = _find(i)
                merges += 1
label_of = {a: hashlib.sha1(origs[_find(i)].encode()).hexdigest()[:12] for i, a in enumerate(origs)}
items, seen = [], set()
for a in origs:
    label = label_of[a]
    for t in [a, *sorted(groups[a])]:
        if t not in seen:  # a text in two groups keeps its first label
            seen.add(t)
            items.append({"id": f"{label}/{len(items)}", "label": label, "text": t})
from collections import Counter

c = Counter(x["label"] for x in items)
items = [x for x in items if c[x["label"]] > 1]
out.write_text(json.dumps({"source": str(root), "dense": items, "sparse": {},
                           "params": {"files": files, "skipped_unsplittable": skipped, "original_variants_merged": merges, "groups": len({x['label'] for x in items}), "functions": len(items)}}))
print(f"{files} files, {skipped} unsplittable, {merges} original variants merged, {len({x['label'] for x in items})} groups, {len(items)} functions")
