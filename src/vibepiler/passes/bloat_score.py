"""bloat-score: the cost model. Prints like `size` does: waste, tangle, and one number for sorting and gating.

    bloat = 1 - (1 - waste)(1 - tangle)

waste   lines that could go / lines of the unit, counting proven and inferred findings only. Lines an embedding
        model thinks could merge (near and semantic duplicates) are kept apart as `model_loc`: shown, planned,
        never in the score. Each finding claims a line budget on the symbol it is about;
        budgets are allocated largest-first and never exceed the symbol's lines (or an enclosing class's or
        module's), so a function that is both dead and a stub is counted once and the plan sums exactly to
        the removable total. Every claim carries a confidence: proven (the pass showed it), inferred (reachability
        or structure, which depends on how much of the call graph linked) or model (an embedding said so).
tangle  structure harder than its job, each term derived from the finding's own numbers as (value - limit)/value,
        combined as a noisy OR. Containers take their children's tangle weighted by share of lines, then OR their
        own flags. Centrality is not tangle: a module everybody imports is normal.
Tests are scored but kept out of the repo and folder numbers. Runs last; at -O1 it reflects -O1 findings only.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath

from vibepiler.ir.model import EdgeKind, Node, NodeKind
from vibepiler.ir.program import Program
from vibepiler.passes.base import Pass
from vibepiler.passes.diagnostic import Diagnostic, Severity

FORMULA = "bloat = 1 - (1 - waste)(1 - tangle)"
BLOATED_MODULE = 0.4  # density at which a module gets its own finding...
BLOATED_LINES = 20  # ...or this many removable lines, whichever comes first
MAX_REPORTED = 10
BOUNDARY_LINES = 2  # a function boundary: the def line and the return
CLAUSE_LINES = 2  # an except/finally clause we do not have the span of

#: finding code -> what to do about it
ACTION: dict[str, str] = {
    "dead-symbol": "delete", "unreachable-symbol": "delete", "unreachable-module": "delete", "stub-function": "delete",
    "unused-import": "delete", "unused-dependency": "delete", "unimplemented-interface": "delete", "redundant-try": "delete",
    "duplicate-function": "fold", "near-duplicate-function": "fold", "semantic-duplicate": "fold",
    "pass-through-wrapper": "fold", "thin-wrapper": "fold", "indirection-chain": "fold", "single-use-helper": "fold",
    "class-as-function": "fold", "namespace-class": "fold", "single-implementation": "fold", "single-subclass": "fold",
    "tiny-module": "fold",
}
PROVEN = {"unused-import", "duplicate-function", "redundant-try", "unused-dependency"}
MODEL = {"near-duplicate-function", "semantic-duplicate"}
WHOLE = {"dead-symbol", "unreachable-symbol", "unreachable-module", "stub-function", "unimplemented-interface", "single-implementation", "pass-through-wrapper", "thin-wrapper", "indirection-chain"}
SHELL = {"class-as-function", "namespace-class", "single-subclass", "tiny-module"}  # the container minus what it contains
THRESHOLD_TERMS = {"high-complexity": ("complexity", "complexity"), "deep-nesting": ("nesting", "nesting"), "too-many-params": ("params", "params"), "long-function": ("loc", "function_loc")}
INHERITANCE_LIMIT = 3


def confidence_of(code: str) -> str:
    return "proven" if code in PROVEN else "model" if code in MODEL else "inferred"


def noisy_or(terms) -> float:  # noqa: ANN001
    p = 1.0
    for t in terms:
        p *= 1.0 - max(0.0, min(1.0, t))
    return 1.0 - p


def excess(value: float, limit: float) -> float:
    return max(0.0, (value - limit) / value) if value else 0.0


@dataclass(frozen=True)
class Bloat:
    """The score as a value. `waste` and `tangle` are the two numbers; `score` combines them for sorting and gating."""

    loc: int
    removable_loc: float  # proven + inferred lines that could go
    tangle: float
    model_loc: float = 0.0  # lines an embedding model thinks could merge; shown, never scored

    @property
    def waste(self) -> float:
        return min(1.0, self.removable_loc / self.loc) if self.loc > 0 else 0.0

    @property
    def score(self) -> float:
        return 1 - (1 - self.waste) * (1 - self.tangle)

    def as_dict(self) -> dict:
        return {"score": self.score, "waste": self.waste, "tangle": self.tangle, "removable_loc": self.removable_loc, "model_loc": self.model_loc}

    @classmethod
    def of(cls, unit, default_loc: int = 0) -> "Bloat | None":  # noqa: ANN001 - a Node, a Program, or a stored dict
        """The bloat of a node or a whole program after `bloat-score` has run; None before it."""
        d = unit if isinstance(unit, dict) else getattr(unit, "attrs", {}).get("bloat") if hasattr(unit, "attrs") else getattr(unit, "metrics", {}).get("bloat_score")
        if not d:
            return None
        loc = d.get("loc", getattr(unit, "loc", default_loc) if not isinstance(unit, dict) else default_loc)
        return cls(loc=loc, removable_loc=d["removable_loc"], tangle=d["tangle"], model_loc=d.get("model_loc", 0.0))

    def __str__(self) -> str:
        return f"waste {self.waste:.0%} ({self.removable_loc:.0f} of {self.loc} lines) · tangle {self.tangle:.2f} · bloat {self.score:.0%}"


def score(removable: float, loc: int, tangle: float) -> dict:
    return Bloat(loc=loc, removable_loc=min(float(removable), float(loc)), tangle=tangle).as_dict()


class BloatScorePass(Pass):
    name = "bloat-score"
    description = "The bloat score: how much of each function, class, module, folder and the repo is waste (lines that could go) or tangle (structure harder than its job)."
    analogue = "cost model / size report"
    cost = 1

    def run(self, program: Program) -> list[Diagnostic]:
        g = program.graph
        th = program.config.thresholds
        symbols = g.nodes((NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS))
        modules = g.nodes(NodeKind.MODULE)
        sym_index = {(n.file, n.qualname): n for n in symbols}
        mod_by_name = {n.name: n for n in modules}
        contained = {n.id: sum(c.loc for c in g.children(n.id) if c.kind in (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS)) for n in [*symbols, *modules]}

        def members(d: Diagnostic) -> list[Node]:
            out = []
            for q, loc in zip(d.data.get("members", []), d.data.get("locations", [])):
                n = sym_index.get((str(loc).rsplit(":", 1)[0], q))
                if n is not None:
                    out.append(n)
            return sorted(out, key=lambda n: (n.file or "", n.start_line or 0))

        # ----- claims: (lines asked, node, finding) -----
        claims: list[tuple[float, Node, Diagnostic]] = []
        tangles: dict[str, list[tuple[float, str]]] = defaultdict(list)
        in_cycle: dict[str, set[str]] = {}
        for d in program.diagnostics:
            if d.pass_name == self.name:
                continue
            code = d.code
            d.data.setdefault("action", ACTION.get(code, "simplify"))
            d.data.setdefault("confidence", confidence_of(code))
            n = g.node(d.node_id) if d.node_id and g.has_node(d.node_id) else None
            if code in WHOLE and n is not None:
                claims.append((float(n.loc), n, d))
            elif code in SHELL and n is not None:
                claims.append((float(max(1, n.loc - contained.get(n.id, 0))), n, d))
            elif code in ("duplicate-function", "near-duplicate-function", "semantic-duplicate"):
                ms = members(d) or ([n] if n else [])
                for m in ms[1:]:  # the first copy is the keeper
                    claims.append((float(m.loc), m, d))
            elif code == "single-use-helper" and n is not None:
                claims.append((float(min(n.loc, BOUNDARY_LINES)), n, d))
            elif code in ("redundant-try", "swallowed-exception", "log-and-reraise") and n is not None:
                claims.append((float(min(n.loc, CLAUSE_LINES)), n, d))
            elif code == "unused-import" and d.file and g.has_node(f"mod:{d.file}"):
                claims.append((1.0, g.node(f"mod:{d.file}"), d))
            elif code in THRESHOLD_TERMS and n is not None:
                key, limit = THRESHOLD_TERMS[code]
                raw = d.data.get(key, 0)
                value = float(len(raw)) if isinstance(raw, (list, tuple)) else float(raw or 0)  # too-many-params lists the names
                tangles[n.id].append((excess(value, float(getattr(th, limit))), code))
            elif code == "god-module" and n is not None:
                fan = float(min(n.attrs.get("fan_in", 0), n.attrs.get("fan_out", 0)))
                tangles[n.id].append((excess(fan, float(th.god_module_fan)), code))
            elif code == "import-cycle":
                ids = {mod_by_name[name].id for name in d.data.get("modules", []) if name in mod_by_name}
                for mid in ids:
                    in_cycle.setdefault(mid, set()).update(ids)
            elif code == "mixed-responsibilities" and n is not None:
                k = len(d.data.get("clusters", []))
                tangles[n.id].append((0.5 * (k - 1) / k if k else 0.0, code))
            elif code == "deep-inheritance" and n is not None:
                tangles[n.id].append((excess(float(d.data.get("depth", 0)), INHERITANCE_LIMIT), code))
        for mid, cyc in in_cycle.items():  # the share of a module's imports that stay inside its cycle
            outs = [e for e in g.out_edges(mid, EdgeKind.IMPORTS) if not e.attrs.get("implicit")]
            inside = sum(1 for e in outs if e.dst in cyc)
            tangles[mid].append((inside / len(outs) if outs else 0.0, "import-cycle"))

        # ----- allocate: largest claims first, never past a symbol's lines or its containers' -----
        ancestors: dict[str, list[str]] = {}

        def anc(node: Node) -> list[str]:  # enclosing symbols, then the module; any node kind (dead variables too)
            if node.id not in ancestors:
                chain, p = [], g.parent(node.id)
                while p is not None and p.kind != NodeKind.MODULE:
                    chain.append(p.id)
                    p = g.parent(p.id)
                ancestors[node.id] = [] if node.kind == NodeKind.MODULE else chain + ([node.module] if node.module and g.has_node(node.module) else [])
            return ancestors[node.id]

        used: dict[str, float] = defaultdict(float)  # proven + inferred lines, per node and per container
        model_used: dict[str, float] = defaultdict(float)  # model-confidence lines, kept apart
        loc_of: dict[str, float] = {}

        def loc(nid: str) -> float:
            if nid not in loc_of:
                loc_of[nid] = float(g.node(nid).loc)
            return loc_of[nid]

        claims.sort(key=lambda c: (-c[0], c[1].file or "", c[1].start_line or 0, c[2].code))
        for asked, n, d in claims:
            bucket = model_used if d.data["confidence"] == "model" else used
            room = loc(n.id) - used[n.id] - model_used[n.id]
            for a in anc(n):
                room = min(room, loc(a) - used[a] - model_used[a])
            got = max(0.0, min(asked, room))
            d.data["lines_freed"] = round(d.data.get("lines_freed", 0.0) + got, 1)
            if got <= 0:
                continue
            bucket[n.id] += got
            for a in anc(n):
                bucket[a] += got

        # ----- roll up -----
        tangle: dict[str, float] = {}

        def visit(n: Node) -> None:
            kids = [c for c in g.children(n.id) if c.kind in (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS)]
            for c in kids:
                visit(c)
            inherited = noisy_or(tangle[c.id] * (c.loc / n.loc if n.loc else 0.0) for c in kids)
            tangle[n.id] = noisy_or([inherited, *(t for t, _ in tangles.get(n.id, []))])
            n.attrs["bloat"] = score(used[n.id], n.loc, tangle[n.id])
            n.attrs["bloat"]["model_loc"] = min(float(n.loc), model_used[n.id])

        for m in modules:
            visit(m)

        source = [m for m in modules if not m.attrs.get("is_test")]
        tests = [m for m in modules if m.attrs.get("is_test")]

        def aggregate(ms: list[Node]) -> dict:
            loc = sum(m.loc for m in ms)
            rem = sum(used[m.id] for m in ms)
            t = noisy_or(tangle[m.id] * (m.loc / loc if loc else 0.0) for m in ms)
            return {"modules": len(ms), "loc": loc, **score(rem, loc, t), "model_loc": sum(model_used[m.id] for m in ms)}

        folders: dict[str, list[Node]] = defaultdict(list)
        for m in source:
            folders[str(PurePosixPath(m.file or m.name).parent)].append(m)
        repo = aggregate(source)
        repo["removable_loc"] = sum(used[m.id] for m in source)
        repo["model_loc"] = sum(model_used[m.id] for m in source)
        repo["tests"] = aggregate(tests)
        repo["folders"] = sorted(({"path": k, **aggregate(v)} for k, v in folders.items()), key=lambda r: (-r["removable_loc"], r["path"]))
        test_ids = {m.id for m in tests}
        plan = []
        by_conf: Counter = Counter()
        reasons: Counter = Counter()
        for d in program.diagnostics:
            lines = d.data.get("lines_freed", 0.0)
            node_mod = (g.node(d.node_id).module or d.node_id) if d.node_id and g.has_node(d.node_id) else (f"mod:{d.file}" if d.file else None)
            if lines and node_mod not in test_ids:
                plan.append({"lines": lines, "action": d.data["action"], "confidence": d.data["confidence"], "code": d.code, "qualname": d.data.get("qualname", ""), "file": d.file, "line": d.line, "node_id": d.node_id})
                by_conf[d.data["confidence"]] += lines
        for mid, terms in tangles.items():
            if mid not in test_ids and g.has_node(mid) and (g.node(mid).module or mid) not in test_ids:
                for t, code in terms:
                    if t > 0:
                        reasons[code] += 1
        plan.sort(key=lambda p: (-p["lines"], p["file"] or "", p["line"] or 0))
        res = g.meta.get("resolution", {})
        total_calls = res.get("calls_total", 0) or 0
        linked = res.get("calls_resolved", 0) + res.get("calls_external", 0) + res.get("calls_builtin", 0)
        repo.update(
            {
                "formula": FORMULA,
                "plan": plan,
                "waste_by_confidence": {k: round(v, 1) for k, v in sorted(by_conf.items())},
                "tangle_reasons": [{"code": c, "count": k} for c, k in reasons.most_common()],
                "modules_top": [{"id": m.id, "name": m.name, "file": m.file, "loc": m.loc, **m.attrs["bloat"]} for m in sorted(source, key=lambda m: (-used[m.id], -m.attrs["bloat"]["score"]))[:10]],
                "conditions": {
                    "opt_level": program.metrics.get("opt_level"),
                    "linked": round(linked / total_calls, 3) if total_calls else None,
                    "model": (program.metrics.get("semantic_duplicates") or {}).get("model"),
                },
            }
        )
        program.metrics["bloat_score"] = repo

        diags: list[Diagnostic] = []
        for m in sorted(source, key=lambda m: (-used[m.id], -m.attrs["bloat"]["score"])):
            b = m.attrs["bloat"]
            if len(diags) >= MAX_REPORTED or not (b["removable_loc"] >= BLOATED_LINES or (b["score"] >= BLOATED_MODULE and m.loc >= BLOATED_LINES)):
                continue
            causes: Counter = Counter()
            for p in plan:
                if p["file"] == m.file:
                    causes[p["code"]] += p["lines"]
            top = ", ".join(f"{c} {v:.0f}" for c, v in causes.most_common(3))
            diags.append(
                self.diag(
                    "bloated-module",
                    Severity.WARNING,
                    f"`{m.name}`: {b['removable_loc']:.0f} removable lines of {m.loc} ({b['waste']:.0%} waste" + (f": {top}" if top else "") + f"); tangle {b['tangle']:.2f}; bloat {b['score']:.0%}",
                    node=m,
                    suggestion="Work the elimination plan from the top: delete what is dead, fold what is duplicated or wrapped, then split what is tangled.",
                    action="fold",
                    confidence="inferred",
                    **{k: round(v, 3) for k, v in b.items()},
                )
            )
        return diags
