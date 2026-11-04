"""Metric: the shape every score shares. A value in [0, 1], the terms behind it, how it prints, gates and moves."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar, Literal


class Metric(ABC):
    key: ClassVar[str]  # node attrs key; the repo value lives at program.metrics[f"{key}_score"]
    label: ClassVar[str]  # what the size line prints
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

    def trips(self, op: str, limit: float) -> bool:
        """The --fail-on test: `bloat>0.25`, `live<0.9`."""
        return self.value > limit if op == ">" else self.value < limit

    def moved(self, prev: Metric, eps: float = 0.005) -> bool:
        """The --baseline test: the value or any term moved by at least `eps`."""
        if abs(self.value - prev.value) >= eps:
            return True
        old = prev.terms()
        return any(abs(v - old.get(k, v)) >= eps for k, v in self.terms().items())
