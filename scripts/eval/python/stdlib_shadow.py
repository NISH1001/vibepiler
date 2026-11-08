"""Count import edges vibepiler resolved from an absolute stdlib import (`import json`, `from datetime import date`)
to a project module that merely shares the name. Usage: python3 -I scripts/eval/python/stdlib_shadow.py"""
import json
import re
import sys
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent  # scripts/eval/python
ROOT = HERE.parents[2]  # the vibepiler checkout
E = Path(os.environ.get("VIBEPILER_EVAL_WORK", ROOT / ".eval" / "python"))  # clones, tools, raw outputs (gitignored)
STD = set(sys.stdlib_module_names)
res = {}
for repo in ["requests", "flask", "httpx", "vibepiler", "gpython", "rich", "black", "django"]:
    d = json.loads((E / "out" / repo / "vibepiler_O2" / "run1" / "ir.json").read_text())
    files = {n["id"]: n.get("file") for n in d["nodes"] if n["kind"] == "module"}
    bad = []
    for e in d["edges"]:
        if e["kind"] != "imports" or e["src"] not in files or e["dst"] not in files:
            continue
        dst = files[e["dst"]] or ""
        stem = Path(dst).stem if not dst.endswith("__init__.py") else Path(dst).parent.name
        if stem not in STD:
            continue
        src = (E / "repos" / repo / files[e["src"]]).read_text(errors="replace")
        legit = re.search(rf"from [\w.]*\.\s*import [^\n]*\b{stem}\b|from [\w.]*\.{stem}\b|from \.+{stem}\b|import [\w.]+\.{stem}\b", src)
        if not legit and re.search(rf"^\s*(import {stem}\b|from {stem}(\.\w+)* import)", src, re.M):
            bad.append((files[e["src"]], dst))
    res[repo] = dict(count=len(bad), examples=bad[:5])
    print(repo, len(bad), bad[:3])
(E / "out" / "stdlib_shadow.json").write_text(json.dumps(res, indent=1))
