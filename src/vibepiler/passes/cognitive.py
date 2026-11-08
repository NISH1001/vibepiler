"""cognitive: SonarSource's cognitive complexity (G. Ann Campbell, 2017), how hard a body is to follow.

Unlike cyclomatic complexity, nesting costs: an `if` inside a loop inside a `try` handler costs more than the same `if`
at the top. A structure (`if`, loop, `catch`/`except`, `switch`/`match`, ternary, a comprehension's `for`) costs 1 plus
how deep it sits and nests what it holds; `elif`, an `if`'s `else` and a comprehension's `if` cost 1 flat; a loop's or
a `try`'s `else` costs nothing; each run of one boolean operator costs 1; a jump to a label and a bare-name call to the
function itself cost 1. Lambdas nest without costing, and a comprehension's element nests under its `for`. A nested function the graph knows is a unit of its own;
an inline callback nests like a lambda. The Python rules agree with complexipy; the language tables live on each `LanguageSpec`.
"""

from __future__ import annotations

from tree_sitter import Node as TSNode

from vibepiler.frontend.languages.base import LanguageSpec
from vibepiler.frontend.loader import SourceFile

ALTERNATIVES = ("else_clause", "elif_clause")
TRANSPARENT = ("parenthesized_expression",)
LOOP_PARTS = ("for_in_clause", "if_clause")  # a comprehension's own clauses sit at its level; its element nests


def cognitive(name: str, body: TSNode, spec: LanguageSpec, sf: SourceFile, units: frozenset[tuple[int, int]]) -> int:
    """`units` are the spans of the functions that are units of their own; any other function (an inline callback)
    counts here, one level deeper, like a lambda."""
    walk = _Walk(name, spec, sf, units)
    walk.children(body, 0)
    return walk.total


class _Walk:
    def __init__(self, name: str, spec: LanguageSpec, sf: SourceFile, units: frozenset[tuple[int, int]]) -> None:
        self.name, self.spec, self.sf, self.units = name, spec, sf, units
        self.total = 0

    def children(self, n: TSNode, nest: int) -> None:
        for c in n.children:
            self.visit(c, nest)

    def visit(self, n: TSNode, nest: int) -> None:
        t, spec = n.type, self.spec
        if t in spec.function_types and (n.start_byte, n.end_byte) in self.units:
            return
        if t in spec.lambda_types or t in spec.function_types:
            self.children(n, nest + 1)
        elif t in spec.comprehension_types:
            for c in n.children:
                self.visit(c, nest if c.type in LOOP_PARTS else nest + 1)
        elif t in spec.cog_structures:
            self.structure(n, nest)
        elif t == "else_clause":
            self.else_clause(n, nest)
        elif t in spec.cog_flat:
            self.total += not self.case_guard(n)
            self.children(n, nest + 1)
        else:
            self.total += self.flat_cost(n)
            self.children(n, nest)

    def structure(self, n: TSNode, nest: int, flat: bool = False) -> None:
        """An `if`, loop, handler, switch or ternary: 1, plus its depth unless it is an `else if`; what it holds nests."""
        self.total += 1 if flat else 1 + nest
        for c in n.children:
            self.visit(c, nest if c.type in ALTERNATIVES else nest + 1)

    def else_clause(self, n: TSNode, nest: int) -> None:
        if n.parent is None or n.parent.type != "if_statement":
            self.children(n, nest)  # a loop's or a try's else: no cost, no nesting
            return
        inner = [c for c in n.named_children if c.type != "comment"]
        if len(inner) == 1 and inner[0].type == "if_statement":
            self.structure(inner[0], nest, flat=True)  # `else if`: one flat step, not a nested if
            return
        self.total += 1
        self.children(n, nest + 1)

    @staticmethod
    def case_guard(n: TSNode) -> bool:
        return n.type == "if_clause" and n.parent is not None and n.parent.type == "case_clause"

    def flat_cost(self, n: TSNode) -> int:
        """1 for the start of a run of one boolean operator, a jump to a label, or a bare-name call to itself."""
        spec, sf = self.spec, self.sf
        if (op := spec.bool_op(n, sf)) is not None:
            return int(self.run_starts(n, op))
        if spec.labeled_jump(n):
            return 1
        fn = n.child_by_field_name("function") if n.type in spec.call_types else None
        return int(fn is not None and fn.type == "identifier" and sf.text(fn) == self.name)

    def run_starts(self, n: TSNode, op: str) -> bool:
        p = n.parent
        while p is not None and p.type in TRANSPARENT:
            p = p.parent
        return p is None or self.spec.bool_op(p, self.sf) != op
