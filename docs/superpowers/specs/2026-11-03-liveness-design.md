# Liveness: the leftovers inside function bodies, and `Metric`, the shape every score shares

Date: 2026-11-03. Branch: `feature/liveness`. Status: design, awaiting approval.

## Intent

Vibepiler finds what a compiler would prune across a project: dead symbols, duplicated
bodies, wrappers, cycles, over-abstraction. Everything it finds today lives *between*
functions. Nothing looks *inside* a body. The frontend only records assignments at module
scope, so a local assigned and never read, a value overwritten before anyone reads it, a
parameter accepted and ignored, an instance attribute no method reads, statements after a
`return`, and a branch on a constant condition are all invisible.

Those are the leftovers coding agents produce most: `result = compute()` with no second
line, `data = None` two lines above `data = load()`, an `options` parameter nobody
consults, `self.cache = {}` that nothing reads, a `return` left above code that was meant
to be deleted, `if False:` around an experiment. They are small per site and numerous per
repo, and every one is provable from the body alone, with no dependence on how much of the
call graph linked.

Two things get built:

1. **A `liveness` pass.** The compiler analogue is liveness analysis feeding dead-store
   elimination, done the way a compiler does it: a backward dataflow analysis over the
   function's control flow, solved to a fixpoint. It produces findings for the leftovers
   above, each with the lines it frees, so they enter the existing waste accounting and the
   elimination plan.
2. **Liveness as a metric, and `Metric` as the shape all scores share.** Every unit gets
   one number in [0, 1] from one formula: the share of the definitions it makes that reach
   a use. High is good. `Bloat` and `Liveness` become two subclasses of one small `Metric`
   base, and every consumer of a score (size line, `size` table, HTML meter and node panel,
   markdown, JSON, `--fail-on`, `--baseline`) iterates the metric registry instead of
   hard-coding bloat.

## Goals

- Five finding codes, each proven from the body (or, for attributes, inferred from the
  project-wide name bag the dead-code pass already uses), for Python and JS/TS.
- A liveness number per function, class, module, folder and repo, printed beside waste,
  tangle and bloat in every place those are printed, with a breakdown so the number is
  never a mystery.
- A `Metric` base that `Bloat` is refactored onto without changing any bloat number, any
  `ir.json` key it writes today, or the README's bootstrap table.
- Conservative by construction: the anti-goal of false positives still holds. When the
  analysis is unsure, a definition is live. Every exemption below is a rule, not a
  heuristic, and the Python findings are checked against ruff on the fixture project.
- Bootstrapped: the branch ends with a round of the elimination loop on vibepiler itself
  using the new pass. Rejected self-findings become fixes to the pass.

## Non-goals

- Folding liveness into the bloat formula on this branch. See "Liveness and bloat".
- An explicit control-flow-graph data structure or a CFG library. The syntax tree encodes
  structured control flow; the analysis walks it. (Python `goto`-like constructs do not
  exist; JS `label:` with `break label` is handled as a jump, see below.)
- Class-level assignments (`x = 1` in a class body) as findings or as definitions. They are
  configuration and constants; a later rung.
- Type information. The typed linker is a separate branch.
- Rewriting code.

## Anti-goals

- Flagging a parameter a framework or a base class imposes. A wrong `unused-param` on a
  route handler or an override would make the tool look naive on the first run.
- Flagging a destructuring sibling, a loop variable with a read sibling, or an underscore
  name. Those are idiom, not waste.
- Double-counting. A dead store inside a dead function must not add lines to waste twice;
  the existing largest-first budget allocation guarantees it, so the new findings go
  through the same claim mechanism and no other.
- A second, parallel score pipeline. There is one registry of metrics; the backends know
  nothing about either metric by name except for the bloat slider, the tint and the new
  colour-by switch in the viewer.

## Part 1: the `liveness` pass

Pass name `liveness`, cost 1 (runs at `-O1`), `requires = ("entry-points", "dead-code")`,
registered after `dead-code`. Analogue: "liveness analysis / dead-store elimination".
Description: "Stores that no read reaches, parameters never read, instance attributes never
read, statements after a terminator, branches on a constant."

### The analysis

