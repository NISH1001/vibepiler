# Vibepiler — a compiler for vibe-coded software

Date: 2026-11-02

## Intent

A compiler takes a program and, through a fixed pipeline of phases, turns it
into a representation it can reason about, then prunes and optimizes it.
Vibepiler does the same thing one level up: the "program" is a whole software
project (typically one produced quickly with AI assistance), and the output is
not machine code but a program graph plus a set of diagnostics that say where
the project can be pruned, simplified, or restructured.

Why: vibe-coded projects accumulate dead functions, duplicated helpers,
tangled imports, and modules nobody can map in their head. Software is
deterministic: every file parses into a known tree, every import resolves to a
known target, every call lands somewhere. That determinism means the
structure can be recovered mechanically and analyzed the way a compiler
analyzes a single program.

## The mapping

| Compiler phase | Vibepiler phase | What it does | Open-source tool |
|---|---|---|---|
| Lexing + parsing | `frontend` | Parse every source file into a concrete syntax tree | tree-sitter (`tree-sitter-language-pack`) |
| Symbol table + semantic analysis | `ir.builder` | Extract definitions, imports, calls, references per file; resolve names across files (the "linker") | tree-sitter queries + own resolver |
| Intermediate representation | `ir.graph` | A typed program graph: modules, classes, functions, variables, external packages; edges for contains / imports / calls / inherits / references | networkx |
| Optimization passes | `passes` | Dead code, inlining candidates, duplicates, import cycles, complexity, hotspots, unused imports, entry points + reachability | networkx algorithms + own passes |
| Code generation | `backend` | JSON IR, DOT, Mermaid, interactive HTML, Markdown, terminal report | graphviz-compatible DOT, cytoscape.js |

## Goals

- Language-agnostic core with per-language adapters. First adapters: Python and JavaScript/TypeScript (incl. JSX/TSX).
- Produce a program graph good enough to answer: what depends on what, what is reachable from the entry points, what is never used, what is duplicated, what is too complex, which modules are hubs.
- Every finding is a `Diagnostic` with a pass name, severity, file, line, and enough data to act on.
- Outputs are both machine-readable (JSON) and human-readable (report, graph pictures).
- Runs on a repo with no build step and no network.

## Non-goals (for now)

- Full type inference. Call resolution is name-based with scope rules; calls on
  values of unknown type are recorded as unresolved, not guessed.
- Rewriting code. Vibepiler reports; it does not edit.
- Languages beyond Python and JS/TS in the first version. The adapter interface
  is the extension point.

## Anti-goals

- Drowning the user in false positives. Dead-code detection must be
  conservative: exported symbols, entry points, tests, dunder methods, and
  methods whose name is called anywhere via attribute access are never flagged as dead.
- A giant config surface. Defaults should be right for a typical vibe-coded
  repo; a small `[tool.vibepiler]` table covers the rest.

## IR

Nodes: `module`, `class`, `function`, `method`, `variable`, `package` (external).
Node ids are stable strings derived from the file path and qualified name.

Edges (MultiDiGraph keyed by kind): `contains`, `imports`, `calls`,
`inherits`, `references`. Module-level code is attributed to the module node
itself, so a module can have `calls` edges (this is how `if __name__ ==
"__main__"` blocks and top-level `app.listen()` calls appear).

## Passes

| Pass | Compiler analogue | Finds |
|---|---|---|
| `entry-points` | program roots | main guards, CLI commands, web routes, tests, exports; computes reachability from roots |
| `dead-code` | dead-code elimination | symbols with no inbound calls/references and not reachable from any root |
| `unused-imports` | unused declarations | import bindings never referenced in their module |
| `cycles` | dependency analysis | strongly-connected components in the module import graph |
| `complexity` | control-flow analysis | cyclomatic complexity, length, nesting, parameter count over thresholds |
| `duplicates` | common-subexpression elimination | functions with identical or near-identical structure |
| `wrappers` | inlining | functions whose body is a single call to another function |
| `hotspots` | critical path | fan-in/fan-out, god modules, PageRank-central functions |
| `abstraction` | indirection removal | base classes with one implementation, one-method classes, single-caller helpers, pass-through chains |
| `bloat` | link-time pruning | declared dependencies never imported, imported packages never declared, try/except that only re-raises or swallows, redundant re-exports |

## Primary target: agent-introduced bloat

The user's stated goal is to find bloatware and the unnecessary complexity and
abstraction that coding agents introduce. The passes above are ordered by how
directly they serve that: `dead-code`, `abstraction`, `bloat`, `wrappers`, and
`duplicates` are the core; `cycles`, `complexity`, and `hotspots` give the map
needed to act on them safely.

## CLI shape

The CLI is deliberately compiler-shaped:

```
vibepiler compile <path> [-O0|-O1|-O2] [-Wall] [--emit json,dot,mermaid,html,md] [-o out/]
vibepiler graph   <path> [--level module|symbol] [--format dot|mermaid|json]
vibepiler paths   <path> --from <symbol> [--to <symbol>]
vibepiler passes
```

- `-O0`: build the graph only. `-O1`: cheap linear passes. `-O2` (default): all passes, including the quadratic duplicate detection.
- `-W`: minimum severity shown (`-Wall` shows `info`).
- Progress prints phase by phase, like a compiler driver.

## Decisions made here

- Python 3.12, managed with `uv`. CLI via `typer`, terminal output via `rich`.
- tree-sitter via `tree-sitter-language-pack` (prebuilt grammars, no compile step).
- `networkx.MultiDiGraph` as the IR container; views for module graph and call graph.
- Ignore rules: `.gitignore` (via `pathspec`) plus built-in defaults (`.venv`, `node_modules`, `dist`, `build`, `.git`, minified files).
- HTML output embeds the graph JSON and loads cytoscape.js from cdnjs; everything else is dependency-free text.

## Left to the builder

- Exact tree-sitter queries per language.
- Thresholds for complexity and duplicate size (configurable, documented defaults).
- Layout of the HTML viewer.
