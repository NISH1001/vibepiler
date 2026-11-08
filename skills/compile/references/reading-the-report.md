# Reading what vibepiler reports

## The size line

```
waste 1% (34 of 6326 lines; inferred 31, proven 3)   tangle 0.31 (high-complexity ×55, ...)   bloat 31%   live 96% (...)   [-O2, 54% linked]
```

- **waste**: the share of lines that could be deleted or folded, split by confidence. A plain percentage.
- **tangle**: structure harder than its job needs, from the findings' own numbers, each `(value − limit) / value`, combined as a noisy OR. A score in [0, 1], not a share of anything; the terms in brackets say what drives it.
- **bloat**: `1 − (1 − waste)(1 − tangle)`. 0 is clean.
- **live**: the share of definitions that something reads. High is good.
- **linked**: the share of call sites the linker resolved. A low figure means dynamic dispatch; dead-code results stay safe (they are conservative) but there are fewer of them, and "alive only by name" counts symbols kept because some call with an unknown receiver used that name.

## The metrics: two kinds of 0 to 1

- **Shares** print as percentages and read as plain ones: `typed 94.5%` is 94.5% of parameters and returns annotated; likewise `documented`, `waste`, `live`.
- **Excess scores** print as decimals: `cognitive`, `cyclomatic`, `mi`. Each is the share of complexity past the limit, weighted by lines: a function at twice the limit scores 0.5, one within it 0. So `cyclomatic 0.15` is **not** "15% of functions". The parts it mixes are printed beside it: how widespread (`91 of 570 functions, 40% of lines, over 10`) and how bad (`worst 35`). Quote those when explaining.

Limits are the published ones: cognitive 15 (SonarQube), cyclomatic 10 (McCabe), maintainability index below 20 (Visual Studio). Per function, findings and the map show the raw number in those units.

## The elimination plan

A table ordered by lines freed: `lines | action | confidence | finding | symbol | where`.

- **action**: `delete`, `fold` (merge or inline into another), `simplify`, `prune` (a dependency), `break` (a cycle).
- **confidence**:
  - `proven`: follows from the code alone (an unused import, a dead store).
  - `inferred`: from the program graph, sound for direct calls but blind to strings, reflection and framework wiring. Check before deleting.
  - `model`: from embeddings (semantic duplicates); a suggestion, never a fact.
- The plan sums exactly to the removable lines; one line is never charged twice.

## Findings by pass

Each pass names its compiler analogue (dead-code elimination, CSE, inlining). Every finding has a code (`dead-symbol`, `duplicate-function`, `high-cognitive-complexity`, ...), a severity, `file:line`, a message and a suggestion. `ir.json` holds the same findings under `diagnostics`, each with `data` (members, lines, confidence) for exact numbers.

## Things not to over-read

- A function flagged dead may be an entry point vibepiler does not know (a CLI hook, a plugin registry, a test fixture). Entry points found are listed in the report; add others with `entry_patterns` in config.
- Semantic duplicates at `-O3` are about meaning, not text: two functions that do the same job with different code. Read both before calling them copies.
- An empty plan with a low linked percentage means little was provable, not that the code is clean.
