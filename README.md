# vibepiler

**A compiler for vibe-coded software.**

A compiler takes one program, parses it, builds a symbol table, lowers it to an
intermediate representation, runs optimization passes over that IR, and emits
output. Vibepiler does the same thing to a *whole project*: every file is
parsed with tree-sitter, every definition, import, call and reference is
linked into one program graph, and a set of passes then finds what a compiler
would prune: dead code, duplicated bodies, pass-through wrappers, cycles,
one-implementation interfaces, declared-but-unused dependencies, swallowed
exceptions, stubs.

It exists because software produced quickly with coding agents accumulates
exactly that kind of bloat: helpers nobody calls, abstractions with a single
implementation, three layers of indirection around one `requests.get`,
dependencies added for one experiment. Software is deterministic, so this
structure can be recovered mechanically and analyzed like a program.

| Compiler phase | Vibepiler | Built on |
|---|---|---|
| lexing + parsing | `frontend/` parses every source file into a concrete syntax tree | [tree-sitter](https://tree-sitter.github.io/) via `tree-sitter-language-pack` |
| symbol table + semantic analysis | `ir/builder.py` extracts facts per file and links them across files (the "linker") | tree-sitter trees + a scope-aware name resolver |
| intermediate representation | `ir/graph.py`: a typed multigraph of modules, classes, functions, methods, variables, packages | [networkx](https://networkx.org/) |
| optimization passes | `passes/`: dead code, duplicates, wrappers, cycles, complexity, hotspots, abstraction, bloat | networkx algorithms + per-pass AST analysis |
| code generation | `backend/`: JSON IR, DOT, Mermaid, interactive HTML, Markdown, terminal | Graphviz-compatible DOT, [cytoscape.js](https://js.cytoscape.org/) |

Languages: **Python**, **JavaScript/TypeScript** (incl. JSX/TSX). Adding a language means adding one adapter file.

## Install & run

```bash
uv sync                                    # or: pip install -e .
uv run vibepiler compile path/to/project   # full pipeline, writes ./vibepiler-out/
open vibepiler-out/report.html             # interactive graph + findings
```

Try it on itself:

```bash
uv run vibepiler compile . --ignore "tests/fixtures/**"
```

## The CLI (deliberately compiler-shaped)

```
vibepiler compile <path> [-O0|-O1|-O2] [-W all|warning|error] [--emit json,dot,mermaid,html,md] [-o out/]
                         [--only pass,pass] [--skip pass] [--ignore "glob,glob"] [--lang python,typescript]
                         [--fail-on warning|error] [-q]
vibepiler graph   <path> [--level module|symbol] [--format dot|mermaid|json] [--packages]
vibepiler paths   <path> --from <symbol> [--to <symbol>] [--depth N]
vibepiler passes
```

- `-O0` builds the graph only. `-O1` runs the linear passes. `-O2` (default) adds the quadratic ones (duplicate detection).
- `-W` is the minimum severity printed to the terminal. `-Wall` shows `info` findings. Everything is always in `ir.json`/`report.md`.
- `--fail-on warning` makes it usable as a CI gate.
- `paths` prints execution pathways: the call tree from a symbol, or every shortest path between two symbols
  (`vibepiler paths . --from app.main.cli --to app.utils._impl`).

What a run looks like:

```
vibepiler 0.1.0 compiling /work/myapp
[1/4] frontend  parsed 212 files (python: 180, typescript: 32)
[2/4] semantic  212 modules, 1840 symbols, 6210 edges; 71% of 9300 call sites linked
      pass entry-points       0 findings  12 ms
      pass dead-code         63 findings   4 ms
      pass unused-imports    41 findings   1 ms
      pass cycles             3 findings   2 ms
      ...
[3/4] optimize  -O2: 241 findings (12 error, 119 warning, 110 info)
[4/4] emit      wrote graph.dot, graph.mmd, ir.json, report.html, report.md to vibepiler-out

warning dead-code/dead-symbol  src/app/utils.py:14: function `unused_fn` is dead: never called or referenced
        Delete it (8 lines).
warning abstraction/single-implementation  src/app/core.py:6: abstract class `Base` has exactly one implementation (`Impl`)
        Delete `Base` and use `Impl` directly; add the interface back when a second implementation exists.
...
                          what can go
┏━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ category              ┃ found                              ┃ action  ┃
┡━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━┩
│ dead code             │ 63 symbols, 1,204 lines            │ delete  │
│ duplicate functions   │ 9 groups (310 lines), 14 near pairs│ merge   │
│ pass-through wrappers │ 17 (2 chains)                      │ inline  │
│ needless abstraction  │ 11                                 │ flatten │
│ dependency bloat      │ 4 unused, 1 undeclared             │ prune   │
│ defensive boilerplate │ 6 swallowed, 9 redundant try, 5 stubs │ remove │
└───────────────────────┴────────────────────────────────────┴─────────┘
```

## Outputs

| File | What it is |
|---|---|
| `report.html` | Self-contained interactive viewer organized by compiler phase: **Parse** (every file and its parse status), **Link** (call-resolution breakdown, the binding table of each module, member names that could only be matched dynamically), **Program graph** (a fixed layered layout where execution flows down from the entry points, unreachable modules parked in their own zone; double-click a module to open its symbols, click a node and *Trace* to watch execution animate outward from it; toggles only show/hide, they never move nodes), **Passes** (every finding with its suggestion, click to jump into the graph). |
| `report.md` | Summary, "what can go" table, entry points, hotspots, complexity, then every finding grouped by pass with a suggestion. |
| `ir.json` | The whole IR: nodes (with all computed attributes), edges, diagnostics, metrics. Feed it to anything. |
| `graph.dot`, `graph-symbols.dot` | Graphviz: `dot -Tsvg vibepiler-out/graph.dot -o graph.svg`. Cycle edges are red, unreachable nodes dashed, flagged nodes get a colored border. |
| `graph.mmd` | Mermaid flowchart of the module graph, pasteable into GitHub issues / READMEs. |

## How the semantic graph is built

This is the part that corresponds to a compiler's front half. It is name-based
and deliberately conservative: when it cannot prove where a call goes, it says
so rather than guessing.

1. **Discover** (`frontend/loader.py`): walk the tree, honor `.gitignore` plus built-in ignores (`node_modules`, `.venv`, `dist`, minified files, ...), pick a grammar by extension.
2. **Parse**: tree-sitter gives a full concrete syntax tree per file, error-tolerant, no build step required.
3. **Extract facts** (`frontend/languages/<lang>.py`): one adapter per language walks the tree once and records, per file:
   - *definitions* (module, class, function, method, variable) with qualified names like `Impl.run`, lines, decorators, bases, parameters, export status;
   - *imports* in every form (`import a.b`, `from .x import y as z`, `import x from './y'`, `require()`, re-exports);
   - *call sites* as attribute chains (`requests.get`, `self.run`, `utils.helper`) with the **enclosing scope** (which function they live in; module-level code is attributed to the module itself, which is how `if __name__ == "__main__":` and `app.listen()` show up);
   - *references* (non-call uses: decorators, arguments, JSX elements, base classes, type annotations);
   - *entry hints* (main guards, `@app.get`, `@click.command`, `test_*`, Next.js route files, ...).
4. **Index modules**: each file gets a canonical module name (`app.core`, `src/utils/math`) plus aliases for the ways vibe-coded repos actually import things (`src.app.core`, `@/utils/math`).
5. **Create nodes**: every definition becomes a node; `contains` edges nest module → class → method.
6. **Resolve imports**: each import is tried against the module index (submodule first, then member of a package); on failure it becomes an external `package` node. This produces the per-module **binding table**: `helper → symbol app.utils.helper`, `utils → module app.utils`, `requests → package requests`, `impl_alias → symbol app.utils._impl`. Function-local imports get their own scope. Python parent packages are added as implicit imports (importing `app.utils` executes `app/__init__.py`).
7. **Resolve inheritance**: base-class chains go through the same binding table → `inherits` edges.
8. **Resolve calls and references**: for a chain like `a.b.c(...)` inside scope `S` of module `M`:
   - `self`/`cls`/`this` → the enclosing class, then member lookup through the class and its resolved bases;
   - a name defined in `S` or in `M` → that symbol;
   - a module binding → extend the dotted path, take the longest prefix that is a real module, then look the rest up as symbols (following package `__init__` re-exports);
   - a member binding → first as a submodule, then as a symbol of the target module;
   - a package binding → `calls` edge to the package node, with the member path (`requests.get`) kept on the edge.
   Exact hits become `calls`/`references` edges. Partial hits (`Config.value(...)`, `module.CONST`) become `references`. Calls on values of unknown type (`impl.run()` where `impl` is a local) are **not guessed**; their member names go into `graph.meta["used_member_names"]`, which later passes use to stay conservative (a method named `run` is never reported dead while *anything* in the project calls `.run()`).
9. **Attach metadata**: resolution statistics, parse errors, import usage flags per module.

The whole thing is one `networkx.MultiDiGraph` with `contains`, `imports`, `calls`, `inherits`, `references` edges; `graph.module_graph()` and `graph.call_graph()` give you the views the passes (and you) work on.

What it does **not** do: type inference. Method calls on local variables, dynamic dispatch, `getattr`, string-based imports and framework magic stay unresolved. The report prints the resolution rate so you know how much of the call graph is proven; on typical Python code it is 50–80 % of call sites, with most of the rest being method calls on locals.

## How it "optimizes"

Vibepiler never edits your code. Each pass is the analysis half of a compiler optimization; the transformation half is you (or your agent) acting on the diagnostics. Every finding has a pass, a code, a severity, a location and a concrete suggestion.

| Pass | Compiler analogue | What it finds | Severity |
|---|---|---|---|
| `entry-points` | program roots / reachability | Roots: main guards, CLI commands, routes, tasks, tests, framework hooks, exports of entry modules, your `entry_patterns`. Then forward reachability over calls/references/imports/inherits. Marks every node `reachable` and `is_root`. | (metrics only) |
| `dead-code` | dead-code elimination | Symbols with no inbound call/reference and not reachable from any root (`dead-symbol`); symbols used only by dead code (`unreachable-symbol`); JS exports nothing imports (`unused-export`); modules nothing imports or runs (`unreachable-module`). Never flags dunders, roots, methods whose name is called anywhere, or overrides of external base classes. | warning / info |
| `unused-imports` | unused declarations | Import bindings never used in their file (respects `__all__`, `__init__`/`index` re-export files, `import x as x`). | info |
| `cycles` | dependency analysis | Strongly connected components of the module import graph, with the weakest edge to break. | warning (error for 6+ modules) |
| `complexity` | control-flow analysis | Cyclomatic complexity, length, nesting, parameter count per function; attaches the numbers to every function node. | warning / info |
| `wrappers` | inlining | Functions whose entire body is one call to another project function (`pass-through-wrapper` when the arguments are forwarded unchanged, `thin-wrapper` otherwise) and chains of them (`indirection-chain`). Skips decorated functions, roots, and overrides. | warning / info |
| `hotspots` | critical-path analysis | Fan-in/fan-out per module, god modules, PageRank-central functions, hub functions. | warning / info |
| `abstraction` | devirtualization / indirection removal | Abstract classes with exactly one implementation (`single-implementation`), base classes with one subclass and no other use, abstract classes with none, classes with a single real method (`class-as-function`), all-static classes (`namespace-class`), helpers called exactly once from the same module (`single-use-helper`), deep inheritance, tiny modules with one importer. Skips dataclasses, pydantic/ORM models, enums, exceptions. | warning / info |
| `bloat` | link-time pruning | Dependencies declared in `pyproject.toml` / `requirements*.txt` / `package.json` but never imported (`unused-dependency`), imported packages not declared (`undeclared-dependency`), `except: pass` (`swallowed-exception`), try/except that only re-raises (`redundant-try`), log-and-reraise, stub functions. | warning / info |
| `duplicates` | common-subexpression elimination | Functions with identical AST shape ignoring identifiers and literals (`duplicate-function`), and near-identical ones by 4-gram Jaccard similarity (`near-duplicate-function`). `-O2` only. | warning / info |

The order is the order of impact for agent-written code: dead code and abstraction findings usually remove the most lines per decision; cycles, complexity and hotspots are the map you need to do that safely.

## Configuration

Optional `[tool.vibepiler]` in `pyproject.toml` (or a `vibepiler.toml`):

```toml
[tool.vibepiler]
ignore = ["migrations/**", "scripts/one_off_*.py"]
languages = ["python"]              # default: all detected
opt_level = 2
entry_patterns = ["*.lambda_handler", "jobs/*"]   # extra roots by qualname or file glob
treat_exports_as_roots = false      # default: only JS/TS entry modules (index, pages/, app/, ...)

[tool.vibepiler.thresholds]
complexity = 10
function_loc = 60
nesting = 4
params = 6
duplicate_min_nodes = 25
near_duplicate_similarity = 0.85
god_module_fan = 10
single_use_helper_max_loc = 8
```

## Using it from Python

```python
from vibepiler.compiler import Compiler

result = Compiler("path/to/project").run(opt_level=2, emit=["json", "html"], out_dir="out")
program = result.program
g = program.graph                       # ProgramGraph (networkx underneath)
for d in program.diagnostics:           # Diagnostic(pass_name, code, severity, message, file, line, node_id, suggestion, data)
    ...
dead = [n for n in g.nodes() if n.attrs.get("reachable") is False]
mg = g.module_graph()                   # nx.DiGraph of modules/packages
cg = g.call_graph()                     # nx.DiGraph of callables
```

## Extending

- **New language**: subclass `LanguageSpec` in `src/vibepiler/frontend/languages/`, implement `module_name`, `extract`, `import_targets`, `package_target` and the small AST hooks, register it in `languages/__init__.py`, add the extensions to `frontend/loader.py`. The builder and every pass are language-agnostic.
- **New pass**: subclass `Pass` in `src/vibepiler/passes/`, return `Diagnostic`s, append it to `passes/registry.py`. Passes may read the tree-sitter node behind any symbol via `program.symbol_def(node)`.

## Limitations and next steps

- Name-based resolution, no types. A language-server backend (pyright / tsserver via LSP) would raise the resolution rate; the resolver is isolated in `ir/builder.py` for that reason.
- Python and JS/TS only, for now. Go, Rust and Java grammars are already available through the language pack.
- Analysis only; an `--fix` mode for the mechanical findings (unused imports, dead symbols) is the obvious next step.

## Development

```bash
uv sync
uv run pytest
uv run vibepiler compile . --ignore "tests/fixtures/**" -Wall
```

Design notes live in `docs/superpowers/specs/`.