For each function or method, a backward liveness analysis over its body:

```
in[s]  = use[s] ∪ (out[s] − def[s])
out[s] = ⋃ in[s′]   for every successor s′ of s
```

solved to a fixpoint. A store to `v` at statement `s` is **dead** when `v ∉ out[s]`: no
path from the store reaches a read of `v` without passing through another store to `v`.

The control flow is read straight off the syntax tree, since both languages are structured:

- **sequence**: fold the statements from the last one upward, threading the live set;
- **branch** (`if`/`elif`/`else`, ternary, `match`/`switch`): `in` is the condition's reads
  joined with the union of the arms' `in` sets; a missing `else` contributes `out`;
- **loop** (`for`, `while`, `for-of`, `for-in`, `do-while`): iterate the body with the
  loop's own `in` as its `out` until the set stops changing (monotone, two or three passes);
  the loop target is a definition at the head on every iteration; a Python loop `else`
  joins the exit;
- **jumps**: `return` and `raise`/`throw` start from the empty set (nothing is live after
  leaving the function); `break` takes the enclosing loop's exit set, `continue` its head
  set, kept on a stack; a labelled JS `break`/`continue` picks that label's loop;
- **`try`/`except`/`finally`**: every statement in the `try` body may jump to any handler,
  so each statement's `out` also includes every handler's `in`; `finally` is a sequence
  after both; `except ... as e` defines `e` at the handler head;
- **`with`**: a sequence whose `as` target is a definition at the head;
- **closures**: a name read by a nested function, lambda or comprehension is live at every
  point of the enclosing function (the closure may run at any later time);
- **augmented assignment** (`x += 1`) is a use then a definition; **walrus** and
  conditional-expression assignments define where they appear; `del x` is a use;
- **attribute and subscript targets** (`x.a = 1`, `x[i] = 2`) are uses of `x`, not
  definitions of it;
- a function whose body contains a dynamic-scope access (`locals()`, `vars()`,
  `globals()`, `eval`, `exec` in Python; `eval`, `arguments`, `with` in JavaScript) is
  skipped for `dead-store` and `unused-param`, and counted in the pass metrics;
- any statement type the adapter does not classify is treated as opaque: it reads every
  identifier it contains and defines nothing. Unknown control flow therefore errs towards
  live.

The analysis is one language-agnostic module (`passes/liveness.py`) that asks the adapter
to classify each statement; see "Language hooks".

### Finding codes

| code | severity | confidence | action | lines charged to waste | what it is |
|---|---|---|---|---|---|
| `dead-store` | warning | proven | delete, or simplify when the right-hand side has a call | the statement's span, or 0 when the right-hand side has a call | a store no read reaches: never read, or overwritten before any read |
| `unused-param` | info | proven | simplify | 0 | a trailing parameter never read in the body |
| `unused-attribute` | warning | inferred | delete | each assignment statement's span | an instance attribute written and never read by that name anywhere in the project |
| `unreachable-code` | warning | proven | delete | the dead statements' span | statements after `return`, `raise`/`throw`, `break` or `continue` in the same block |
| `dead-branch` | warning | proven | delete | the branch's span | `if`/`while` on a literal constant condition, plus the `else` of a literal-true `if` |

