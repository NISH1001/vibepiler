---
name: compile
description: Run vibepiler, a compiler-style static analyzer for Python, JavaScript and TypeScript, on this codebase and read its report. It finds dead and unreachable code, duplicate and near-duplicate functions, pass-through wrappers, needless abstraction, unused imports and dependencies, import cycles, and complexity (cyclomatic, cognitive, maintainability), and scores waste, tangle and bloat. Use when the user asks how bloated or messy a repo or folder is, what is dead, duplicated or most complex, for an audit or code-health review, or invokes /vibepiler:compile with flags (-O0 to -O3, paths, --exclude) or words (semantic, quick, graph). Read-only; to remove what it finds, use the cleanup skill.
license: MIT
---

# vibepiler compile

vibepiler reads a codebase like a compiler: a frontend parses every file, a linker builds one program graph, and optimization passes report what could be deleted or simplified. It never edits code. Your job here is to run it well and explain what it found.

## 1. Choose the run

Read what the user asked, or the words after `/vibepiler:compile`. Real CLI flags pass through as they are; a few words map to flags:

| The user says | Use |
|---|---|
| nothing | the default: `-O2` on the whole repo |
| `semantic`, `deep`, "duplicates by meaning" | `-O3` (embedding tier; downloads a model once, slower) |
| `quick`, `fast` | `-O1` (cheap passes only) |
| `graph` | `-O0` (the program graph, no passes) |
| folder or file names (`backend`, `api/`) | name them after the source: `vibepiler compile . backend/ api/` |
| generated or vendored code to skip | `--exclude "**/gen/**"` (gitignore patterns, repeatable) |

Anything else that looks like a flag (`-O3`, `-Wall`, `--isolate`) goes to the command unchanged. The full list and what each does: [references/flags.md](references/flags.md).

## 2. Run it

Use an installed `vibepiler` if `command -v vibepiler` finds one; otherwise run it with uv, no install:

```bash
uvx --from git+https://github.com/NISH1001/vibepiler@v0.4.4 vibepiler compile . -R -q
# -O3 needs the semantic extra:
uvx --from "vibepiler[semantic] @ git+https://github.com/NISH1001/vibepiler@v0.4.4" vibepiler compile . -O3 -R -q
```

- `-R` prints the elimination plan, `-q` keeps the per-pass lines out of your context. Add `-v` only when a run is slow or surprising and you need to see why.
- The reports go to `vibepiler-out/` (`report.html`, `report.md`, `ir.json`, graphs); the last lines of the output list them. If `vibepiler-out/` is not gitignored, never stage it, and mention that to the user.
- A non-zero exit with an error line means the path or a flag is wrong: fix the command, do not guess at results.

## 3. Read the result

Read `vibepiler-out/report.md` rather than scrolling terminal output. Its sections, in order: Summary (with Metrics and What can go), Entry points, Hotspots, Complexity, Elimination plan, Findings by pass, and Concepts at `-O3`. How to read every number, and which ones not to over-read: [references/reading-the-report.md](references/reading-the-report.md).

## 4. Answer

Lead with what the user asked. For a general audit, keep it short:

1. **The size line**, in words: waste (share of lines that could go), tangle and bloat, live (share of definitions used), and the linked percentage.
2. **The top of the elimination plan**: 5 to 10 items, each with its symbol, `file:line`, the lines it frees, the action, and its confidence (`proven`, `inferred`, `model`).
3. **The worst complexity**: the few functions furthest past their limits, with the raw numbers.
4. **Where the full report is**: `vibepiler-out/report.html` opens in a browser.

Then offer the next step: removing the top items is the cleanup skill's job; a narrower question may suit another command ([references/other-commands.md](references/other-commands.md)).

## Rules

- Never edit code from this skill.
- Report the tool's numbers as it states them. An excess score such as `cyclomatic 0.15` is not "15% of functions"; say what it is (see the reading guide).
- Dead-code findings are conservative but name-based: a symbol used only through strings, reflection or a framework can look dead. Say `inferred` findings need a check before deleting.
- If the linked percentage is low (under about 50%), say that dynamic dispatch hides calls, so fewer things are reported dead, not that the code is clean.
