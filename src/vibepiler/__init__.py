"""Vibepiler: a compiler for vibe-coded software."""

__version__ = "0.1.1"


def __getattr__(name: str):  # noqa: ANN202 - PEP 562: `vibepiler.compile` without importing the driver at package import
    if name == "compile":
        from vibepiler.compiler import compile

        return compile
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
