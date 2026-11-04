# Liveness against waste: does liveness carry information bloat lacks?

Date: 2026-11-03. Measured on branch `feature/liveness` after bootstrap round five.

The design deferred folding liveness into the bloat formula until the two rankings were compared. If modules rank
the same way by waste (lines that could go) and by dead definitions, liveness is a density view of the same thing
and stays beside bloat. If the top tens disagree, it carries new information and a follow-up branch folds it.

## The rankings

**igaurab/gpython** (38 source modules, `-O2`): the top ten by removable lines and the top ten by dead definitions
share **9 of 10** modules, in nearly the same order.

| rank | by removable lines | by dead definitions |
|---|---|---|
| 1 | `manager.py`, 160 lines, waste 29.1% | `manager.py`, 20 dead of 198, live 89.9% |
| 2 | `imageview.py`, 108, 40.8% | `imageview.py`, 19 of 120, 84.2% |
| 3 | `variables.py`, 72, 12.6% | `variables.py`, 9 of 178, 94.9% |
| 4 | `app.py`, 50, 3.2% | `app.py`, 8 of 468, 98.3% |
| 5 | `envpicker.py`, 40, 6.8% | `envpicker.py`, 6 of 243, 97.5% |
| 6 | `kernel_side.py`, 36, 2.1% | `finder.py`, 6 of 123, 95.1% |
| 7 | `finder.py`, 26, 8.5% | `kernel_side.py`, 6 of 669, 99.1% |
| 8 | `themepicker.py`, 26, 18.1% | `images.py`, 5 of 164, 97.0% |
| 9 | `help.py`, 24, 55.8% | `themepicker.py`, 5 of 58, 91.4% |
| 10 | `notebook.py`, 20, 2.6% | `help.py`, 3 of 23, 87.0% |

**vibepiler** (58 modules, `-O3`): 3362 of 3362 definitions are live after round five, so the liveness ranking is
all ties and says nothing; waste is 18 lines spread over seven modules.

## Reading

On gpython, 84 of its 104 dead definitions are whole symbols, which waste already charges in full, and 11 more are
unused imports. The body-level kinds the liveness pass adds are 9 of the 104: 2 stores, 7 parameters, no attributes.
The repo is 98.3% live (6128 of 6232 definitions). So on a real agent-written repo the
two rankings agree because the same dead functions drive both. Liveness does not yet carry information bloat lacks,
by the decision rule in the design. **It stays beside bloat; no fold.**

What would change that: a repo where agents leave many locals and parameters behind inside live functions, which
moves liveness without moving waste much. Re-run this comparison on two or three more vibe-coded repos before
revisiting the fold.
