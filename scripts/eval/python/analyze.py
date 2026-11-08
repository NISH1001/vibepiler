"""Read the outputs bench.py left in out/ and compute determinism, agreement and novelty.

Every finding is normalized to a key relative to the repo root, then sets are compared.
Writes out/analysis.json and out/disagreements.json (the pools the hand-verification samples from).

Usage: python3 -I scripts/eval/python/analyze.py
"""

import hashlib
import json
import re
from collections import Counter, defaultdict
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent  # scripts/eval/python
ROOT = HERE.parents[2]  # the vibepiler checkout
E = Path(os.environ.get("VIBEPILER_EVAL_WORK", ROOT / ".eval" / "python"))  # clones, tools, raw outputs (gitignored)
OUT = E / "out"
REPOS = ["requests", "flask", "httpx", "vibepiler", "gpython", "rich", "black", "django"]
DEF_KINDS = {"function", "class", "method"}


def rel(repo: str, p: str) -> str:
    p = str(p)
    marker = f"repos/{repo}/"
    i = p.find(marker)
    return p[i + len(marker):] if i >= 0 else p


def ir(repo: str, run: int = 1) -> dict:
    return json.loads((OUT / repo / "vibepiler_O2" / f"run{run}" / "ir.json").read_text())


def stats(a: set, b: set) -> dict:
    inter = a & b
    union = a | b
    return dict(vibepiler=len(a), tool=len(b), both=len(inter), vibepiler_only=len(a - b), tool_only=len(b - a),
                jaccard=round(len(inter) / len(union), 3) if union else None)


# --- determinism --------------------------------------------------------------
def determinism(repo: str) -> dict:
    hashes = {}
    for lvl in ("vibepiler_O1", "vibepiler_O2"):
        for run_dir in sorted((OUT / repo / lvl).glob("run*")):
            d = json.loads((run_dir / "ir.json").read_text())
            diags = sorted(json.dumps(x, sort_keys=True) for x in d["diagnostics"])
            hashes[f"{lvl}/{run_dir.name}"] = hashlib.sha256("\n".join(diags).encode()).hexdigest()[:16]
    by = defaultdict(set)
    for k, h in hashes.items():
        by[k.split("/")[0]].add(h)
    return dict(hashes=hashes, identical={k: len(v) == 1 for k, v in by.items()}, runs={k: sum(1 for x in hashes if x.startswith(k)) for k in by})


# --- dead code ----------------------------------------------------------------
VULTURE = re.compile(r"^(.*?):(\d+): (?:unused (\w+) '([^']+)'|(unreachable code|redundant if-condition|unsatisfiable \w+ condition)[^(]*) \((\d+)% confidence")


def vulture(repo: str, tool: str) -> list[dict]:
    out = []
    for line in (OUT / repo / tool / "run1" / "vulture.txt").read_text().splitlines():
        m = VULTURE.match(line)
        if not m:
            continue
        f, ln, kind, name, other, conf = m.groups()
        out.append(dict(file=rel(repo, f), line=int(ln), kind=kind or other, name=name, conf=int(conf)))
    return out


DEADCODE = re.compile(r"^(.*?):(\d+):\d+: (DC\d+) (\w+(?: \w+)?) `?([^`]*)`?")


def deadcode(repo: str) -> list[dict]:
    out = []
    for line in (OUT / repo / "deadcode" / "run1" / "deadcode.txt").read_text().splitlines():
        m = DEADCODE.match(line)
        if m:
            f, ln, code, kind, name = m.groups()
            out.append(dict(file=rel(repo, f), line=int(ln), code=code, kind=kind.split()[0].lower(), name=name))
    return out


def vp_dead(d: dict) -> list[dict]:
    """Dead definitions, unreachable modules expanded to the definitions they hold."""
    out = []
    dead_mods = {x["file"] for x in d["diagnostics"] if x["code"] == "unreachable-module"}
    for x in d["diagnostics"]:
        if x["code"] in ("dead-symbol", "unreachable-symbol"):
            out.append(dict(file=x["file"], line=x["line"], kind=x["data"]["kind"], name=x["data"]["qualname"].split(".")[-1],
                            qualname=x["data"]["qualname"], code=x["code"]))
    for n in d["nodes"]:
        if n.get("file") in dead_mods and n["kind"] in DEF_KINDS | {"variable"}:
            out.append(dict(file=n["file"], line=n["start_line"], kind=n["kind"], name=n["name"], qualname=n["qualname"], code="unreachable-module"))
    return out


