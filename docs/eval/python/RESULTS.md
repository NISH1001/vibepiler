# Vibepiler vs the standard Python tools: speed, consistency, novelty

vibepiler 0.4.5 at `d9ca2bb`, run from its repo venv (`.venv/bin/vibepiler compile <repo> -O1|-O2 -q --lang python --emit json`; `uv run` adds ~0.2 s of startup, which is left out of the timings). Tool versions: vulture 2.16, deadcode 2.6.0, ruff 0.17.4, deptry 0.25.1, pylint 4.1.3, radon 6.0.1, complexipy 8.3.0, jscpd 4.0.5. Every tool was pointed at the whole repo checkout. ruff ran with `--isolated`, so repo configs and noqa-free defaults applied. vibepiler reads in-repo `[tool.vibepiler]` config, and only the vibepiler repo has one: it ignores `tests/fixtures/**`.
Reproduce with `sh scripts/eval/python/run_all.sh` from the repo root. Raw numbers are in `results.json`, and every tool output is written to `.eval/python/out/<repo>/<tool>/run<N>/` (or under `$VIBEPILER_EVAL_WORK`) (1.5 GB, not committed; rerun to regenerate).

**What was skipped and why:** creosote needs each project's virtualenv installed to map imports to distributions, and the corpus code is never installed here. pydeps is replaced by `ruff analyze graph` plus Tarjan SCC (`scripts/eval/python/analyze.py`), with and without TYPE_CHECKING imports. deptry failed on requests and django (no `[project]`/requirements manifest it accepts). deadcode produced no findings on black because parse errors in `tests/data` abort it.

## Corpus and machine
Machine: {'cpu': 'Apple M3 Max', 'cores': 14, 'ram_gb': 36, 'os': 'macOS-15.8.1-arm64-arm-64bit'}

| repo | sha | .py files | non-blank lines |
|---|---|---|---|
| requests | `0e322af877` | 36 | 9,163 |
| flask | `ab81496641` | 83 | 13,756 |
| httpx | `26d48e0634` | 60 | 14,374 |
| vibepiler | `d9ca2bbfce` | 99 | 11,521 |
| gpython | `c55944f5c4` | 90 | 26,202 |
| rich | `43d3b04725` | 190 | 33,512 |
| black | `1b2427a2b7` | 284 | 118,887 |
| django | `2d4add11fd` | 2788 | 410,361 |


All repos are pinned: shallow clone at the tag (vibepiler at the local HEAD SHA). LOC means non-blank `.py` lines over the whole checkout, tests and data included, and every tool saw the same tree. black's 119k includes ~70k lines of formatter test data.

## 1. Speed
`scripts/eval/python/bench.py` runs each (repo, tool) once cold (first invocation, tool caches off: `ruff --no-cache`, fresh complexipy cache dir) and then 3 times warm, and reports the warm median. Peak RSS comes from `/usr/bin/time -l`. Runs are sequential on an idle machine.

### Wall time, warm median (s)

| tool | requests | flask | httpx | vibepiler | gpython | rich | black | django |
|---|---|---|---|---|---|---|---|---|
| vibepiler_O1 | 0.62 | 1.10 | 1.11 | 1.45 | 2.88 | 2.16 | 2.72 | 36.04 |
| vibepiler_O2 | 0.65 | 1.13 | 1.13 | 1.55 | 3.03 | 2.26 | 2.79 | 36.65 |
| vulture60 | 0.16 | 0.26 | 0.26 | 0.34 | 0.67 | 0.48 | 1.19 | 5.68 |
| vulture100 | 0.16 | 0.24 | 0.26 | 0.37 | 0.69 | 0.49 | 1.14 | 5.56 |
| deadcode | 0.28 | 0.43 | 0.49 | 0.70 | 1.33 | 0.99 | 2.32 | 13.39 |
| ruff_unused | 0.03 | 0.03 | 0.03 | 0.04 | 0.05 | 0.04 | 0.06 | 0.13 |
| ruff_exc | 0.02 | 0.03 | 0.03 | 0.03 | 0.04 | 0.04 | 0.05 | 0.09 |
| ruff_graph | 0.02 | 0.03 | 0.03 | 0.04 | 0.04 | 0.04 | 0.04 | 0.12 |
| deptry | 0.10 | 0.13 | 0.12 | 0.13 | 0.19 | 0.17 | 0.16 | 0.24 |
| pylint_dup | 1.09 | 2.54 | 1.62 | 1.75 | 4.18 | 2.68 | 4.56 | 24.39 |
| pylint_exc | 1.35 | 2.61 | 2.44 | 1.75 | 4.38 | 2.98 | 4.63 | 19.84 |
| jscpd | 0.46 | 0.71 | 0.94 | 1.71 | 2.51 | 1.55 | 1.42 | 13.49 |
| radon_cc | 0.19 | 0.24 | 0.23 | 0.31 | 0.46 | 0.38 | 0.86 | 3.28 |
| complexipy | 0.06 | 0.07 | 0.07 | 0.08 | 0.07 | 0.07 | 0.13 | 0.18 |

