# Metrics round (v0.4.0) — design

## Intent

vibepiler reports two scores today (Bloat, Liveness). Code reviewers also expect the numbers SonarQube, radon and
Visual Studio print: cognitive and cyclomatic complexity, Halstead, the maintainability index, and how much of the code
is typed and documented. This round adds them as first-class `Metric`s so each one is computed per function, rolled up
through class → module → folder → repo, printed, gated (`--fail-on`), diffed (`--baseline`) and coloured on the
Semantic map, for Python, JavaScript and TypeScript alike.

## Goals

- One contract: every `Metric` has a `value` in [0, 1], a `better` direction, and keeps its raw numbers.
- One roll-up: a metric is an additive state (`__add__`, a zero); a shared walker sums a unit's own state with its
  children's and stores `attrs[key]` on every function, class and module, plus folders, source and tests at
  `program.metrics[f"{key}_score"]` in the shape Liveness uses today.
- New metrics: **cognitive** (SonarSource 2017), **cyclomatic** (McCabe), **maintainability** (Visual Studio MI),
  **typed** and **documented** (shares). Halstead volume/difficulty/effort are stored per function (they feed MI).
  Comment density is a per-module statistic, not a metric (no good/bad direction).
- Bloat's tangle uses cognitive complexity (limit 15) instead of cyclomatic (limit 10).
- Every new metric is a Semantic-map measure with a threshold slider and shows in tooltip and panel.

## Non-goals

- Security scanning (round B, v0.5.0). Git churn hotspots, coverage import (later).
- Running radon/complexipy at compile time. They are test-only oracles.

## Anti-goals

- Repo-relative normalisation (min–max). A value must mean the same thing in every repo and every run.
- Bloat or Liveness numbers moving because of the refactor. The only intended shift is tangle switching to cognitive.

## Rescaling to [0, 1]

| kind | metrics | value | raw kept |
|---|---|---|---|
| share | liveness, typed, documented | have / total (1.0 when total is 0) | the counts |
| count with a limit | cognitive (15), cyclomatic (10) | `(v − limit) / v` over the limit, else 0 | the count; worst |
| higher is better | maintainability (20) | `(20 − MI) / 20` below 20, else 0 | MI |

Above a function, a limit metric's value is the line-weighted mean of its functions' values: state
`(weighted, loc, functions, over, worst)`, `value = weighted / loc`. Prints e.g.
`cognitive 4.2% (12 of 469 functions over 15; worst 41)`.

## The metrics

- **Cyclomatic**: 1 + decision points, the existing `complexity` attr. The Python table stays aligned with radon
  (radon counts `with`, `assert`, comprehensions and boolean operators); a radon oracle test over the fixtures and
  vibepiler's own source decides any change. `high-complexity` stays a finding but no longer feeds tangle.
- **Cognitive** (SonarSource, "Cognitive Complexity", 2017): +1 for each `if`, ternary, `switch`/`match`, loop,
  `catch`/`except`, jump to a label, and each recursive call; +nesting level for those that nest; `else`/`elif`/
  `else if` +1 without nesting; each run of like boolean operators +1; nested functions/lambdas raise nesting.
  Shared walker in `passes/cognitive.py`, per-language node tables on `LanguageSpec`. New finding
  `high-cognitive-complexity` (threshold 15, config `cognitive`); it replaces `high-complexity` in bloat tangle.
- **Halstead**: operands are identifier and literal leaves, operators every other leaf token. `n1, n2, N1, N2`,
  volume `N log2 n`, difficulty `n1/2 · N2/n2`, effort `D·V`. Stored at `attrs["halstead"]`.
- **Maintainability**: `max(0, (171 − 5.2 ln V − 0.23 CC − 16.2 ln LOC) · 100 / 171)` per function, attrs `mi`.
- **Typed**: per function, annotated parameters (minus `self`/`cls`/`this`) + an annotated return, over the same
  count; Python and TypeScript only (plain `.js` defines nothing, so it does not dilute the share).
- **Documented**: public functions, methods, classes and modules (name not starting with `_`, not tests) with a
  docstring (Python) or a `/** … */` comment directly above (JS/TS).

## Outputs

- Terminal header stays waste/tangle/bloat/live. Markdown gets a metrics table (repo value + raw) and per-folder
  values. JSON carries everything through `attrs` and `program.metrics`.
- `--fail-on cognitive>0.05`, `--baseline` work for every metric through `all_metrics()`.
- Viewer: measures cognitive, cyclomatic (replaces "complexity"), maintainability, typed, documented added to the map
  select; health's "tangled" follows `high-cognitive-complexity`.

## Verification

- Liveness snapshot taken before the refactor must stay byte-identical; Bloat's existing snapshot changes only
  through the tangle switch, in its own commit.
- radon (cyclomatic, Halstead, MI) and complexipy (cognitive) as dev-only oracles on Python; SonarSource paper
  examples transcribed as JS/TS fixtures.

## Builder's calls (mine, not the user's)

- Halstead is raw-only (no accepted limit); it reaches [0, 1] through MI.
- Bloat keeps its own roll-up (noisy-OR tangle and line allocation are not additive); the shared walker serves the
  additive metrics and Liveness.

## What building it decided (2026-11-07)

- Cyclomatic follows radon exactly: comprehension `if` filters and a loop's or `try`'s `else` count; `with`, `match`, a
  wildcard `case _:` and case guards do not; an `assert` counts once and its condition is not looked into. Agrees with
  radon on every Python function in vibepiler, gpython and the fixtures.
- Cognitive follows complexipy's rules where they are rules (a comprehension's `for` +1 plus nesting, its `if` +1 flat,
  its element nested; lambdas nest; bare-name recursion +1, `self.f()` not). complexipy also skips some expressions
  (operands of `+`, starred arguments), so the oracle is: equal on hand-checked cases, never lower on whole repos.
  Nested functions are their own units (complexipy folds them into the outer function).
- Halstead is the classic token reading, not radon's (radon counts only arithmetic/boolean/comparison operators), so MI
  has formula tests instead of a radon oracle. At Visual Studio's limit 20, MI is 0 on vibepiler and 1% on gpython.
- Liveness moved onto the shared roll-up byte-identically (both fixture snapshots and all of gpython). Bloat keeps its
  own roll-up.
- Raw cognitive complexity lives at `attrs["cognitive_complexity"]`; the metric dicts at `attrs[key]` for keys
  cyclomatic, cognitive, maintainability, typed, documented (labels cyclomatic, cognitive, mi, typed, documented).
- akd-labs was dropped from verification at the user's request; vibepiler, gpython and the fixtures remain.
- Tangle switch: vibepiler bloat 13.5% → 12.8% (-O2), gpython 18.2% → 17.1%.