def dead_code(repo: str, d: dict) -> dict:
    vp = vp_dead(d)
    vp_defs = {(x["file"], x["name"]) for x in vp if x["kind"] in DEF_KINDS}
    vp_vars = {(x["file"], x["name"]) for x in vp if x["kind"] == "variable"}
    for x in d["diagnostics"]:
        if x["code"] == "unused-attribute":
            vp_vars |= {(x["file"], nm) for nm in x["data"]["names"]}
    res = {}
    pools = {}
    for tool in ("vulture60", "vulture100"):
        v = vulture(repo, tool)
        tdefs = {(x["file"], x["name"]) for x in v if x["kind"] in ("function", "class", "method", "property")}
        # vulture's 'variable' covers module/class variables and, at 100%, unused arguments; attributes are self.x
        tvars = {(x["file"], x["name"]) for x in v if x["kind"] in ("variable", "attribute") and x["conf"] < 100}
        res[tool] = dict(definitions=stats(vp_defs, tdefs), variables_attributes=stats(vp_vars, tvars),
                         kinds=dict(Counter(x["kind"] for x in v)))
        if tool == "vulture60":
            pools["vibepiler_only_defs"] = sorted(vp_defs - tdefs)
            pools["vulture_only_defs"] = sorted(tdefs - vp_defs)
    dc = deadcode(repo)
    ddefs = {(x["file"], x["name"]) for x in dc if x["kind"] in ("function", "class", "method", "property")}
    dvars = {(x["file"], x["name"]) for x in dc if x["kind"] in ("variable", "attribute", "name")}
    res["deadcode"] = dict(definitions=stats(vp_defs, ddefs), variables_attributes=stats(vp_vars, dvars),
                           kinds=dict(Counter(x["code"] + " " + x["kind"] for x in dc)))
    v60 = {(x["file"], x["name"]) for x in vulture(repo, "vulture60") if x["kind"] in ("function", "class", "method", "property")}
    res["vulture60_vs_deadcode_definitions"] = stats(v60, ddefs)
    return res, pools


# --- unused imports, dead stores, params, unreachable code ----------------------
def ruff(repo: str, tool: str) -> list[dict]:
    return [dict(file=rel(repo, x["filename"]), line=x["location"]["row"], code=x["code"], msg=x["message"])
            for x in json.loads((OUT / repo / tool / "run1" / "ruff.json").read_text())]


def local_findings(repo: str, d: dict) -> dict:
    diags = d["diagnostics"]
    r = ruff(repo, "ruff_unused")
    v = vulture(repo, "vulture60")
    vp_imp = {(x["file"], x["data"]["local_name"]) for x in diags if x["code"] == "unused-import"}
    f401 = {(x["file"], re.search(r"`([^`]+)`", x["msg"]).group(1).split(".")[-1]) for x in r if x["code"] == "F401"}
    vp_store = {(x["file"], ln) for x in diags if x["code"] == "dead-store" for ln in [x["line"]]}
    vp_param = {(x["file"], nm) for x in diags if x["code"] == "unused-param" for nm in x["data"]["names"]}
    vp_unreach = {(x["file"], x["line"]) for x in diags if x["code"] == "unreachable-code"}
    return dict(
        unused_imports_vs_ruff_F401=stats(vp_imp, f401),
        unused_imports_vs_ruff_F401_outside_init=stats({k for k in vp_imp if not k[0].endswith("__init__.py")}, {k for k in f401 if not k[0].endswith("__init__.py")}),
        unused_imports_vs_vulture=stats(vp_imp, {(x["file"], x["name"].split(".")[-1]) for x in v if x["kind"] == "import"}),
        dead_store_vs_ruff_F841=stats(vp_store, {(x["file"], x["line"]) for x in r if x["code"] == "F841"}),
        unused_param_vs_vulture100_args=stats(vp_param, {(x["file"], x["name"]) for x in vulture(repo, "vulture100") if x["kind"] == "variable"}),
        unreachable_code_vs_vulture=stats(vp_unreach, {(x["file"], x["line"]) for x in v if x["kind"] == "unreachable code"}),
    )


