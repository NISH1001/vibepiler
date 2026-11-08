"""Metric: the shape every score shares. A value in [0, 1], the terms behind it, how it prints, gates and moves.

Most metrics are additive: a unit's state is what it holds itself plus what its children hold, so one walker rolls
every one of them up function -> class -> module -> folder -> repo (`roll_up`). Two shapes cover the new ones:
`Share` (have / total: typed, documented) and `OverLimit` (how far past a fixed limit, line-weighted: cognitive,
cyclomatic, maintainability). A limit metric keeps its raw number (the worst one in the unit) beside the value.
Bloat is not additive (its tangle is a noisy-OR, its waste an allocation) and keeps its own roll-up.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, ClassVar, Literal, Self

from vibepiler.ir.model import NodeKind

if TYPE_CHECKING:
    from vibepiler.ir.model import Node
    from vibepiler.ir.program import Program

SCOPES = (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS)


class Metric(ABC):
    key: ClassVar[str]  # node attrs key; the repo value lives at program.metrics[f"{key}_score"]
    label: ClassVar[str]  # what the size line prints and --fail-on names
    better: ClassVar[Literal["low", "high"]]
    formula: ClassVar[str]

    @property
    @abstractmethod
    def value(self) -> float: ...

    @abstractmethod
    def terms(self) -> dict[str, float]: ...

    @abstractmethod
    def as_dict(self) -> dict: ...

    @classmethod
    @abstractmethod
    def of(cls, unit, default_loc: int = 0) -> Metric | None: ...  # noqa: ANN001 - a Node, a Program, or a stored dict

    @abstractmethod
    def __str__(self) -> str: ...

    @classmethod
    def stored(cls, unit) -> dict | None:  # noqa: ANN001 - a Node, a Program, or a stored dict
        """What a node's attrs or a program's metrics hold for this metric; None before its pass ran."""
        if isinstance(unit, dict):
            return unit or None
        if hasattr(unit, "attrs"):
            return unit.attrs.get(cls.key)
        return getattr(unit, "metrics", {}).get(f"{cls.key}_score")

    def trips(self, op: str, limit: float) -> bool:
        """The --fail-on test: `bloat>0.25`, `live<0.9`."""
        return self.value > limit if op == ">" else self.value < limit

    def moved(self, prev: Metric, eps: float = 0.005) -> bool:
        """The --baseline test: the value or any term moved by at least `eps`."""
        if abs(self.value - prev.value) >= eps:
            return True
        old = prev.terms()
        return any(abs(v - old.get(k, v)) >= eps for k, v in self.terms().items())


def roll_up(program: Program, cls: type[Metric], own: Callable[[Node], Metric]) -> None:
    """Sum `own` over every scope, store each total at `attrs[cls.key]`, then folders, source and tests at
    `program.metrics[f"{cls.key}_score"]`. Only modules in scope are scored; test modules are counted apart."""
    g = program.graph

    def visit(n: Node) -> Metric:
        total = own(n)
        for c in g.children(n.id):
            if c.kind in SCOPES:
                total = total + visit(c)
        n.attrs[cls.key] = total.as_dict()
        return total

    source, tests = cls(), cls()
    folders: dict[str, list] = defaultdict(lambda: [0, cls()])
    for m in sorted(g.nodes(NodeKind.MODULE), key=lambda m: m.file or ""):
        if not m.attrs.get("in_scope", True):
            continue  # read for its calls, not scored: the named paths are what this compile reports on
        total = visit(m)
        if m.attrs.get("is_test"):
            tests = tests + total
            continue
        source = source + total
        f = folders[str(PurePosixPath(m.file or m.name).parent)]
        f[0] += 1
        f[1] = f[1] + total
    rows = [{"path": p, "modules": k, **v.as_dict()} for p, (k, v) in folders.items()]
    worst_first = (lambda r: (r["value"], r["path"])) if cls.better == "high" else (lambda r: (-r["value"], r["path"]))
    program.metrics[f"{cls.key}_score"] = {**source.as_dict(), "folders": sorted(rows, key=worst_first), "tests": tests.as_dict(), "formula": cls.formula}


@dataclass(frozen=True)
class Share(Metric):
    """have / total; 1.0 when there is nothing to count. High is good."""

    noun: ClassVar[str]
    better: ClassVar[Literal["low", "high"]] = "high"

    have: int = 0
    total: int = 0

    @property
    def value(self) -> float:
        return self.have / self.total if self.total else 1.0

    def terms(self) -> dict[str, float]:
        return {"have": float(self.have), "total": float(self.total)}

    def as_dict(self) -> dict:
        return {"value": self.value, "have": self.have, "total": self.total}

    @classmethod
    def of(cls, unit, default_loc: int = 0) -> Self | None:  # noqa: ANN001, ARG003
        d = cls.stored(unit)
        return cls(have=d["have"], total=d["total"]) if d else None

    def __add__(self, other: Self) -> Self:
        return type(self)(self.have + other.have, self.total + other.total)

    def __str__(self) -> str:
        return f"{self.label} {self.value:.1%} ({self.have} of {self.total} {self.noun})"


@dataclass(frozen=True)
class OverLimit(Metric):
    """How far past a fixed limit a function's raw number goes, rescaled to [0, 1]: `(v − limit) / v` for a count
    (0.5 at twice the limit), `(limit − v) / limit` for a score where high is good. Above a function, the line-weighted
    mean over its functions. `worst` is the worst raw number inside the unit."""

    attr: ClassVar[str]  # the per-function raw number in node attrs
    raw_better: ClassVar[Literal["low", "high"]] = "low"
    better: ClassVar[Literal["low", "high"]] = "low"

    limit: float = 0.0
    loc: int = 0  # function lines measured
    weighted: float = 0.0  # sum of value × lines
    functions: int = 0
    over: int = 0
    worst: float | None = None

    @classmethod
    def rescale(cls, raw: float, limit: float) -> float:
        if cls.raw_better == "high":
            return max(0.0, (limit - raw) / limit) if limit else 0.0
        return max(0.0, (raw - limit) / raw) if raw else 0.0

    @classmethod
    def measured(cls, raw: float, limit: float, loc: int) -> Self:
        v = cls.rescale(raw, limit)
        return cls(limit=limit, loc=loc, weighted=v * loc, functions=1, over=int(v > 0), worst=raw)

    @property
    def value(self) -> float:
        return self.weighted / self.loc if self.loc else 0.0

    def terms(self) -> dict[str, float]:
        return {"over": float(self.over)}

    def as_dict(self) -> dict:
        return {"value": self.value, "limit": self.limit, "loc": self.loc, "weighted": self.weighted, "functions": self.functions, "over": self.over, "worst": self.worst}

    @classmethod
    def of(cls, unit, default_loc: int = 0) -> Self | None:  # noqa: ANN001, ARG003
        d = cls.stored(unit)
        return cls(**{k: d[k] for k in ("limit", "loc", "weighted", "functions", "over", "worst")}) if d else None

    def __add__(self, other: Self) -> Self:
        pick = max if self.raw_better == "low" else min
        worst = pick((w for w in (self.worst, other.worst) if w is not None), default=None)
        return type(self)(self.limit or other.limit, self.loc + other.loc, self.weighted + other.weighted, self.functions + other.functions, self.over + other.over, worst)

    def __str__(self) -> str:
        side = "below" if self.raw_better == "high" else "over"
        worst = f"; worst {self.worst:g}" if self.worst is not None else ""
        return f"{self.label} {self.value:.1%} ({self.over} of {self.functions} functions {side} {self.limit:g}{worst})"
