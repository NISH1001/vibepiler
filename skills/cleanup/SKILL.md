---
name: cleanup
description: Shrink a codebase with vibepiler's elimination plan, one verified edit at a time. Runs vibepiler, takes the top proven or inferred item (dead code, unreachable modules, duplicate functions, pass-through wrappers, needless abstraction, unused imports or dependencies), checks the finding against the code, makes the edit, runs the tests, and re-runs vibepiler with --baseline to show what moved, until the plan is empty or something needs the user. Use when the user asks to clean up, de-bloat, remove dead or duplicated code, or apply vibepiler's findings, or invokes /vibepiler:cleanup, optionally with a scope (a folder, "dead code only", "imports only") or vibepiler flags.
license: MIT
---

# vibepiler cleanup

vibepiler's elimination plan is a to-do list: every item names the symbol, `file:line`, the action and how sure the tool is. This skill works through it as a fixpoint loop: edit, test, recompile, repeat. vibepiler never rewrites code; you do, and the next run checks the result.

## Before the loop

1. **Know how to test.** Find the project's test command (README, `pyproject.toml`, `package.json`, `Makefile`, CI config). If there is none, say so and ask whether to continue with vibepiler's re-run as the only check.
2. **Know the scope.** The words after `/vibepiler:cleanup` or in the request: a folder becomes `vibepiler compile . <folder>/`; "dead code only" or "imports only" limits which findings you take; vibepiler flags pass through.
3. **Start from a clean slate.** Note `git status`. Your edits should be the only changes, so the user can review them as one diff.
4. **Run vibepiler once and keep the baseline.** Use an installed `vibepiler` if there is one, otherwise:

   ```bash
   uvx --from git+https://github.com/NISH1001/vibepiler@v0.4.3 vibepiler compile . -R -q
   cp vibepiler-out/ir.json /tmp/vibepiler-start.json
   ```

   Read the elimination plan in `vibepiler-out/report.md`. Never stage `vibepiler-out/`.

## The loop

Take items from the top of the plan (largest first), one at a time:

1. **Only `proven` and `inferred` items.** `model` items (semantic duplicates from embeddings) are suggestions: list them at the end, do not apply them unless the user asks.
2. **Check the finding against the code before touching it.**
   - Dead or unreachable: search the whole repo for the name as text, not just as a call: string references, `getattr`, registries, decorators, config files, templates, exports in `__init__` or `index` files, and tests. A hit you cannot rule out means skip it.
   - Duplicate: read every copy; fold into the clearest one and point the callers at it.
   - Wrapper or abstraction: inline only when every caller can use the target directly.
   - A finding that is wrong or intentional: do not edit. Record it for the user, and suggest an `accept` entry (`accept = ["dead-symbol:pkg.module.name"]` under `[tool.vibepiler]`) so it stops being reported.
3. **Make the smallest edit that does what the action says.** Delete, fold or inline; remove imports the edit leaves unused. Nothing else in the same step.
4. **Run the tests.** If they fail because of your edit, revert that edit, record why, and move to the next item. Never change a test to make a deletion pass.
5. **Recompile against the previous run** and say what moved:

   ```bash
   cp vibepiler-out/ir.json /tmp/vibepiler-prev.json
   vibepiler compile . -R -q --baseline /tmp/vibepiler-prev.json
   ```

   One line to the user per item: what you removed, and waste and bloat before and after.

Stop when the plan has no `proven` or `inferred` items left in scope, when the same item comes back after you removed it, or when an item needs a judgment call (public API, a behaviour change, unclear ownership): stop there and ask.

## At the end

Report:

- what was removed, as a short list with lines freed;
- the start and end numbers: re-run once more with `--baseline /tmp/vibepiler-start.json`;
- what you skipped and why (failing tests, uses vibepiler cannot see, judgment calls), with the `accept` lines to add if the user agrees;
- the `model` suggestions you left alone.

## Rules

- Do not commit, push or open a pull request unless the user asks; leave the changes in the working tree.
- Tests run after every edit. A red test stops that item, never the safety check.
- Do not touch complexity findings (`high-complexity`, `high-cognitive-complexity`, `long-function`) unless the user asks: splitting a function is a refactor with judgment in it, not an elimination.
- How to read the plan and its confidence levels: see the compile skill's reading guide, or `report.md`'s own notes.