### Peak RSS (MB, max over runs)

| tool | requests | flask | httpx | vibepiler | gpython | rich | black | django |
|---|---|---|---|---|---|---|---|---|
| vibepiler_O1 | 102 | 141 | 136 | 157 | 270 | 228 | 337 | 2587 |
| vibepiler_O2 | 103 | 141 | 138 | 160 | 276 | 233 | 341 | 2624 |
| vulture60 | 30 | 30 | 28 | 31 | 50 | 46 | 136 | 139 |
| vulture100 | 33 | 31 | 28 | 31 | 56 | 42 | 152 | 140 |
| deadcode | 48 | 43 | 36 | 43 | 74 | 54 | 254 | 197 |
| ruff_unused | 29 | 27 | 28 | 30 | 43 | 35 | 78 | 77 |
| ruff_exc | 28 | 27 | 28 | 28 | 39 | 33 | 70 | 77 |
| ruff_graph | 24 | 26 | 26 | 27 | 32 | 30 | 67 | 67 |
| deptry | 25 | 34 | 35 | 38 | 43 | 38 | 86 | 53 |
| pylint_dup | 114 | 148 | 110 | 107 | 190 | 176 | 335 | 792 |
| pylint_exc | 111 | 153 | 135 | 105 | 187 | 165 | 327 | 696 |
| jscpd | 168 | 183 | 296 | 343 | 360 | 353 | 310 | 1410 |
| radon_cc | 45 | 41 | 39 | 43 | 63 | 50 | 157 | 179 |
| complexipy | 34 | 36 | 37 | 37 | 48 | 45 | 72 | 179 |

### Cold (first) run wall (s)

| tool | requests | flask | httpx | vibepiler | gpython | rich | black | django |
|---|---|---|---|---|---|---|---|---|
| vibepiler_O1 | 0.65 | 1.12 | 1.11 | 1.49 | 2.95 | 2.19 | 2.74 | 36.39 |
| vibepiler_O2 | 0.64 | 1.13 | 1.13 | 1.50 | 3.04 | 2.33 | 2.92 | 35.03 |
| vulture60 | 0.17 | 0.26 | 0.26 | 0.38 | 0.69 | 0.47 | 1.19 | 5.72 |
| vulture100 | 0.16 | 0.24 | 0.25 | 0.37 | 0.65 | 0.51 | 1.12 | 5.30 |
| deadcode | 0.28 | 0.45 | 0.49 | 0.74 | 1.41 | 1.00 | 2.28 | 14.11 |
| ruff_unused | 0.04 | 0.04 | 0.04 | 0.04 | 0.06 | 0.09 | 0.06 | 0.20 |
| ruff_exc | 0.03 | 0.03 | 0.03 | 0.03 | 0.03 | 0.04 | 0.05 | 0.09 |
| ruff_graph | 0.02 | 0.03 | 0.03 | 0.04 | 0.04 | 0.04 | 0.04 | 0.14 |
| deptry | 0.13 | 0.11 | 0.11 | 0.14 | 0.21 | 0.19 | 0.20 | 0.29 |
| pylint_dup | 1.17 | 2.48 | 1.63 | 1.74 | 4.87 | 3.67 | 4.83 | 24.41 |
| pylint_exc | 1.35 | 2.59 | 2.47 | 1.75 | 4.28 | 2.95 | 4.58 | 19.87 |
| jscpd | 0.77 | 0.71 | 0.90 | 1.77 | 2.41 | 1.59 | 1.35 | 13.78 |
| radon_cc | 0.21 | 0.24 | 0.25 | 0.31 | 0.48 | 0.43 | 1.56 | 3.66 |
| complexipy | 0.10 | 0.09 | 0.08 | 0.10 | 0.15 | 0.07 | 0.17 | 0.21 |

### Time vs size

