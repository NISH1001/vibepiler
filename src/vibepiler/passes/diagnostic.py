"""Diagnostics: what passes emit."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class Severity(IntEnum):
    INFO = 10
    WARNING = 20
    ERROR = 30

    @property
    def label(self) -> str:
        return self.name.lower()

    @classmethod
    def parse(cls, s: str) -> Severity:
        return cls[s.upper()]


@dataclass(slots=True)
class Diagnostic:
    pass_name: str
    code: str  # e.g. "dead-function", "import-cycle"
    severity: Severity
    message: str
    file: str | None = None
    line: int | None = None
    node_id: str | None = None
    suggestion: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def location(self) -> str:
        if self.file is None:
            return "<project>"
        return f"{self.file}:{self.line}" if self.line else self.file

    def to_dict(self) -> dict[str, Any]:
        return {
            "pass": self.pass_name,
            "code": self.code,
            "severity": self.severity.label,
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "node_id": self.node_id,
            "suggestion": self.suggestion,
            "data": self.data,
        }