# --- exceptions ----------------------------------------------------------------
def exceptions(repo: str, d: dict) -> dict:
    sw = [x for x in d["diagnostics"] if x["code"] == "swallowed-exception"]
    vp_all = {(x["file"], x["line"]) for x in sw}
    vp_broad = {(x["file"], x["line"]) for x in sw if x["data"].get("broad")}
    r = ruff(repo, "ruff_exc")
    s110 = {(x["file"], x["line"]) for x in r if x["code"] == "S110"}
    ble = {(x["file"], x["line"]) for x in r if x["code"] == "BLE001"}
    sim105 = {(x["file"], x["line"]) for x in r if x["code"] == "SIM105"}
    pl = json.loads((OUT / repo / "pylint_exc" / "run1" / "pylint.json").read_text() or "[]")
    pl_broad = {(rel(repo, x["path"]), x["line"]) for x in pl if x["message-id"] in ("W0702", "W0718")}
    # a swallowed handler that ruff/pylint call broad, restricted to bodies that are only pass/...
    return dict(
        swallowed_vs_ruff_S110=stats(vp_all, s110),
        swallowed_broad_vs_ruff_S110=stats(vp_broad, s110),
        swallowed_broad_within_ruff_BLE001=dict(vp_broad=len(vp_broad), in_ble001=len(vp_broad & ble)),
        swallowed_broad_within_pylint_broad=dict(vp_broad=len(vp_broad), in_pylint=len(vp_broad & pl_broad), pylint_total=len(pl_broad)),
        counts=dict(S110=len(s110), BLE001=len(ble), SIM105=len(sim105), pylint_W0702_W0718=len(pl_broad), vibepiler_swallowed=len(vp_all), vibepiler_broad=len(vp_broad)),
        pools=dict(vibepiler_only=sorted(vp_all - s110), s110_only=sorted(s110 - vp_all)),
    )


# --- dependencies -----------------------------------------------------------
def norm(p: str) -> str:
    return re.sub(r"[-_.]+", "_", p.lower())


ROOT_MANIFEST = re.compile(r"declared in (pyproject\.toml|requirements[^ /]*\.txt)(?: |$)")


def deps(repo: str, d: dict) -> dict:
    p = OUT / repo / "deptry" / "run1" / "deptry.json"
    vp_unused, vp_undecl = set(), set()
    for x in d["diagnostics"]:
        if x["code"] in ("unused-dependency", "undeclared-dependency") and ROOT_MANIFEST.search(x["message"]):
            (vp_unused if x["code"] == "unused-dependency" else vp_undecl).add(norm(x["data"]["package"]))
    all_vp = Counter(x["code"] for x in d["diagnostics"] if x["code"] in ("unused-dependency", "undeclared-dependency"))
    if not p.exists():
        return dict(deptry="failed (no usable manifest for deptry)", vibepiler_root=dict(unused=sorted(vp_unused), undeclared=sorted(vp_undecl)), vibepiler_all=dict(all_vp))
    dt = json.loads(p.read_text())
    t_unused = {norm(x["module"]) for x in dt if x["error"]["code"] == "DEP002"}
    t_undecl = {norm(x["module"]) for x in dt if x["error"]["code"] in ("DEP001", "DEP003")}
    return dict(unused=stats(vp_unused, t_unused), undeclared=stats(vp_undecl, t_undecl), vibepiler_all=dict(all_vp),
                deptry_codes=dict(Counter(x["error"]["code"] for x in dt)),
                pools=dict(unused_vp_only=sorted(vp_unused - t_unused), unused_deptry_only=sorted(t_unused - vp_unused),
                           undecl_vp_only=sorted(vp_undecl - t_undecl), undecl_deptry_only=sorted(t_undecl - vp_undecl)))


