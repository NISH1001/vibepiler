"""Print what a hand-verifier needs for one finding: the definition and every textual use of the name.

Usage: python3 -I scripts/eval/python/inspect_item.py <repo> <file> <name> [context_lines]
"""

import re
import sys
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent  # scripts/eval/python
ROOT = HERE.parents[2]  # the vibepiler checkout
E = Path(os.environ.get("VIBEPILER_EVAL_WORK", ROOT / ".eval" / "python"))  # clones, tools, raw outputs (gitignored)
repo, file, name = sys.argv[1:4]
ctx = int(sys.argv[4]) if len(sys.argv) > 4 else 4
root = E / "repos" / repo
src = (root / file).read_text(errors="replace").splitlines()
pat = re.compile(rf"\b{re.escape(name)}\b")
defs = [i for i, l in enumerate(src) if re.search(rf"(def|class)\s+{re.escape(name)}\b|^\s*{re.escape(name)}\s*[:=]|self\.{re.escape(name)}\s*=", l)]
for i in defs[:3]:
    print(f"--- {file}:{i + 1}")
    for j in range(max(0, i - 2), min(len(src), i + ctx)):
        print(f"{j + 1:5} {src[j]}")
uses = []
for p in root.rglob("*"):
    if p.suffix not in (".py", ".pyi", ".toml", ".cfg", ".txt", ".rst", ".md", ".html", ".ini", ".json", ".yaml", ".yml") or ".git/" in str(p):
        continue
    try:
        for k, l in enumerate(p.read_text(errors="replace").splitlines()):
            if pat.search(l):
                uses.append(f"{p.relative_to(root)}:{k + 1}: {l.strip()[:140]}")
    except OSError:
        pass
print(f"=== {len(uses)} textual uses of {name}")
for u in uses[:25]:
    print("   ", u)
import json
d = json.loads((E / "out" / repo / "vibepiler_O2" / "run1" / "ir.json").read_text())
for x in d["diagnostics"]:
    if x["file"] == file and x["pass"] == "dead-code" and (x["data"].get("qualname", "").split(".")[-1] == name or x["code"] == "unreachable-module"):
        print("VP:", x["code"], x["line"], x["message"][:200])
for l in (E / "out" / repo / "vulture60" / "run1" / "vulture.txt").read_text().splitlines():
    if l.split(f"repos/{repo}/")[-1].startswith(file + ":") and f"'{name}'" in l:
        print("VULTURE:", l.split(f"repos/{repo}/")[-1])