| repo | non-blank lines | vibepiler -O1 s | -O2 s | -O2 kLOC/s | toolchain sum (lean) s | toolchain sum (pylint) s | -O2 / lean |
|---|---|---|---|---|---|---|---|
| requests | 9,163 | 0.62 | 0.65 | 14.1 | 1.05 | 3.02 | 0.62x |
| flask | 13,756 | 1.10 | 1.13 | 12.2 | 1.49 | 5.92 | 0.76x |
| httpx | 14,374 | 1.11 | 1.13 | 12.7 | 1.71 | 4.83 | 0.66x |
| vibepiler | 11,521 | 1.45 | 1.55 | 7.4 | 2.68 | 4.46 | 0.58x |
| gpython | 26,202 | 2.88 | 3.03 | 8.7 | 4.02 | 10.07 | 0.75x |
| rich | 33,512 | 2.16 | 2.26 | 14.9 | 2.78 | 6.89 | 0.81x |
| black | 118,887 | 2.72 | 2.79 | 42.6 | 3.91 | 11.68 | 0.71x |
| django | 410,361 | 36.04 | 36.65 | 11.2 | 23.22 | 53.95 | 1.58x |

The "lean" toolchain is vulture + ruff (F401/F841) + ruff (BLE001/S110/SIM105) + ruff analyze graph + deptry + jscpd + radon cc + complexipy. The "pylint" toolchain swaps jscpd for pylint R0801 and adds pylint W0702/W0718.

**Reading:**
- vibepiler -O2 beats running the lean toolchain one tool after another on every repo up to black's size (0.6–0.8x). On django (410k LOC, 2.8k files) it is 1.6x slower than the lean sum and still faster than the pylint sum (36.7 s vs 54 s).
- It is always slower than any single tool doing its pass: vulture is 4–6x faster, ruff 20–300x.
- Throughput is about 7–15k LOC/s. black's 42k LOC/s is inflated by large, trivial data files.
- -O2 costs only 1–5% more than -O1. Duplicate detection is cheap; parse and link dominate.
- Memory is the weak spot: 2.6 GB peak on django, against 0.14 GB for vulture and 1.4 GB for jscpd.

## 2. Consistency

### Determinism
Every repo got 4 runs of -O1 and 4 of -O2. Hashing the sorted diagnostics gave **identical output in all 64 runs**.

