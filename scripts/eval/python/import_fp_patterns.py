"""Classify every vibepiler unused-import by a textual pattern that shows the name is in fact used.

Patterns: the name appears in an `except` clause; in a subscripted base class `class X(Name[...])`;
another project file imports the name from this module; otherwise counted as 'no textual use' (likely TP)
or 'other textual use'. Usage: python3 -I scripts/eval/python/import_fp_patterns.py
"""
import json
import re
from collections import Counter
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent  # scripts/eval/python
ROOT = HERE.parents[2]  # the vibepiler checkout
E = Path(os.environ.get("VIBEPILER_EVAL_WORK", ROOT / ".eval" / "python"))  # clones, tools, raw outputs (gitignored)
REPOS = ["requests", "flask", "httpx", "vibepiler", "gpython", "rich", "black", "django"]
out = {}
for repo in REPOS:
    d = json.loads((E / "out" / repo / "vibepiler_O2" / "run1" / "ir.json").read_text())
    root = E / "repos" / repo
    mods = {}
    for n in d["nodes"]:
        if n["kind"] == "module" and n.get("file"):
            mods[n["file"]] = n["qualname"] if "." in n["qualname"] else n["name"]
    texts = {}
    c = Counter()
    for x in d["diagnostics"]:
        if x["code"] != "unused-import":
            continue
        name = x["data"]["local_name"].split(".")[0]
        f = x["file"]
        src = texts.setdefault(f, (root / f).read_text(errors="replace"))
        lines = [l for i, l in enumerate(src.splitlines()) if i + 1 != x["line"]]
        body = "\n".join(lines)
        if re.search(rf"^\s*except\s*\(?[^:]*\b{re.escape(name)}\b", body, re.M):
            c["except clause"] += 1
        elif re.search(rf"^\s*class \w+\([^)]*\b{re.escape(name)}\[", body, re.M):
            c["subscripted base"] += 1
        else:
            modname = mods.get(f, "")
            short = modname.split(".")[-1] if modname else Path(f).stem
            reimported = any(
                re.search(rf"from \S*{re.escape(short)} import [^\n]*\b{re.escape(name)}\b", t.read_text(errors="replace"))
                for t in root.rglob("*.py") if str(t.relative_to(root)) != f and ".git" not in t.parts
            ) if not f.endswith("__init__.py") else False
            if reimported:
                c["re-imported from this module elsewhere"] += 1
            elif re.search(rf"\b{re.escape(name)}\b", body.replace(x["data"].get("module") or "\0", "")):
                c["other textual use"] += 1
            else:
                c["no textual use"] += 1
    out[repo] = dict(c)
    print(repo, sum(c.values()), dict(c), flush=True)
(E / "out" / "import_fp_patterns.json").write_text(json.dumps(out, indent=1))