The `dead-store` message says which case it is: "`x` is assigned at line 12 and never
read" or "`x` is assigned at line 12 and overwritten at line 14 before it is read". A store
whose right-hand side contains a call, `await` or `yield` may be the point of the line
(`x = fetch()`), so it charges nothing and the action is simplify ("drop the assignment,
keep the call if it has side effects"). A store of a literal, name, attribute, arithmetic
or call-free comprehension charges its lines and says delete.

`unused-param` charges no lines: removing a parameter frees nothing and changes a
signature. It lowers the function's liveness and is reported as a finding; it stays out of
the elimination plan, which lists entries with lines.

### Exemptions (rules, not heuristics)

`dead-store` is not reported for a name that:

- starts with `_`;
- is declared `global` or `nonlocal` in that function;
- is one target of a tuple/array/object destructuring where any sibling target is read.
  A destructuring with *no* read sibling reports once, on the statement, naming all targets
  (ruff does not flag this; see the oracle rule in Part 4);
- is a `for` loop target or a comprehension target with a read sibling (`for i, x in ...`
  where only `x` is read). A loop target that is never read at all is reported like any
  other definition.

`with ... as f` and `except ... as e` targets are ordinary definitions and are reported
when nothing reads them; ruff flags both, and `except Exception as e: pass` is a
recognisable agent leftover.

`unused-param` is not reported for:

- `self`, `cls`, and any name in the language's `self_names`;
- names starting with `_`, `*args`, `**kwargs`, and rest parameters;
- a parameter that precedes a read parameter (positional placeholders such as the `err`
  in `(err, data) =>` cannot be dropped). Only the trailing run of unread parameters is
  reported, one finding per function listing them. This is eslint's `after-used` rule;
- any method of a class that has an `inherits` edge to a resolved class defining a member
  of the same name (an override), or a class with an external or unresolved base (the base
  may define it; the graph already knows which classes have an external base);
- any decorated function or method, any entry point (the `entry` attr the entry-points
  pass sets), any abstract method, lambda, or function whose body the adapter's
  `is_stub_body` recognises;
- a function skipped for dynamic scope.

`unused-attribute` concerns instance attributes only: `self.x = ...` in any method
(Python), `this.x = ...` and declared class fields (JS/TS). An attribute is read if an
attribute access with that name appears anywhere in the project outside an assignment
target, resolved or not. The builder adds a sibling bag of attribute-read names next to the
linker's `used_member_names` (Python assignment targets already land in the existing bag,
so it cannot be reused as-is). Exempt: dunder names; classes that define `__getattr__`,
`__getattribute__` or `__slots__`; dataclass, NamedTuple, TypedDict, Enum and pydantic
bodies, which the abstraction pass already recognises as data shapes; classes with an
external or unresolved base when the attribute name is read nowhere but the base is
unknown (the framework may read it).

`unreachable-code` has no exemptions. It is same-block only: statements that follow a
terminator statement in the same statement list. The case where every arm of an `if`
terminates and code follows the whole `if` falls out of the dataflow walk for free when the
arms' `out` sets are empty, and is reported too; the builder confirms it on a test before
claiming it.

`dead-branch` recognises literal conditions only: `False`, `True`, `0`, `1`, `None`, `not`
of one of those, and the JS equivalents `false`, `true`, `0`, `1`, `null`, `undefined`.
Exempt: `if TYPE_CHECKING:` and `if __debug__:` in Python, and any condition that is a
name rather than a literal. A literal-true `if` reports its `else` branch; a literal-false
`if` reports its own body; a literal-false `while` reports its body.

### Language hooks

Each adapter adds a small set of AST hooks to `LanguageSpec`, in the style of the existing
`body_statements` and `except_clauses`. The pass stays language-agnostic. The builder names
them; the capabilities:

- classify a statement: sequence, branch (condition + arms), loop (target, condition,
  body, else), try (body, handlers with their targets, finally), with (targets, body),
  jump (return / raise / break / continue, with label), or plain;
- the definitions and the uses in a plain statement or an expression, with destructuring
  groups kept together and attribute/subscript targets reported as uses;
- the parameter list in positional order, with the `self`/rest markers;
- names a nested scope (def, lambda, comprehension, arrow function) reads from the
  enclosing function;
- a literal-condition evaluator returning `True`, `False` or `None`;
- whether a body contains a dynamic-scope access.

Python covers assignment, augmented assignment, walrus, annotated assignment, `for`,
`while`, `with`, `try`, `match`, `global`/`nonlocal`, comprehensions, lambdas, nested
defs, `del`. JavaScript/TypeScript covers `var`/`let`/`const`, destructuring,
`for`/`for-of`/`for-in`/`while`/`do-while`, `switch`, `try`/`catch`/`finally`, labelled
jumps, arrow and function expressions, class fields and `this.x =`. Writes inside an
anonymous callback belong to that callback's own scope, not the enclosing function's.

### Where the findings go

