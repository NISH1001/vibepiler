"""Vibepiler: a compiler for vibe-coded software."""

import os
from typing import TYPE_CHECKING

from loguru import logger

__version__ = "0.4.2"

if not os.environ.get("VIBEPILER_DEBUG"):
    logger.disable("vibepiler")  # a library is quiet until asked: loguru.logger.enable("vibepiler"), the CLI, or VIBEPILER_DEBUG=1

if TYPE_CHECKING:
    from vibepiler.compiler import Compiler, compile  # noqa: F401 - `vibepiler.compile(source, ...)`, `vibepiler.Compiler(source)`


def __getattr__(name: str):  # noqa: ANN202 - PEP 562: `vibepiler.compile` without importing the driver at package import
    if name in ("compile", "Compiler"):
        from vibepiler import compiler

        return getattr(compiler, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted([*globals(), "compile", "Compiler"])