# --- duplicates --------------------------------------------------------------
def overlaps(a: tuple, b: tuple) -> bool:
    return a[0] == b[0] and a[1] <= b[2] and b[1] <= a[2]


def duplicates(repo: str, d: dict) -> dict:
    nodes = {n["id"]: n for n in d["nodes"]}
    rng = lambda i: (nodes[i]["file"], nodes[i]["start_line"], nodes[i]["end_line"])
    exact, near = [], []
    for x in d["diagnostics"]:
        if x["code"] == "duplicate-function":
            ids = x["data"]["ids"]
            exact += [(rng(ids[0]), rng(j)) for j in ids[1:]]  # each copy against the first
        elif x["code"] == "near-duplicate-function":
            near.append((rng(x["data"]["ids"][0]), rng(x["data"]["ids"][1])))
    jr = json.loads((OUT / repo / "jscpd" / "run1" / "jscpd-report.json").read_text())
    jclones = [((rel(repo, c["firstFile"]["name"]), c["firstFile"]["start"], c["firstFile"]["end"]),
                (rel(repo, c["secondFile"]["name"]), c["secondFile"]["start"], c["secondFile"]["end"])) for c in jr["duplicates"]]
    pl = json.loads((OUT / repo / "pylint_dup" / "run1" / "pylint.json").read_text() or "[]")
    files = [str(p.relative_to(E / "repos" / repo)) for p in (E / "repos" / repo).rglob("*.py")]
    pclones = []
    for x in pl:
        if x["message-id"] != "R0801":
            continue
        locs = re.findall(r"==([\w.]+):\[(\d+):(\d+)\]", x["message"])
        rs = []
        for mod, a, b in locs:
            tail = mod.replace(".", "/")
            cand = [f for f in files if f.endswith(tail + ".py") or f.endswith(tail + "/__init__.py")]
            if cand:
                rs.append((min(cand, key=len), int(a) + 1, int(b)))
        pclones += [(rs[0], r) for r in rs[1:]]

    def covered(pair, clones):
        a, b = pair
        return any((overlaps(a, c1) and overlaps(b, c2)) or (overlaps(a, c2) and overlaps(b, c1)) for c1, c2 in clones)

    out = {}
    for tname, clones in (("jscpd", jclones), ("pylint_R0801", pclones)):
        out[tname] = dict(
            tool_clones=len(clones),
            exact_pairs=len(exact), exact_pairs_confirmed_by_tool=sum(covered(p, clones) for p in exact),
            near_pairs=len(near), near_pairs_confirmed_by_tool=sum(covered(p, clones) for p in near),
            tool_clones_covered_by_vibepiler=sum(covered(c, exact + near) for c in clones),
        )
    return out


# --- complexity --------------------------------------------------------------
def complexity(repo: str, d: dict) -> dict:
    fns = {(n["file"], n["start_line"]): n for n in d["nodes"] if n["kind"] in ("function", "method") and "complexity" in n.get("attrs", {})}
    by_q = defaultdict(list)
    for n in fns.values():
        by_q[(n["file"], n["qualname"])].append(n)
    radon = json.loads((OUT / repo / "radon_cc" / "run1" / "radon.json").read_text())
    pairs, seen = [], set()

    def walk(items, f):
        for it in items:
            if isinstance(it, dict) and it.get("type") in ("function", "method"):
                n = fns.get((f, it["lineno"]))
                if n and (f, it["lineno"]) not in seen:
                    seen.add((f, it["lineno"]))
                    pairs.append((it["complexity"], n["attrs"]["complexity"]))
                walk(it.get("closures", []), f)
            if isinstance(it, dict):
                walk(it.get("methods", []), f)

    for f, items in radon.items():
        if isinstance(items, list):
            walk(items, rel(repo, f))
    cx = json.loads((OUT / repo / "complexipy" / "run1" / "complexipy.json").read_text())
    cpairs = []
    for it in cx:
        cand = by_q.get((rel(repo, it["path"]), it["function_name"].replace("::", ".")), [])
        if len(cand) == 1:
            cpairs.append((it["complexity"], cand[0]["attrs"]["cognitive_complexity"]))
    return dict(
        radon_matched=len(pairs), radon_total_vp_functions=len(fns),
        radon_exact_agree=sum(a == b for a, b in pairs), radon_within_1=sum(abs(a - b) <= 1 for a, b in pairs),
        complexipy_matched=len(cpairs), complexipy_exact_agree=sum(a == b for a, b in cpairs),
        complexipy_vp_higher=sum(b > a for a, b in cpairs), complexipy_vp_lower=sum(b < a for a, b in cpairs),
    )


