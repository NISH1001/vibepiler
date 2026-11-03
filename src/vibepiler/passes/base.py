"""Pass interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from vibepiler.passes.diagnostic import Diagnostic, Severity

if TYPE_CHECKING:
    from vibepiler.ir.program import Program


class Pass(ABC):
    #: short kebab-case name used on the CLI
    name: str = ""
    #: one-line description of what it finds
    description: str = ""
    #: the compiler concept this pass mirrors
    analogue: str = ""
    #: 1 = cheap/linear (runs at -O1), 2 = expensive (runs at -O2 only)
    cost: int = 1
    #: passes that must have run first
    requires: tuple[str, ...] = ()

    @abstractmethod
    def run(self, program: Program) -> list[Diagnostic]: ...

    # helper for subclasses
    def diag(
        self,
        code: str,
        severity: Severity,
        message: str,
        *,
        node=None,  # noqa: ANN001
        file: str | None = None,
        line: int | None = None,
        suggestion: str = "",
        **data,  # noqa: ANN003
    ) -> Diagnostic:
        if node is not None:
            file = file or node.file
            line = line or node.start_line
            data.setdefault("qualname", node.qualname)
            data.setdefault("kind", node.kind.value)
        return Diagnostic(
            pass_name=self.name,
            code=code,
            severity=severity,
            message=message,
            file=file,
            line=line,
            node_id=node.id if node is not None else None,
            suggestion=suggestion,
            data=data,
        )
