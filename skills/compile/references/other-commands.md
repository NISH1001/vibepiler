# The other vibepiler commands

`compile` writes every report. These answer narrower questions faster, and print to the terminal.

| Question | Command |
|---|---|
| How bloated is each folder? (for a PR comment) | `vibepiler size . [paths...] [--top N]` |
| What else does the same job as this function, class or module? | `vibepiler similar . <symbol> -n 10` (needs `vibepiler[semantic]`) |
| What calls what, from an entry point? | `vibepiler paths . --from app.main.main [--to app.db.save] [--depth 6]` |
| The module or symbol graph, for a diagram | `vibepiler graph . --level module --format mermaid` (or `dot`, `json`; `--packages` adds external ones) |
| Which passes exist and what they compare to | `vibepiler passes` |

A symbol is named by its qualified name (`app.utils.render_email`, `ClassName.method`) or by a file path for a module.
