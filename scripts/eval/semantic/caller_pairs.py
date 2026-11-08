"""How many reported semantic-duplicate pairs are a function and its direct callee (a wrapper, not a twin)?"""
import json, sys
from pathlib import Path
OUT = Path(sys.argv[1]); res = []
for r in ("vibepiler", "rich", "django"):
    d = json.loads((OUT / "cli" / f"{r}_cre_int8" / "ir.json").read_text())
    edges = d.get("edges") or d.get("graph", {}).get("edges", [])
    calls = {(e["src"], e["dst"]) for e in edges if e.get("kind") == "calls"}
    pairs = [x for x in d["diagnostics"] if x["code"] == "semantic-duplicate"]
    hit = [x["data"]["members"] for x in pairs if (x["data"]["ids"][0], x["data"]["ids"][1]) in calls or (x["data"]["ids"][1], x["data"]["ids"][0]) in calls]
    res.append({"repo": r, "pairs": len(pairs), "one_calls_the_other": len(hit), "examples": hit[:4]})
    print(json.dumps(res[-1]))
(OUT / "caller_pairs.json").write_text(json.dumps(res, indent=1))
