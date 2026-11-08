"""halstead: Halstead's software science (1977) and the maintainability index built on it.

Operands are the identifier and literal tokens (a whole string is one operand), operators every other token: keywords,
punctuation, operator symbols. That is the classic token reading, the same in every language; radon's Halstead counts
only arithmetic, boolean and comparison operators, so its numbers are smaller than these. A nested function is a unit
of its own and is left out.

    volume V = N log2 n        difficulty D = n1/2 · N2/n2        effort E = D · V
    MI = max(0, (171 − 5.2 ln V − 0.23 CC − 16.2 ln LOC) · 100 / 171)        (Visual Studio's 0–100 variant)
"""

from __future__ import annotations

import math
from collections import Counter

from tree_sitter import Node as TSNode

from vibepiler.frontend.languages.base import LanguageSpec
from vibepiler.frontend.loader import SourceFile

SKIP = frozenset({"comment"})


def halstead(body: TSNode, spec: LanguageSpec, sf: SourceFile) -> dict:
    operators: Counter[str] = Counter()
    operands: Counter[str] = Counter()
    stack = [body]
    while stack:
        n = stack.pop()
        if n.type in SKIP or (n is not body and n.type in spec.function_types):
            continue
        if n.type in spec.leaf_types:
            operands[sf.text(n)] += 1
        elif n.child_count == 0:
            operators[n.type] += 1
        else:
            stack.extend(n.children)
    n1, n2, N1, N2 = len(operators), len(operands), sum(operators.values()), sum(operands.values())
    volume = (N1 + N2) * math.log2(n1 + n2) if n1 + n2 > 1 else 0.0
    difficulty = n1 / 2 * N2 / n2 if n2 else 0.0
    return {"n1": n1, "n2": n2, "N1": N1, "N2": N2, "volume": round(volume, 1), "difficulty": round(difficulty, 1), "effort": round(difficulty * volume, 1)}


def maintainability(volume: float, cyclomatic: int, loc: int) -> float:
    """Visual Studio's maintainability index, 0 (hard) to 100 (easy); below 20 is its warning band."""
    raw = 171 - 5.2 * math.log(max(volume, 1.0)) - 0.23 * cyclomatic - 16.2 * math.log(max(loc, 1))
    return round(max(0.0, raw * 100 / 171), 1)
