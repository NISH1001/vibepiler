"""cognitive: SonarSource's cognitive complexity (G. Ann Campbell, 2017), how hard a body is to follow.

Unlike cyclomatic complexity, nesting costs: an `if` inside a loop inside a `try` handler costs more than the same `if`
at the top. A structure (`if`, loop, `catch`/`except`, `switch`/`match`, ternary, a comprehension's `for`) costs 1 plus
how deep it sits and nests what it holds; `elif`, an `if`'s `else` and a comprehension's `if` cost 1 flat; a loop's or
a `try`'s `else` costs nothing; each run of one boolean operator costs 1; a jump to a label and a bare-name call to the
function itself cost 1. Lambdas nest without costing, and a comprehension's element nests under its `for`. A nested function is a unit of its own, as for cyclomatic
complexity. The Python rules agree with complexipy; the language tables live on each `LanguageSpec`.
"""

from __future__ import annotations

from tree_sitter import Node as TSNode

from vibepiler.frontend.languages.base import LanguageSpec
from vibepiler.frontend.loader import SourceFile

ALTERNATIVES = ("else_clause", "elif_clause")
TRANSPARENT = ("parenthesized_expression",)


def cognitive(name: str, body: TSNode, spec: LanguageSpec, sf: SourceFile) -> int:
    total = 0

    def bool_run_starts(n: TSNode, op: str) -> bool:
        p = n.parent
        while p is not None and p.type in TRANSPARENT:
            p = p.parent
        return p is None or spec.bool_op(p, sf) != op

    def structure(n: TSNode, nest: int, flat: bool = False) -> None:
        nonlocal total
        total += 1 if flat else 1 + nest
        for c in n.children:
            visit(c, nest) if c.type in ALTERNATIVES else visit(c, nest + 1)

    def visit(n: TSNode, nest: int) -> None:
        nonlocal total
        t = n.type
        if t in spec.function_types:
            return
        if t in spec.lambda_types:
            for c in n.children:
                visit(c, nest + 1)
            return
        if t in spec.comprehension_types:
            for c in n.children:
                visit(c, nest if c.type in ("for_in_clause", "if_clause") else nest + 1)
            return
        if t in spec.cog_structures:
            structure(n, nest)
            return
        if t == "else_clause":
            if n.parent is None or n.parent.type != "if_statement":
                for c in n.children:  # a loop's or a try's else: no cost, no nesting
                    visit(c, nest)
                return
            inner = [c for c in n.named_children if c.type != "comment"]
            if len(inner) == 1 and inner[0].type == "if_statement":
                structure(inner[0], nest, flat=True)  # `else if`: one flat step, not a nested if
                return
            total += 1
            for c in n.children:
                visit(c, nest + 1)
            return
        if t in spec.cog_flat:
            if not (t == "if_clause" and n.parent is not None and n.parent.type == "case_clause"):  # a case guard is free
                total += 1
            for c in n.children:
                visit(c, nest + 1)
            return
        if (op := spec.bool_op(n, sf)) is not None and bool_run_starts(n, op):
            total += 1
        elif spec.labeled_jump(n):
            total += 1
        elif t in spec.call_types and (fn := n.child_by_field_name("function")) is not None and fn.type == "identifier" and sf.text(fn) == name:
            total += 1
        for c in n.children:
            visit(c, nest)

    for c in body.children:
        visit(c, 0)
    return total