### Agreement (normalized keys: file + name for definitions, params and dependencies; file + line for stores, unreachable code and except clauses)
| comparison (both / vibepiler-only / tool-only, Jaccard) | requests | flask | httpx | vibepiler | gpython | rich | black | django |
|---|---|---|---|---|---|---|---|---|
| dead defs vs vulture 60 | 17/10/12 (J 0.44) | 24/30/205 (J 0.09) | 0/4/3 (J 0.00) | 0/0/32 (J 0.00) | 41/28/147 (J 0.19) | 47/37/2 (J 0.55) | 70/54/273 (J 0.18) | 313/822/453 (J 0.20) |
| dead defs vs deadcode | 18/9/14 (J 0.44) | 24/30/205 (J 0.09) | 0/4/3 (J 0.00) | 0/0/32 (J 0.00) | 41/28/147 (J 0.19) | 47/37/3 (J 0.54) | 0/124/0 (J 0.00) | 313/822/456 (J 0.20) |
| dead vars/attrs vs vulture 60 | 36/11/12 (J 0.61) | 20/12/17 (J 0.41) | 1/5/55 (J 0.02) | 0/0/3 (J 0.00) | 2/16/46 (J 0.03) | 26/131/33 (J 0.14) | 62/26/311 (J 0.15) | 1043/61/1068 (J 0.48) |
| vulture 60 vs deadcode (defs) | 29/0/3 (J 0.91) | 229/0/0 (J 1.00) | 3/0/0 (J 1.00) | 32/0/0 (J 1.00) | 188/0/0 (J 1.00) | 49/0/1 (J 0.98) | 0/343/0 (J 0.00) | 766/0/3 (J 1.00) |
| unused imports vs ruff F401 (excl. __init__) | 23/12/2 (J 0.62) | 0/11/0 (J 0.00) | 0/10/0 (J 0.00) | 0/0/2 (J 0.00) | 10/12/1 (J 0.43) | 1/7/0 (J 0.12) | 54/35/2 (J 0.59) | 2/145/0 (J 0.01) |
| dead-store vs ruff F841 | 0/4/0 (J 0.00) | – | 0/1/0 (J 0.00) | 0/0/6 (J 0.00) | 2/4/1 (J 0.29) | 9/8/0 (J 0.53) | 50/28/108 (J 0.27) | 0/120/0 (J 0.00) |
| unused-param vs vulture args | 3/7/3 (J 0.23) | 2/16/10 (J 0.07) | 0/10/0 (J 0.00) | 0/0/3 (J 0.00) | 0/13/3 (J 0.00) | 1/9/11 (J 0.05) | 0/44/56 (J 0.00) | 8/237/36 (J 0.03) |
| unreachable-code vs vulture | – | 0/0/1 (J 0.00) | 4/0/0 (J 1.00) | 0/0/1 (J 0.00) | – | – | 2/0/15 (J 0.12) | – |
| broad swallowed vs ruff S110 | – | 5/0/2 (J 0.71) | – | 0/0/1 (J 0.00) | 6/0/0 (J 1.00) | 0/0/2 (J 0.00) | 1/0/4 (J 0.20) | 9/0/0 (J 1.00) |
| unused deps vs deptry (root manifest) | deptry failed | 0/0/1 (J 0.00) | 0/2/1 (J 0.00) | 0/0/1 (J 0.00) | 0/0/2 (J 0.00) | 1/1/0 (J 0.50) | 0/0/1 (J 0.00) | deptry failed |
| undeclared deps vs deptry | deptry failed | 6/10/6 (J 0.27) | 2/18/0 (J 0.10) | 0/0/3 (J 0.00) | 5/3/3 (J 0.46) | 5/1/2 (J 0.62) | 9/16/9 (J 0.27) | deptry failed |
| files in cycles vs ruff graph (no TYPE_CHECKING) | – | 13/6/3 (J 0.59) | 0/0/2 (J 0.00) | 0/0/7 (J 0.00) | 0/0/10 (J 0.00) | 38/1/15 (J 0.70) | 0/0/6 (J 0.00) | 135/34/74 (J 0.56) |
| exact dup pairs confirmed by jscpd | 1/13 | 0/27 | 11/104 | 1/17 | 7/47 | 4/53 | 6/36 | 94/2438 |
| jscpd clones covered by vibepiler | 1/1 | 0/5 | 18/66 | 1/10 | 6/32 | 4/46 | 3/109 | 87/567 |
| exact dup pairs confirmed by pylint R0801 | 0/13 | 3/27 | 0/104 | 0/17 | 13/47 | 1/53 | 0/36 | 39/2438 |
| cyclomatic = radon (matched fns) | 525/525 | 735/735 | 866/866 | 957/957 | 2246/2246 | 1427/1428 | 1150/1160 | 23656/23682 |
| cognitive = complexipy (matched fns) | 586/599 | 794/838 | 1032/1054 | 854/1067 | 1712/2105 | 1568/1656 | 1778/1882 | 27152/27781 |
| determinism O1/O2 (4 runs each) | identical | identical | identical | identical | identical | identical | identical | identical |
- **Dead code.** Agreement with vulture is low: corpus-wide 512 shared, 985 vibepiler-only and 1,127 vulture-only definitions (Jaccard 0.20). vulture and deadcode agree with each other almost perfectly (J 0.9–1.0), so vibepiler is the outlier. That would be fine if the disagreements were vibepiler catching more, but the sample below says they are not.
- **Unused imports.** Outside `__init__.py`, vibepiler reports 232 imports ruff F401 does not, while ruff reports 7 that vibepiler does not. A textual triage of all 408 vibepiler unused-imports (`scripts/eval/python/import_fp_patterns.py`) found 109 whose name is used in an `except` clause in the same file, 31 re-imported by another project module, and 17 used in a subscripted base class. All three are confirmed false positives (bugs C, A and D below). The triage is in `out/import_fp_patterns.json`.
- **Dead stores.** vibepiler finds what F841 does not look for: values overwritten before any read. All 6 of a random sample of django's 120 vibepiler-only dead stores were true positives (labels in `scripts/eval/python/labels.json`).
- **Complexity.** Cyclomatic complexity equals radon on 99.9% of the 31.5k matched functions. Cognitive complexity equals complexipy on 94%. Where they differ, vibepiler is usually higher, as its README says.
- **Cycles.** Agreement is mixed. Some differences are by design: vibepiler ignores function-local imports, ruff's graph does not (httpx `_urls`/`_utils`). Others are a vibepiler bug: stdlib imports resolved to same-named project modules (bug E) add 6 false files to flask's cycle and pull django's whole `gis/gdal` stack into a 161-module SCC.
- **Duplicates.** vibepiler's AST-shape groups barely overlap with token-window clones. jscpd confirms 125 of 2,735 exact pairs; most vibepiler pairs are short functions under jscpd's 50-token minimum. vibepiler covers 120 of 836 jscpd clones; most jscpd clones span statement windows, not whole functions. The tools measure different things.
- **Swallowed exceptions.** On broad `except: pass`, vibepiler matches ruff S110 wherever both fire (flask, gpython, django J=1.0). It misses S110 hits at module level (only function bodies are scanned) and also reports narrow `except ImportError: pass` at info level.

