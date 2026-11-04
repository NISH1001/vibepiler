"""Vibepiler: a compiler for vibe-coded software."""

from typing import TYPE_CHECKING

__version__ = "0.1.1"

if TYPE_CHECKING:
    from vibepiler.compiler import compile  # noqa: F401 - `vibepiler.compile(source, ...)`


def __getattr__(name: str):  # noqa: ANN202 - PEP 562: `vibepiler.compile` without importing the driver at package import
    if name == "compile":
        from vibepiler.compiler import compile

        return compile
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted([*globals(), "compile"])
