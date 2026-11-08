"""Pull the semantic-duplicate findings out of each repo's `vibepiler compile -O3` ir.json, and dump the top pairs
with their source for hand-labelling (out/wild_pairs.md). Labels live in out/wild_labels.json (written by hand).

usage: wild_pairs.py OUT_DIR [PER_REPO]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

OUT = Path(sys.argv[1])
PER = int(sys.argv[2]) if len(sys.argv) > 2 else 5
REPOS = ["vibepiler", "rich", "django"]
rows, md = [], []
for r in REPOS:
    ir = OUT / "cli" / f"{r}_cre_int8" / "ir.json"
    if not ir.exists():
        print("missing", ir)
        continue
    d = json.loads(ir.read_text())
    sem = [x for x in d["diagnostics"] if x["code"] == "semantic-duplicate"]
    sem.sort(key=lambda x: -x["data"]["similarity"])
    meta = d.get("metrics", {}).get("semantic_duplicates", {})
    print(r, "pairs:", len(sem), "symbols:", meta.get("symbols"), "threshold:", meta.get("threshold"))
    for k, x in enumerate(sem[:PER]):
        row = {"repo": r, "rank": k + 1, "similarity": x["data"]["similarity"], "members": x["data"]["members"], "locations": x["data"]["locations"], "total_in_repo": len(sem)}
        rows.append(row)
        md.append(f"## {r} #{k + 1}  {x['data']['similarity']}  {' | '.join(x['data']['members'])}\n")
        for loc in x["data"]["locations"]:
            f, _, rng = loc.rpartition(":") if loc.count(":") >= 2 else (loc, "", "")
            path, line = loc.split(":")[0], loc.split(":")[1] if ":" in loc else "1"
            src = (OUT.parent / "repos" / r / path)
            try:
                lines = src.read_text(errors="replace").splitlines()
                a = int(line.split("-")[0]) - 1
                b = int(line.split("-")[1]) if "-" in line else a + 40
                md.append(f"```python\n# {loc}\n" + "\n".join(lines[a:b]) + "\n```\n")
            except (OSError, ValueError):
                md.append(f"(source not found: {loc})\n")
(OUT / "wild_pairs.json").write_text(json.dumps(rows, indent=1))
(OUT / "wild_pairs.md").write_text("\n".join(md))
print(len(rows), "pairs dumped")