## 3. Novelty and disagreement

### Finding kinds no compared tool covers (counts per repo)
| novel kind | requests | flask | httpx | vibepiler | gpython | rich | black | django | total |
|---|---|---|---|---|---|---|---|---|---|
| pass-through-wrapper | 1 | 0 | 0 | 0 | 0 | 5 | 0 | 22 | 28 |
| thin-wrapper | 0 | 1 | 4 | 1 | 8 | 1 | 3 | 112 | 130 |
| single-implementation | 0 | 2 | 0 | 0 | 0 | 2 | 0 | 41 | 45 |
| single-subclass | 2 | 7 | 0 | 0 | 0 | 0 | 2 | 95 | 106 |
| unimplemented-interface | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 8 | 10 |
| class-as-function | 6 | 7 | 4 | 3 | 1 | 10 | 40 | 109 | 180 |
| namespace-class | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 2 | 3 |
| single-use-helper | 1 | 1 | 10 | 21 | 45 | 7 | 15 | 30 | 130 |
| tiny-module | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 10 | 12 |
| test-only | 3 | 6 | 13 | 0 | 22 | 19 | 2 | 750 | 815 |
| stub-function | 7 | 7 | 8 | 0 | 3 | 5 | 136 | 207 | 373 |
| god-module | 0 | 1 | 1 | 1 | 1 | 4 | 1 | 24 | 33 |
| hub-function | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 2 | 3 |
| dead-branch | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 1 |
| bloated-module | 8 | 10 | 8 | 1 | 10 | 10 | 10 | 10 | 67 |
| deep-inheritance | 0 | 14 | 15 | 0 | 0 | 0 | 0 | 2831 | 2860 |
Also partly novel: `unreachable-symbol` (transitively dead: 471, which vulture cannot express), `unreachable-module` (219), overwrite-before-read `dead-store`, and per-folder/PEP 723 dependency scoping. `deep-inheritance` is noise on django: 2,831, almost all test classes under Django's TestCase chain. pylint and ruff have rules close to some of these (R0901 too-many-ancestors, TRY203 useless-try-except), but they were not part of the compared set. The novel kinds were not hand-verified.

### Hand verification of disagreements (labels and reasons in `scripts/eval/python/labels.json`, sample in `out/samples.json`, round-robin over repos, seeded)

| pool | n | TP | FP | unclear | precision on decided |
|---|---|---|---|---|---|
| dead defs, vibepiler-only | 15 | 0 | 15 | 0 | **0%** |
| dead defs, vulture-only | 15 | 4 | 9 | 2 | 31% |
| unused deps, vibepiler-only | 3 | 0 | 2 | 1 | 0% |
| unused deps, deptry-only | 6 | 1 | 5 | 0 | 17% |
| undeclared deps, vibepiler-only | 10 | 1 | 6 | 3 | 14% |
| undeclared deps, deptry-only | 10 | 1 | 9 | 0 | 10% |
| dead-store, vibepiler (django, not a disagreement pool) | 6 | 6 | 0 | 0 | 100% |

Where the tools disagree on dead code, **neither side is reliable, and vibepiler's extra findings were all wrong.** Of the 15 FPs, 7 are the bugs below. The other 8 are dynamic dispatch and framework conventions that a name-based tool cannot see: a getattr visitor, `import_module` plus getattr, a Django template-tag library, a stdlib duck-typed protocol, the celery CLI, and example scripts without a `__main__` guard. vulture's errors are of a different kind: pytest autouse fixtures, decorator-registered handlers, and re-exported public API. vibepiler gets every one of those right. On dependencies, deptry's misses come from mapping distribution names to import names without an installed env; vibepiler's come from manifest parsing.