The bloat-score pass learns the new codes: the four line-charging codes join `ACTION`,
`PROVEN` (all but `unused-attribute`) and the claim rules; `unused-attribute` is inferred.
Each claim is on the enclosing function or method node, so the largest-first allocation
caps it at that symbol's lines and a dead store inside a dead function counts once. The
diagnostic carries `names` (the dead names), `span` (start and end line of the statement
or branch), `loc` (lines charged) and, for an overwritten store, `overwritten_at`, so the
viewer and markdown can show the exact lines.

### Metrics written by the pass

`program.metrics["liveness"]` holds counts per code and the number of functions skipped
for dynamic scope, mirroring how the `bloat` pass and the `bloat_score` metric coexist.
The metric itself is `program.metrics["liveness_score"]` (Part 2).

## Part 2: `Metric`, and `Liveness(Metric)`

### The formula

For any unit U, let D(U) be the definitions made lexically inside U:

```
live(U) = |{ d ∈ D(U) : d reaches a use }| / |D(U)|        (1.0 when D(U) is empty)
```

What a definition is, and what "reaches a use" means, by level:

| level | definitions | a definition reaches a use when |
|---|---|---|
| function, method | its parameters (after the `unused-param` exemptions) and its stores (after the `dead-store` exemptions) | the dataflow analysis above says `v ∈ out[s]` |
| class | its methods, its instance attributes, plus everything inside its methods | method reachable, referenced or kept by name (the dead-code verdict); attribute read by name anywhere; inner definitions as above |
| module | its top-level functions, classes, variables and import bindings, plus everything inside them | symbol reachable, referenced or kept by name; import used; inner definitions as above |
| folder, repo | the sum of the modules' counts (not a mean of ratios); tests excluded, as bloat does | as above |

Lexical containment is the rule: a module's number includes the stores and parameters of
the functions it contains, so the repo fragment can honestly say how many of each kind are
dead. The breakdown names every kind. The reachability and kept-by-name verdicts are the
ones entry-points and dead-code already stamp; an accepted finding counts as live. The
builder reads the dead-code verdict from the diagnostics or stamps an attr, one or the
other.

Printed fragment:

```
live 94.1% (448 of 476 definitions; dead: 9 stores, 4 params, 3 attributes, 7 symbols, 5 imports)
```

### The base

`src/vibepiler/passes/metric.py`, a small abstract base class (shared behaviour, so an ABC
rather than a `typing.Protocol`):

```python
class Metric(ABC):
    key: ClassVar[str]        # attrs key on a node; program.metrics[f"{key}_score"] at repo level
    label: ClassVar[str]      # what the size line prints: "bloat", "live"
    better: ClassVar[Literal["low", "high"]]
    formula: ClassVar[str]    # one line, printed in the markdown and the viewer tooltip

    @property
    @abstractmethod
    def value(self) -> float: ...            # in [0, 1]

    @abstractmethod
    def terms(self) -> dict[str, float]: ...  # the breakdown beside the value: waste/tangle; live/defined

    @abstractmethod
    def as_dict(self) -> dict: ...            # what is stored on the node and in ir.json

    @classmethod
    @abstractmethod
    def of(cls, unit, default_loc: int = 0) -> "Metric | None": ...  # a Node, a Program, or a stored dict

    @abstractmethod
    def __str__(self) -> str: ...             # the size-line fragment, plain text

    def passes(self, op: str, limit: float) -> bool: ...   # the --fail-on test: "bloat>0.25", "live<0.9"
    def moved(self, prev: "Metric") -> bool: ...           # the --baseline test; default: any term or the value moved by 0.005
```

`Bloat` keeps its fields, its `attrs["bloat"]` and `metrics["bloat_score"]` keys, its
`as_dict` output and its `of`; it gains `key = "bloat"`, `label = "bloat"`,
`better = "low"`, `terms()` returning waste and tangle, and `passes`. Its `__str__` stays
the fragment it prints today. No bloat number changes.

`all_metrics()` in `passes/registry.py` returns `[Bloat, Liveness]` in print order. A
repo-level score is `program.metrics[f"{m.key}_score"]`; a node's is `node.attrs[m.key]`.

### `Liveness`

```python
@dataclass(frozen=True)
class Liveness(Metric):
    key, label, better = "liveness", "live", "high"
    formula = "live = definitions that reach a use / definitions"
    defined: int
    live: int
    dead: dict[str, int]   # by kind: stores, params, attributes, symbols, imports
    value = live / defined, or 1.0 when nothing is defined
```

