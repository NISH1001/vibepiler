"""What the slow work is doing while it runs: loguru records, memory figures, timed steps, live counters.

vibepiler is a library, so its records are off until asked for. The CLI turns them on under the pass that is running
(`-v` for every line; by default only the live counter, on one line it rewrites). From Python, call
`loguru.logger.enable("vibepiler")`, or set `VIBEPILER_DEBUG=1` to print everything to stderr. Memory comes from the standard library: the peak from `getrusage`, the current size from /proc
on Linux (None elsewhere).
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager

from loguru import logger as log


def memory() -> tuple[float | None, float]:
    """(current, peak) resident memory of this process, in GB; current is None where the OS does not say cheaply."""
    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (2**30 if sys.platform == "darwin" else 2**20)  # bytes on macOS, KB on Linux
    except ImportError:  # Windows
        peak = 0.0
    now = None
    try:
        with open("/proc/self/statm") as f:
            now = int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 2**30
    except (OSError, ValueError, AttributeError):
        pass
    return now, max(peak, now or 0.0)


def mem_text() -> str:
    now, peak = memory()
    return (f"{now:.2f} GB now, " if now is not None else "") + f"{peak:.2f} GB peak"


@contextmanager
def timed(step: str) -> Iterator[None]:
    """Log `step` with how long it took and the memory after it."""
    t = time.perf_counter()
    yield
    log.debug("{}  {:.2f} s  {}", step, time.perf_counter() - t, mem_text())


def progress(what: str, done: int, total: int, **info: object) -> None:
    """A live counter: the CLI rewrites one line with it; any other sink sees an INFO record with `progress` in extra."""
    log.bind(progress={"what": what, "done": done, "total": total, **info}).info("{} {} / {}", what, done, total)


@contextmanager
def verbose(on: bool = True) -> Iterator[None]:
    """vibepiler's records on for the duration, into whatever loguru sinks there are (stderr by default), then off
    again unless VIBEPILER_DEBUG asked for them always: `Compiler(..., verbose=True)`."""
    if not on:
        yield
        return
    log.enable("vibepiler")
    try:
        yield
    finally:
        if not os.environ.get("VIBEPILER_DEBUG"):
            log.disable("vibepiler")
