# Evaluation: vibepiler against the standard tools

vibepiler 0.4.5 (`d9ca2bb`) compared, check by check, with the tools people already use for each job, on pinned open-source repos. Every repo is pinned to a SHA. The harnesses live in `scripts/eval/` and rerun from the repo root with one command each; clones, tools and raw outputs go to `.eval/` (gitignored), or to `$VIBEPILER_EVAL_WORK`.

![Speed of vibepiler -O2, the dead-code tool alone and the whole toolchain run one after another, for Python and JS/TS repos; and, where vibepiler and the other tool disagree, the share of each side's findings that held up when read by hand](img/eval.png)

*Left and middle: warm median wall time on a log scale. Blue is vibepiler -O2. Green is the dead-code tool alone (vulture for Python, knip for JS/TS). Orange is every tool that covers the same checks, run one after another. Right: findings only one side reported, sampled and checked by reading the code at the pinned SHA. Regenerate with `uv run scripts/eval_figure.py`.*

| | Python | JS/TS |
|---|---|---|
| report | [python/RESULTS.md](python/RESULTS.md) | [javascript/RESULTS.md](javascript/RESULTS.md) |
| repos | requests, flask, httpx, vibepiler, gpython, rich, black, django (9k–410k lines) | zustand, release-it, taxonomy, excalidraw, and two vibe-coded apps, scuttlepay and tower-of-time (8k–136k lines) |
| compared with | vulture, deadcode, ruff, deptry, pylint, jscpd, radon, complexipy | knip, ts-prune, ts-unused-exports, depcheck, jscpd, madge, ESLint + sonarjs |
| harness | [scripts/eval/python/](../../scripts/eval/python/): `sh scripts/eval/python/run_all.sh` | [scripts/eval/javascript/](../../scripts/eval/javascript/): `sh scripts/eval/javascript/run.sh` |

Machine: Apple M3 Max, 14 cores, 36 GB, CPU only. The JS/TS timings ran while other benchmarks were using the same machine, so read them as approximate.

## What holds up

- **Determinism.** Identical diagnostics on every repeated run: 64 Python runs and 48 JS/TS runs.
- **Speed against the toolchain.** On repos up to about 120k lines, vibepiler -O2 is faster than running the tools it covers one after another: 0.6–0.8× their time for Python and 3–11× faster for JS/TS. Any single tool is faster at its one job; vulture is 4–6× faster, and ruff 20–300×.
- **Metrics.** Cyclomatic complexity matches radon on 99.9% of 31.5k Python functions, and cognitive complexity matches complexipy on 94%. Against ESLint and sonarjs, 60–87% are exact matches.
- **Cycles and swallowed exceptions.** Import cycles match madge's graph (Jaccard 0.96–1.00). Broad `except: pass` matches ruff S110 exactly on two of three repos.
- **Dead stores.** All 6 sampled dead stores that ruff does not look for (a value overwritten before it is read) were real.

## What does not, yet

- **Dead code where the tools disagree.** All 15 Python findings only vibepiler reported were wrong, and 14 of 15 JS/TS ones were. vulture was right on 4 of 15 of its own disagreements, and knip on 12 of 15. Most of vibepiler's misses are linker gaps: stdlib imports resolved to same-named project modules, exceptions used only in an `except` clause, re-exports through `export *` or chained `import *`, tsconfig path aliases, workspace packages, package.json `bin` entries. Both reports list each bug with file:line evidence; the Python ones also have a minimal reproduction in [scripts/eval/python/repro/](../../scripts/eval/python/repro/).
- **Large repos.** On django (410k lines), vibepiler takes 36.7 s with a 2.6 GB peak, where the toolchain takes 23.2 s. The time goes to parsing in one process, two walks over every function body (complexity, liveness) and a 129 MB `ir.json`.
- **Kinds no compared tool reports** (wrappers, single-implementation interfaces, class-as-function, single-use helpers, test-only code; about 1,800 in Python and 770 in JS/TS) have not been hand-checked yet.

## Safety note

The corpus code was never installed or run. One exception: knip loads each JS/TS repo's config files (`next.config.js`, vite and drizzle configs), which executes them. That was caught after a few runs; from then on knip and depcheck ran in a macOS sandbox with the network denied (`scripts/eval/javascript/nonet.sb`), and every reported number comes from those runs.

The semantic tier (`-O3`) has its own evaluation, which will be added here.