## Suspected vibepiler bugs (each reproduced on a minimal project in `scripts/eval/python/repro/minirepro*/`)

| id | bug | evidence |
|---|---|---|
| A | Names a non-`__init__` module imports and other project modules re-import (`from .compat import quote`) are reported `unused-import` | requests `src/requests/compat.py:69-81` (quote, urlparse...); used by utils.py:34, models.py:29 |
| B | A fallback `def` in `try: from x import f / except ImportError: def f(...)` (or an `if platform` redefinition) is `dead-symbol` | requests `adapters.py:63` SOCKSProxyManager (used at :282); `utils.py:114` proxy_bypass |
| C | A bare `except Name:` (no `as`, not a tuple) is not a reference, so the imported exception is `unused-import` | 109 of 408 unused-imports; e.g. django `core/handlers/asgi.py:12` RequestDataTooBig (used at :287), flask `sessions.py:10` BadSignature |
| D | A subscripted base `class C(Base[T])` is not linked: the base import is "unused", the class is not seen as having an external base, so framework hooks are dead | gpython: every `ModalScreen[...]` subclass's compose/on_mount/on_key/action_* and their callees (e.g. `imageview.py:194` _step); rich `prompt.py:353,358` Confirm overrides; flask `sessions.py:6,12` |
| E | An absolute stdlib import resolves to any project module with the same name (`import json` -> `flask/json`, `from datetime import date` -> `django/db/models/functions/datetime.py`, `import html` -> itself) | ~530 such edges (`out/stdlib_shadow.json`); false import cycles in flask (`config.py`, `logging.py`) and django (161-module SCC) |
| F | A call to an inherited classmethod on a subclass (`Sub.as_view()`) links to `Base.as_view` and leaves no reference to `Sub`, so `Sub` is dead and the base becomes `unreachable-symbol` | flask `tests/test_views.py:18-248` (11 classes); repro `minirepro3/main.py` (even in the `__main__` guard) |
| G | Public API re-exported through chained `from .x import *` with `__all__` is not treated as public | httpx `_transports/mock.py` MockTransport, asgi/wsgi transports, `_exceptions.py:193` CloseError |
| I | Virtual dispatch reaches overrides in direct subclasses only, not grandchildren | black `trans.py:1497` StringSplitter.do_transform unreachable, 7 callees reported `unreachable-symbol` ("only used by dead code" while the caller itself is not reported); requests `tests/test_requests.py:2215` |
| J | pip-compile lock files (`requirements/*.txt`, `examples/*/requirements.txt`) are read as manifests, flooding unused/undeclared findings | flask: 74 unused-dependency + 127 undeclared, mostly transitive pins such as sphinxcontrib-* |
| K | Distribution-to-import mapping misses `markdown-it-py` -> `markdown_it` | rich: `markdown-it-py` "never imported" (used in `rich/markdown.py:6`) |
| L | A wildcard specifier `httpcore==1.*` is not parsed, so the dependency reads as undeclared | httpx `pyproject.toml:32` |
| M | The stdlib check runs on the lowercased name, so `cProfile` becomes the undeclared dependency `cprofile` | gpython `scripts/bench.py:12`; cause at `passes/bloat.py:120` + `manifests.py:84,200` |
| N | `test_requirements.txt` is not recognized as a manifest (the glob is `requirements*.txt`) | black: pytest "undeclared" |

Smaller or design-level issues:
- Example scripts with top-level code and no `__main__` guard are `unreachable-module` (rich `examples/`: 38 modules).
- Methods of any class with an external base are never reported. This hides a genuinely dead `RequestsCookieJar._find` that vulture finds.
- `deep-inheritance` counts Django test-case chains.
- Swallowed exceptions are scanned only inside functions.

## Honest bottom line
- **vibepiler wins on:** one deterministic pass that is faster than the equivalent toolchain up to about 100k LOC; exact cyclomatic and near-exact cognitive numbers; overwrite-before-read dead stores that ruff misses; a set of structural findings (wrappers, single-implementation, class-as-function, test-only, transitive dead code) no compared tool produces; and dependency scoping (per-folder manifests, PEP 723) that deptry lacks.
- **vibepiler loses on:** memory at scale (2.6 GB on django), and precision on dead code and unused imports where it disagrees with vulture and ruff. Several of the bugs above (C, D, E, F) are linker gaps, not inherent limits of a name-based analysis, and fixing them should remove most of the vibepiler-only noise.
