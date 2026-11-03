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

## Quick start

You need [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`). Nothing else: no clone, no install step.

```bash
# a project on disk
uvx --from git+https://github.com/NISH1001/vibepiler vibepiler compile path/to/project

# a repo on GitHub: owner/repo, owner/repo@branch, or any GitHub / git URL
uvx --from git+https://github.com/NISH1001/vibepiler vibepiler compile igaurab/gpython

# the project you are standing in
cd my-vibe-coded-app && uvx --from git+https://github.com/NISH1001/vibepiler vibepiler compile .
```

If you will run it more than once, install the command:

```bash
uv tool install git+https://github.com/NISH1001/vibepiler   # or: pipx install git+https://github.com/NISH1001/vibepiler
vibepiler compile .                                           # then just this
vibepiler compile owner/repo -Wall                            # -Wall also shows the info-level findings
```

### Where the output goes

Everything is written to a `vibepiler-out/` directory **in the directory you ran the command from** (`vibepiler-out/<repo>/` for a remote target). Change it with `-o some/dir`.

```
vibepiler-out/
├── report.html         the interactive viewer: open this first
├── report.md           every finding grouped by pass, each with a suggested action
├── ir.json             the whole program graph, findings and metrics, for scripts and agents
├── graph.dot           module dependency graph (dot -Tsvg graph.dot -o graph.svg)
├── graph-symbols.dot   symbol-level graph
└── graph.mmd           Mermaid module graph, pasteable into a GitHub issue
```

```bash
open vibepiler-out/report.html            # macOS; xdg-open on Linux
```

The terminal shows the phase-by-phase progress, the warnings (first 60), and a "what can go" table. The files always contain everything.

Remote repos are shallow-cloned into `~/.cache/vibepiler/repos/` and reused on the next run (`--refresh` re-clones, `--cache DIR` relocates). File paths in the report are relative to that checkout.

### Reading the result

1. **The size line** at the top of the terminal output and of `report.html`: `waste` is lines that could go, `tangle` is structure harder than its job, `bloat` combines them. Lead with the lines, not the percentage.
2. **`report.html` → Overview**: the "what can go" tiles, the entry points, and "Biggest wins". Orbs with a red wedge are modules with removable lines; click one for its findings and its own bloat block.
3. **Findings tab** (or `report.md`): filter by pass and by finding type; the graph lights up the matches and "only flagged" isolates them. `dead-code`, `duplicates`, `wrappers` and `abstraction` remove the most lines per decision; `cycles`, `complexity` and `hotspots` tell you what is risky to touch.
4. **Concepts tab** (`-O3`): the codebase grouped by meaning, with scattered concerns and mixed modules named.
5. **Link tab**: how much of the call graph was proven. A low "linked" percentage means dynamic dispatch; dead-code results stay safe (they are conservative), but there are fewer of them and the size line says how many lines are alive only by name.

### Five ways to use it

**1. Audit a repo you did not write.** One command, no install, read the size line and the Findings tab.

```bash
uvx --from git+https://github.com/NISH1001/vibepiler vibepiler compile owner/repo -O3
```

**2. Shrink a codebase with the elimination loop.** `-R` prints the plan, one edit per line with the lines it frees and how sure the tool is. Apply the top one, re-run against the previous `ir.json`, repeat until nothing moves.

```bash
vibepiler compile . -R -q                                   # the plan
# ...apply the top remark...
vibepiler compile . -R -q --baseline vibepiler-out/ir.json  # what moved
```

**3. Let your coding agent do the loop.** Every remark carries the file, the line, the action and a suggestion, so the plan is a to-do list an agent can execute. A prompt that works:

> Run `vibepiler compile . -R -q`. Apply the first remark that is `proven` or `inferred`, run the tests, then re-run vibepiler with `--baseline` and show me what moved. Stop when the plan is empty or a remark looks wrong, and tell me which.

The tool never rewrites code itself; the agent or you do, and the next run checks the result.

**4. Gate it in CI.** Fail on severity, or on the score:

```bash
vibepiler compile . --fail-on warning -q
vibepiler compile . --fail-on bloat>0.25 -q
vibepiler size .                                   # the per-folder size report, for a PR comment
```

**5. Script it.** `ir.json` holds the whole graph, every finding and every metric; the Python API below gives the same objects in-process, including `Bloat.of(node)`.

## Develop on it

```bash
git clone https://github.com/NISH1001/vibepiler && cd vibepiler
uv sync
uv run vibepiler compile path/to/project
uv run pytest
```

## The CLI (deliberately compiler-shaped)

```
vibepiler compile <path | owner/repo[@ref] | git url> [-O0|-O1|-O2|-O3] [-W all|warning|error]
                         [--emit json,dot,mermaid,html,md] [-o out/] [--only pass,pass] [--skip pass]
                         [--ignore "glob,glob"] [--lang python,typescript] [--fail-on warning|error|bloat>0.25] [-q]
                         [-R] [--baseline previous/ir.json] [--cache DIR] [--refresh]
vibepiler size    <path> [-O1|-O2|-O3] [--top N]    # waste, tangle and bloat per folder, like `size` on a binary
vibepiler graph   <path> [--level module|symbol] [--format dot|mermaid|json] [--packages]
vibepiler paths   <path> --from <symbol> [--to <symbol>] [--depth N]
vibepiler similar <path> <symbol> [-n 10]          # -O3 tier: nearest functions by meaning
vibepiler passes
```

- `-O0` builds the graph only. `-O1` runs the linear passes. `-O2` (default) adds the quadratic ones (duplicate detection). `-O3` adds the embedding tier (see below).
- `-W` is the minimum severity printed to the terminal. `-Wall` shows `info` findings. Everything is always in `ir.json`/`report.md`.
- `-R` prints the **missed-optimization remarks** (after clang's `-Rpass-missed`): the elimination plan, one line per edit with the lines it frees and how sure the tool is.
- `--baseline previous/ir.json` prints how waste, tangle and bloat moved since an earlier run. That is the loop: apply the top remark, re-run, repeat.
- `--fail-on warning` or `--fail-on bloat>0.25` makes it a CI gate.

What each tier does:

| tier | passes | what it can tell you |
|---|---|---|
| `-O0` | none | the program graph only: modules, symbols, imports, calls, inheritance, references |
| `-O1` | entry-points, dead-code, unused-imports, cycles, complexity, wrappers, hotspots, bloat-score | everything that follows from reachability and structure in linear time |
| `-O2` | + abstraction, bloat, duplicates | needless interfaces and classes, dependency and exception boilerplate, structurally identical and near-identical functions |
| `-O3` | + semantic-duplicates, responsibilities, concepts | meaning: functions that do the same job with different code, modules mixing unrelated jobs, and the concept map of the whole codebase |

The bloat score runs at every level above `-O0` and reflects only the passes that ran; it says which level it was measured at.
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
| `report.html` | Self-contained interactive viewer organized by compiler phase: **Parse** (every file and its parse status), **Link** (name resolution: the call-resolution breakdown, each module's binding table, member names that could only be matched dynamically), **Program graph** (a living map: modules are orbs sized by lines with a red wedge for the share of lines that could go, traffic moves along import and call edges, unreachable code is grey and still; the layout is fixed and toggles never move nodes; double-click a module to open it, click a node and *Trace* to watch execution flow at a speed you choose; a *bloat ≥* slider hides everything scored below it, and the Findings filters, by pass and by finding type, decide which nodes "only flagged" keeps and light the matches up), **Concepts** (`-O3`: the semantic space, see below), **Passes** (every finding with its suggestion, click to jump into the graph). The header carries the size line: waste, tangle, bloat. |
| `report.md` | Summary with the size line and bloat per folder, "what can go" table, entry points, hotspots, complexity, concepts, the elimination plan, then every finding grouped by pass with a suggestion. |
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
| `semantic-duplicates` | value numbering (semantic CSE) | Functions that do the same job with different code, by embedding similarity. Only mutual nearest neighbours above the threshold are reported, so the list stays short and precise. `-O3` only. | warning / info |
| `responsibilities` | function partitioning | Modules whose functions cluster into several unrelated groups by meaning (a module that is really three modules). `-O3` only. | info |

The order is the order of impact for agent-written code: dead code and abstraction findings usually remove the most lines per decision; cycles, complexity and hotspots are the map you need to do that safely.

## The semantic tier (`-O3`)

Everything up to `-O2` is exact: names, scopes, AST shapes. `-O3` adds the one thing that is not mechanical, *meaning*, with a code embedding model that runs locally on the CPU, no PyTorch.

```bash
# one-off
uvx --from "vibepiler[semantic] @ git+https://github.com/NISH1001/vibepiler" vibepiler compile . -O3

# installed
uv tool install "vibepiler[semantic] @ git+https://github.com/NISH1001/vibepiler"
vibepiler compile owner/repo -O3
vibepiler similar . app.utils.sum_even      # the functions closest in meaning to one you point at
```

- **Default model**: [`nomic-ai/CodeRankEmbed`](https://huggingface.co/nomic-ai/CodeRankEmbed), a 137M-parameter encoder trained for code retrieval (CoIR 60.1, the best open model under 200M). The upstream release needs PyTorch and custom model code, so vibepiler runs an int8 ONNX build of it, [`nishparadox/CodeRankEmbed-onnx-int8`](https://huggingface.co/nishparadox/CodeRankEmbed-onnx-int8) (138 MB, pinned to a commit), through [fastembed](https://github.com/qdrant/fastembed): no PyTorch anywhere. Model files land in `~/.cache/vibepiler/models/`; vectors are cached by content hash in `~/.cache/vibepiler/embeddings/`, so only the first run of a repo pays for embedding.
- **Alternative**: `embedding_model = "jinaai/jina-embeddings-v2-base-code"` (161M, code-trained, official int8 ONNX, 160 MB) if you would rather not depend on a community-converted model. Same speed, slightly weaker separation.
- **Fast mode**: `embedding_model = "minishlab/potion-code-16M-v2"`, a 16M static code embedding model run with [model2vec](https://github.com/minishlab/model2vec) (pure numpy). Roughly 500x faster, weaker on "same job, different code"; use it on repos with tens of thousands of functions.
- **Nothing is truncated**: each function or method is embedded whole (signature and body); the only limit is the model's 8,192-token context. Speed comes from sorting texts by length before batching, so a batch of short functions is never padded up to one long one. That beat truncating to 800 characters on every model measured. Tests and nested closures are skipped, since they are repetitive by design.
- **Why these**: measured on a 1,037-function repo (Apple-silicon laptop, CPU only), whole function bodies, length-sorted batches:

  | model | code-trained | functions/s | separates the known twin pair* | notes |
  |---|---|---|---|---|
  | `CodeRankEmbed` int8 | yes | 33 | +0.086 | default |
  | `CodeRankEmbed` fp32 | yes | 16 | +0.100 | same ranking, half the speed |
  | `jina-embeddings-v2-base-code` int8 | yes | 36 | +0.059 | alternative |
  | `BAAI/bge-small-en-v1.5` | no | 52 | +0.040 | general text model, 33M |
  | `granite-embedding-small-english-r2` | partly | 67 (800-char inputs) | +0.029 | 47M, fastest transformer tried |
  | `potion-code-16M-v2` (static) | yes | 17,000 | misses it (nearest neighbour is a longer function sharing tokens) | fast mode |

  \* cosine of two functions that do the same job with different code (a `for` loop versus a `sum` over a generator) minus the 99th percentile of all unrelated pairs in the same file. Code-trained transformers win regardless of size, which matches the June 2026 code-to-code retrieval benchmark ([arXiv 2606.27401](https://arxiv.org/abs/2606.27401)): contrastive training on code matters more than parameter count. Static models are fast because they average fixed per-token vectors, which is also why they cannot see past vocabulary.
- **`semantic-duplicates`**: two functions are reported only when each is the other's nearest neighbour *and* their cosine similarity is above `thresholds.semantic_similarity` (per-model default: 0.72 for CodeRankEmbed, 0.82 for the jina model, 0.88 for bge-small, 0.90 for potion; cosine distributions differ a lot between models). That mutual-nearest-neighbour rule is what keeps the output honest: a 1,800-function repo has 1.6 million pairs, and a plain threshold would list thousands. Pairs the structural pass already reported are left out; same-named methods on different classes (interface implementations) and dunder methods are ignored.
- **`responsibilities`**: within each module, functions are greedily clustered at `thresholds.responsibility_similarity` (per-model default). A module is reported when it has two to five groups of three or more functions that together cover most of it; a module that shatters into many tiny groups is just large, not mixed.
- **Swap the model** with `embedding_model = "..."` in the config: any fastembed model name, or any `minishlab/potion-*` static model.
- Without the extra installed, `-O3` runs everything else and prints one line telling you what to install; it never fails the build.

What the tier produces, concretely:

1. **`semantic-duplicates`**: pairs of functions that do the same job with different code. Structural duplicate detection (`-O2`) cannot see these; this is the one thing embeddings add that nothing else can.
2. **`responsibilities`**: modules whose functions fall into several unrelated groups, each group labelled by the words its names share (`{resolve · import} ×6, {emit · html} ×4`).
3. **`concepts`**: the whole codebase partitioned by meaning. Every function is linked to its nearest neighbours by cosine similarity, communities in that graph (Louvain, fixed seed) become concepts, each labelled from identifier words and placed on a 2-D map of the concept centroids. The **Concepts** tab of the viewer is that map: distance is similarity of meaning, size is how many functions, lines are calls between concepts, amber means a concept spread over several modules with no home (a *scattered concern*, the mirror image of a mixed module). An interface implemented in every module, such as a `run` on every pass, is recognised as a pattern, not scattering.

The map is projected with PCA by default. Install `vibepiler[map]` to project with UMAP instead (seeded, so the map is the same on every run); it is a separate extra because umap-learn pulls in scikit-learn, scipy and numba, about 150 MB, and the rest of the tool should keep installing in seconds.

What it does and does not do: an embedding cannot tell `parse` from `dump`, so inverse operations will occasionally pair up. It is also not used anywhere exactness matters; call resolution, reachability and dead code stay deterministic, and lines the model thinks could merge are kept out of the bloat score.

## Bloat, the score

Every function, class, module, folder and the repo gets one number in [0, 1]. It prints like `size` prints text, data and bss:

```
waste 1% (34 of 6326 lines; inferred 31, proven 3; +222 if the semantic pairs merge)   tangle 0.31 (high-complexity ×55, long-function ×13, mixed-responsibilities ×8)   bloat 31%   [-O3, 54% linked, nomic-ai/CodeRankEmbed:int8]
```

```
bloat = 1 − (1 − waste)(1 − tangle)
```

- **waste** is lines that could go, over the unit's lines. Every finding claims a line budget on the symbol it is about; budgets are allocated largest-first and never exceed the symbol's lines or an enclosing class's or module's, so a function that is both dead and a stub counts once and the elimination plan sums exactly to the removable total. Each claim carries a confidence: **proven** (unused import, exact duplicate, redundant try), **inferred** (reachability and structure, which depend on how much of the call graph linked) or **model** (an embedding said so). Model lines are shown and planned but never scored.
- **tangle** is structure harder than its job needs, each term derived from the finding's own numbers as `(value − limit) / value` against your thresholds, combined as a noisy OR. Containers take their children's tangle weighted by share of lines, then OR their own flags. Centrality is not tangle: a module everybody imports is normal.
- The two combine as a probabilistic OR, so the score stays in range and one bad function cannot push a module past 1. Clean code is exactly 0.

| finding | lines charged | action |
|---|---|---|
| dead or unreachable symbol or module, stub, one-implementation interface, pass-through or thin wrapper, indirection link | all of it | delete / fold |
| exact duplicate group | every copy but the first | fold |
| near or semantic duplicate pair | the second copy, at model confidence | fold |
| class-as-function, namespace class, one-subclass base, tiny module | the shell: the container minus what it contains | fold |
| single-use helper | 2, the boundary itself | fold |
| redundant try, swallowed exception, log-and-reraise | 2, the clause | delete / simplify |
| unused import | 1 | delete |

| finding | tangle term |
|---|---|
| complexity, nesting, parameters, length over the threshold | (value − threshold) / value |
| god module | (fan − threshold) / fan |
| module in an import cycle | share of its imports that stay inside the cycle |
| mixed responsibilities | ½ · (groups − 1) / groups |
| deep inheritance | (depth − 3) / depth |

Rules that follow from the critique it went through before shipping: tests are scored but kept out of the repo and folder totals; modules are ranked by lines freed, with the score shown beside them as a density, because a 3,000-line module with 50 dead lines matters more than a 10-line module with 3; the score is stamped with the `-O` level, link rate and model it was measured under, since it is a property of the analysis, not of the code alone; and the semantic tier can list a pair but cannot move the score.

Every finding also carries an `action`, delete, fold or simplify, and the **elimination plan** (`-R`, and in `report.md`) orders them by lines freed. The loop is a fixpoint iteration: apply the top edit, re-run with `--baseline`, repeat until nothing moves. From Python, `Bloat.of(node)` and `Bloat.of(program)` give the same numbers as a value with `.waste`, `.tangle` and `.score`.

### Bootstrapped on itself

The first thing it was run on was vibepiler. Three rounds of the loop, each measured with `--baseline`:

| | start | round 1 | round 2 | round 3 |
|---|---|---|---|---|
| removable lines shown by analysis | 71 | 34 | 41 | 31 |
| tangle | 0.30 | 0.31 | 0.27 | 0.26 |
| bloat | 31% | 31% | 28% | 26% |
| most tangled function | `BloatScorePass.run` 100 | same | `AbstractionPass.run` 57 | `read_manifests` 43 |

Round one was waste: five dead helpers in the tree-sitter wrapper left over from the first implementation, a constant nothing read, unused imports, the `extract` method every language adapter had copied, and two copies of the "does this class inherit from a framework" check. Rounds two and three were tangle: the tool's own scoring pass, at cyclomatic complexity 100, became five stages; the markdown emitter became one function per section; the CLI's printing moved into a terminal backend, which is what the mixed-responsibilities finding on `cli.py` meant; the abstraction and entry-point passes were split the same way.

Self-findings the owner rejected became fixes to the tool, which is the other half of the loop: a module constant used only as a default argument was reported dead (the extractor skipped identifier defaults); typer's decorated callback was called a stub (decorated stubs are registered with a framework on purpose); and round two briefly *raised* waste by 7 lines because the section functions it created were charged as single-use helpers, so the rule now recognises a caller with three or more such helpers as a function made of named steps, not indirection.

What is left is honest: 60 functions over the complexity threshold, most of them tree-walkers and manifest parsers that are long because the grammar is, and 1,040 lines alive only by name because 54% of call sites link. The second number is the one a type-aware linker would move.

## Configuration

Optional `[tool.vibepiler]` in `pyproject.toml` (or a `vibepiler.toml`):

```toml
[tool.vibepiler]
ignore = ["migrations/**", "scripts/one_off_*.py"]
languages = ["python"]              # default: all detected
opt_level = 2
entry_patterns = ["*.lambda_handler", "jobs/*"]   # extra roots by qualname or file glob
treat_exports_as_roots = false      # default: only JS/TS entry modules (index, pages/, app/, ...)
embedding_model = "nomic-ai/CodeRankEmbed"   # -O3; or "jinaai/jina-embeddings-v2-base-code", or "minishlab/potion-code-16M-v2" (fast mode)

[tool.vibepiler.thresholds]
complexity = 10
function_loc = 60
nesting = 4
params = 6
duplicate_min_nodes = 25
near_duplicate_similarity = 0.85
god_module_fan = 10
single_use_helper_max_loc = 8
semantic_similarity = 0.72          # -O3; omit to use the per-model default
responsibility_similarity = 0.32    # -O3; omit to use the per-model default
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
- The bloat denominator is physical lines, so blank lines, comments and docstrings count; logical source lines would be fairer to terse code. Generated code is not yet excluded.
- The elimination plan orders edits by lines freed without simulating them: inlining a wrapper raises its caller's complexity, merging duplicates can create a hub. A graph-level what-if (delete, contract, identify on the networkx graph, then re-score) is the next step for the plan.
- Concepts are seed-stable but not edit-stable: one deletion can reshuffle communities, which is why they are reported and mapped but never scored.

Design notes live in `docs/superpowers/specs/`.