# --- cycles ---------------------------------------------------------------------
def tarjan(graph: dict) -> list[set]:
    index, low, stack, on, out, i = {}, {}, [], set(), [], [0]
    import sys
    sys.setrecursionlimit(100000)

    def visit(v):
        index[v] = low[v] = i[0]
        i[0] += 1
        stack.append(v)
        on.add(v)
        for w in graph.get(v, []):
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = set()
            while True:
                w = stack.pop()
                on.discard(w)
                comp.add(w)
                if w == v:
                    break
            if len(comp) > 1:
                out.append(comp)

    for v in list(graph):
        if v not in index:
            visit(v)
    return out


def cycles(repo: str, d: dict) -> dict:
    vp = [set(x["data"]["modules"]) for x in d["diagnostics"] if x["code"] == "import-cycle"]
    vf = set().union(*vp) if vp else set()
    out = dict(vibepiler_cycles=len(vp), vibepiler_largest=max(map(len, vp), default=0))
    for tool in ("ruff_graph", "ruff_graph_notc"):
        g = json.loads((OUT / repo / tool / "run1" / "graph.json").read_text())
        g = {rel(repo, k): [rel(repo, x) for x in v] for k, v in g.items()}
        sccs = tarjan(g)
        tf = set().union(*sccs) if sccs else set()
        out[tool] = dict(sccs=len(sccs), largest=max(map(len, sccs), default=0), files_in_cycles=stats(vf, tf),
                         identical_sccs=sum(1 for c in vp if c in sccs), vp_only_files=sorted(vf - tf), tool_only_files=sorted(tf - vf))
    return out


# --- novelty ---------------------------------------------------------------------
NOVEL = ["pass-through-wrapper", "thin-wrapper", "indirection-chain", "single-implementation", "single-subclass", "no-implementation",
         "class-as-function", "namespace-class", "single-use-helper", "deep-inheritance", "tiny-module", "unused-attribute", "test-only",
         "unreachable-module", "unreachable-symbol", "redundant-try", "log-and-reraise", "stub-function", "near-duplicate-function",
         "duplicate-function", "god-module", "hub-function", "central-function", "dead-branch", "unreachable-code", "dead-store", "unused-param",
         "undeclared-dependency", "unused-dependency", "import-cycle"]


def main() -> None:
    res, pools = {}, {}
    import sys
    for repo in sys.argv[1:] or REPOS:
        if not (OUT / repo / "vibepiler_O2" / "run1" / "ir.json").exists():
            continue
        d = ir(repo)
        dc, dpools = dead_code(repo, d)
        ex = exceptions(repo, d)
        dp = deps(repo, d)
        pools[repo] = dict(dead=dpools, exceptions=ex.pop("pools"), deps=dp.pop("pools", {}))
        res[repo] = dict(
            determinism=determinism(repo), dead_code=dc, local=local_findings(repo, d), exceptions=ex, deps=dp,
            duplicates=duplicates(repo, d), complexity=complexity(repo, d), cycles=cycles(repo, d),
            vibepiler_codes=dict(Counter(x["code"] for x in d["diagnostics"])),
            link_rate=d["meta"]["resolution"],
        )
        print(repo, "done", flush=True)
    (OUT / "analysis.json").write_text(json.dumps(res, indent=1, default=str))
    (OUT / "disagreements.json").write_text(json.dumps(pools, indent=1))


if __name__ == "__main__":
    main()