Stored on function, class and module nodes as `attrs["liveness"]`, and at repo level as
`metrics["liveness_score"]` with `folders` and `tests` like `bloat_score`.

### Liveness and bloat

Not folded on this branch. Waste counts lines; liveness counts definitions. They are
correlated (a dead function is both lines and a definition) but not the same: a 60-line
function with three one-line dead stores is 5% waste and 70% live with ten definitions.

What this branch does: after the bootstrap round, rank vibepiler's and gpython's modules
by waste and by liveness and put both rankings in the branch's final report. If the orders
agree almost everywhere, liveness is a density view of the same thing and stays beside
bloat. If the top ten disagree, it carries information bloat lacks, and a follow-up branch
folds it (`bloat = 1 − (1 − waste)(1 − tangle)·live`) with its own README re-measure.
Bloat values are byte-identical before and after this branch.

## Part 3: wiring

- **Size line** (terminal, markdown, HTML meter): built by joining each metric's fragment
  in `all_metrics()` order, the stamp last. `size_parts` keeps its extras (the confidence
  split, the tangle reasons, "alive only by name"); the metric-driven join must not drop
  them. Terminal and markdown keep one source of the text.
- **`vibepiler size`**: one `live` column after `bloat`, per folder and for tests.
- **HTML viewer**: the meter shows the live number beside bloat; the node panel adds a
  "Liveness" block under the "Bloat" block listing the dead definitions by kind with their
  lines; a **colour-by** switch, bloat or live, that changes node tint only, never layout
  or position; the five codes appear in the finding-type filter like every other code and
  drive the graph the same way. No new tab; the bloat slider stays.
- **JSON**: `attrs.liveness` on function, class and module nodes; `metrics.liveness_score`
  at repo level.
- **`--fail-on`**: generic `<label><op><value>` where label is any metric's label and op
  is `>` or `<`; `bloat>0.25` keeps working; `live<0.9` is new. Severity names still work.
- **`--baseline`**: prints a line per metric that moved, using `moved`.
- **`Bloat.of`, `Liveness.of`**: same shapes, documented together in "Using it from Python".
- **`vibepiler passes`**: the new row.
- **README**: the pass in the passes table and the "How it optimizes" list; the size line
  explained with the new fragment; a "Liveness, the metric" subsection after "Bloat, the
  score" with the formula, the dataflow equations and the table above; "Limitations"
  updated (class-level attributes, the fold decision).

## Part 4: tests and bootstrap

- Unit tests per code on `tmp_path` snippets, Python and JS/TS, each with a positive and
  every exemption as a negative. Exemptions are the point; they get the most cases. The
  dataflow walk gets its own cases: overwritten before read, store in one arm read after
  the `if`, store at the end of a loop body read at the next iteration's head, store before
  `try` read in a handler, store captured by a closure, `break` and `continue` paths,
  both arms terminating.
- Fixture additions in a new module in each fixture project (`src/app/leftovers.py`,
  `src/leftovers.ts`) so existing count-based tests move by a known amount.
- **Ruff oracle** for Python, in the test suite: run
  `ruff check --select F841,ARG --output-format json` on the fixture project (ruff goes in
  the `dev` group; the test skips if it is not runnable). Every `(file, line)` ruff reports
  as F841 is in our `dead-store` set; our extra entries are only destructurings with no
  read sibling and overwritten-before-read stores, and the test asserts that by case. The
  `unused-param` set is a subset of ruff's ARG set (ours is after-used, ruff's is all).
- Metric tests: `Bloat` values on the fixture are byte-identical before and after the
  refactor (snapshot the `bloat_score` dict in a test before touching anything); `Liveness`
  values on the fixture by hand, at function, class, module and repo level, checking the
  lexical-containment sums; `--fail-on live<0.99` exits 1 on the fixture; `--baseline`
  reports a liveness move.
