"""What the slow work is doing while it runs: one `vibepiler` logger, memory figures, timed steps, live counters.

The CLI prints these under the pass that is running (`-v` for every line; by default only the live counter, on one line
it rewrites). From Python, attach a handler to `logging.getLogger("vibepiler")`, or set `VIBEPILER_DEBUG=1` to print
everything to stderr. Memory comes from the standard library: the peak from `getrusage`, the current size from /proc
on Linux (None elsewhere).
"""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager

log = logging.getLogger("vibepiler")


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
    log.debug("%s  %.2f s  %s", step, time.perf_counter() - t, mem_text())


def progress(what: str, done: int, total: int, **info: object) -> None:
    """A live counter: the CLI rewrites one line with it; a plain handler sees an INFO record."""
    log.info("%s %d / %d", what, done, total, extra={"progress": {"what": what, "done": done, "total": total, **info}})


if os.environ.get("VIBEPILER_DEBUG"):
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("vibepiler: %(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.DEBUG)
