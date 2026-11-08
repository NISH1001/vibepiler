"""measures: the code-quality metrics reviewers know from SonarQube, radon and Visual Studio, each a `Metric` rolled
up function -> class -> module -> folder -> repo.

Three are a function's raw number against a fixed, configurable limit (`OverLimit`): cognitive complexity (15,
SonarQube's default), cyclomatic complexity (10, McCabe's), and the maintainability index (20, Visual Studio's poor
band). Two are shares (`Share`): how many parameter and return slots carry a type annotation, and how many public
functions, classes and modules carry a docstring or doc comment. The raw numbers come from `complexity`.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from vibepiler.ir.model import Node, NodeKind
from vibepiler.ir.program import Program
from vibepiler.passes.base import Pass
from vibepiler.passes.diagnostic import Diagnostic
from vibepiler.passes.metric import SCOPES, OverLimit, Share, roll_up

FUNCTIONS = (NodeKind.FUNCTION, NodeKind.METHOD)


class Cognitive(OverLimit):
    key: ClassVar[str] = "cognitive"
    label: ClassVar[str] = "cognitive"
    attr: ClassVar[str] = "cognitive_complexity"
    formula: ClassVar[str] = "cognitive = Σ lines × max(0, (c − limit) / c) / Σ lines, c = SonarSource cognitive complexity"


class Cyclomatic(OverLimit):
    key: ClassVar[str] = "cyclomatic"
    label: ClassVar[str] = "cyclomatic"
    attr: ClassVar[str] = "complexity"
    formula: ClassVar[str] = "cyclomatic = Σ lines × max(0, (c − limit) / c) / Σ lines, c = McCabe's 1 + decision points"


class Maintainability(OverLimit):
    key: ClassVar[str] = "maintainability"
    label: ClassVar[str] = "mi"
    attr: ClassVar[str] = "mi"
    raw_better: ClassVar[Literal["low", "high"]] = "high"
    formula: ClassVar[str] = "mi = Σ lines × max(0, (limit − MI) / limit) / Σ lines, MI = max(0, (171 − 5.2 ln V − 0.23 CC − 16.2 ln LOC) · 100/171)"


class Typed(Share):
    key: ClassVar[str] = "typed"
    label: ClassVar[str] = "typed"
    noun: ClassVar[str] = "parameters and returns annotated"
    formula: ClassVar[str] = "typed = annotated parameters and returns / parameters and returns (Python, TypeScript)"


class Documented(Share):
    key: ClassVar[str] = "documented"
    label: ClassVar[str] = "documented"
    noun: ClassVar[str] = "public functions, classes and modules documented"
    formula: ClassVar[str] = "documented = public units with a docstring or /** */ comment / public units"


LIMITS: dict[type[OverLimit], str] = {Cognitive: "cognitive", Cyclomatic: "complexity", Maintainability: "maintainability"}
METRICS = (Cognitive, Cyclomatic, Maintainability, Typed, Documented)


class MeasuresPass(Pass):
    name = "measures"
    description = "Cognitive and cyclomatic complexity, maintainability index, typed and documented shares, rolled up to the repo."
    analogue = "static metrics / size report"
    cost = 1
    requires = ("complexity",)

    def run(self, program: Program) -> list[Diagnostic]:
        g = program.graph
        th = program.config.thresholds
        nested = {n.id: sum(c.loc for c in g.children(n.id) if c.kind in SCOPES) for n in g.nodes(FUNCTIONS)}
        for cls, threshold in LIMITS.items():
            limit = float(getattr(th, threshold))

            def own(n: Node, cls: type[OverLimit] = cls, limit: float = limit) -> OverLimit:
                raw = n.attrs.get(cls.attr) if n.kind in FUNCTIONS else None
                return cls.measured(raw, limit, max(1, n.loc - nested[n.id])) if raw is not None else cls(limit=limit)

            roll_up(program, cls, own)
        roll_up(program, Typed, lambda n: self._typed(program, n))
        roll_up(program, Documented, lambda n: self._documented(program, n))
        return []

    @staticmethod
    def _typed(program: Program, n: Node) -> Typed:
        if n.kind not in FUNCTIONS or (d := program.symbol_def(n)) is None or d.node is None:
            return Typed()
        slots = program.spec_for(n).annotation_slots(d.node, program.facts[n.module].file)
        return Typed(*slots) if slots else Typed()

    @staticmethod
    def _documented(program: Program, n: Node) -> Documented:
        if n.name.startswith(("_", "#")):
            return Documented()
        spec = program.spec_for(n)
        if n.kind is NodeKind.MODULE:
            if n.id not in program.facts or (doc := spec.module_documented(program.facts[n.id].file)) is None:
                return Documented()
            return Documented(int(doc), 1)
        parent = program.graph.parent(n.id)
        if n.kind not in SCOPES or (parent is not None and parent.kind in FUNCTIONS):
            return Documented()  # a function's inner helpers are not its public surface
        if (d := program.symbol_def(n)) is None or d.node is None:
            return Documented()
        return Documented(int(spec.documented(d.node, program.facts[n.module].file)), 1)