- **Bootstrap round five**: run the new pass on vibepiler, act on the findings through the
  elimination loop, re-measure with `--baseline`, add the round to the README table and
  the figure, and record vibepiler's own liveness before and after. A self-finding judged
  wrong is a bug in a rule above; fix the rule, not the code it pointed at. Then the two
  rankings for the fold decision.

## Part 5: the typed result

`CompileResult` is a dataclass today with `program`, `outputs` and `timings`, and `program.metrics`
is a plain dict of per-pass dicts with different shapes. With `Metric` in place the result gains a
typed layer over the scores and shortcuts to what every caller reaches for. No pydantic; the
codebase is dataclasses throughout and the install stays light.

```python
@dataclass
class CompileResult:
    program: Program
    outputs: dict[str, Path]        # backend name -> file written
    timings: dict[str, float]       # phase -> seconds

    scores: dict[str, Metric]       # label -> value: result.scores["live"].value; built from all_metrics()
    bloat: Bloat | None             # the same object as scores["bloat"]
    liveness: Liveness | None       # the same object as scores["live"]
    diagnostics: list[Diagnostic]   # program.diagnostics
    graph: ProgramGraph             # program.graph
    metrics: dict[str, Any]         # program.metrics, the raw per-pass numbers, documented as the untyped layer
    def to_dict(self) -> dict: ...  # the ir.json payload
```

`scores`, `bloat`, `liveness`, `diagnostics`, `graph` and `metrics` are properties over `program`,
not copies, so they never drift from it. A score is `None` when its pass did not run (`-O0`).
`vibepiler.compile(source, ...)` returns the same object. The README's "Using it from Python"
shows `result.bloat.waste`, `result.liveness.value` and `result.scores` next to the existing
graph examples. The pipeline stays synchronous: the work is CPU-bound and a full run on vibepiler
itself takes about a quarter of a second, so there is nothing to hide behind an `await`.

## Constraints

- Default install stays light: no new runtime dependency. ruff is dev-only.
- Deterministic: same input, same findings, same order (sorted by file, line, code).
- The compiler framing holds in every name: pass `liveness`, analogue "liveness analysis /
  dead-store elimination", codes named after what a compiler would call them.
- Existing bloat numbers, `ir.json` keys and CLI flags keep working unchanged.

## Decisions made here (mine, unless marked as the user's)

- The pass comes before the typed linker (the user's call).
- Liveness is a metric in [0, 1], high is good, shown everywhere bloat is shown (the
  user's); it sits beside bloat rather than inside it on this branch (mine, rule above).
- Real dataflow liveness from day one, not the whole-function approximation (the user
  asked for the compiler version; the structured walk makes it affordable).
- Lexical containment: a unit's definitions include everything inside it, so folder and
  repo sums are meaningful and the breakdown can name every kind (settles the reviewer's
  first question).
- No fold on this branch; both rankings go in the final report (the reviewer's second).
- The destructuring rule stays; the ruff oracle is "ruff's F841 set is contained in ours,
  and every extra is one of two named cases" (the reviewer's third).
- Class-level assignments are out of scope entirely, neither findings nor definitions
  (the reviewer's fourth).
- `with` and `except` targets are ordinary definitions, as ruff treats them.
- `Metric` is an abstract base class, not a Protocol, because the gate, baseline and
  size-line behaviours are shared code.
- Class name `Liveness`, label `live`: the compiler term.
- One pass does both the findings and the metric roll-up; the bloat-score pass still runs
  last and consumes the findings.
- `unused-param` charges no lines and is after-used only.
- A dead store with a call on the right-hand side charges nothing and says simplify.
- The viewer's colour-by switch changes tint only.
- The result stays a dataclass with typed properties over `program`; no pydantic, no async (the
  user asked about both; the reasons are in Part 5).

## Left to the builder

- The exact hook names and signatures on `LanguageSpec`, and the tree-sitter node types
  per language.
- The attribute-read bag's name and where the frontend fills it.
- Whether the dead-code verdict is read from diagnostics or stamped as an attr.
- The order of fragments in the viewer's node panel and the exact wording of each finding
  message and suggestion, in the voice of the existing ones.
- How the markdown lists the five codes (grouped under the pass like every other pass).
- Whether `--fail-on` parsing lives in the CLI or on `Metric` (either is fine; one place).
