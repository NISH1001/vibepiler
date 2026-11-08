# vibepiler compile: the flags

`vibepiler compile <source> [paths...] [flags]`. The source is `.`, a folder, one file, or a remote repo (`owner/repo`, `owner/repo@ref`, a GitHub URL, any git URL; cloned into `~/.cache/vibepiler`).

## How deep

| Flag | Runs |
|---|---|
| `-O0` | the program graph only, no passes |
| `-O1` | the linear passes: dead code, liveness, unused imports, cycles, complexity, measures, wrappers, hotspots, abstraction, dependency bloat |
| `-O2` | the default: adds duplicate detection |
| `-O3` | adds the embedding tier: semantic duplicates, mixed responsibilities, the concept map. Needs `vibepiler[semantic]`; the first run downloads the model |

## What to read and report

- `paths...` after the source: report on these folders or files only. The whole source is still read, so callers outside them count.
- `--isolate`: read only the named paths (faster, but calls from outside are not seen).
- `--exclude PATTERN`: gitignore-style patterns left out, comma-separated or repeated. `.gitignore` already applies.
- `--lang python,typescript`: restrict languages.

## Output

- `-o DIR`: output folder (default `vibepiler-out/`, or `vibepiler-out/<repo>` for a remote).
- `--emit json,md,html`: which files to write (default all: json, dot, mermaid, html, md).
- `-q`: only the phase lines and the summary. `-v`: every stage with time and memory.
- `-W all|warning|error`: the lowest severity printed (`-Wall` shows infos). The files always hold everything.
- `-R`: print the elimination plan, one edit per line with the lines it frees and its confidence.
- `--max-print N`: findings printed to the terminal.

## Comparing and gating

- `--baseline vibepiler-out/ir.json`: print how waste, tangle, bloat and the metrics moved since that run. Copy the old `ir.json` aside first if the new run writes to the same folder.
- `--fail-on warning|error`: exit 1 when a finding is at or above that severity.
- `--fail-on "bloat>0.25"` or `--fail-on "live<0.9"`: exit 1 when a score crosses the line. The labels that gate: bloat, live, cognitive, cyclomatic, mi, typed, documented (waste and tangle are parts of bloat, not gates).

## -O3 embedding

- `--max-tokens N`: tokens embedded per function (default 4096; memory grows with its square; 0 means the model's whole context).
- `--no-cache`: embed everything afresh, without reading or writing `~/.cache/vibepiler/embeddings`.
- `--model NAME`: the embedding model; `minishlab/potion-code-16M-v2` is the fast static one.

## Remote sources

- `--cache DIR`: where remote repos are cloned. `--refresh`: clone afresh.

## Passes

- `--only dead-code,duplicates` or `--skip concepts`: run a subset. `vibepiler passes` lists them all.

Project defaults live in `[tool.vibepiler]` in `pyproject.toml` or in `vibepiler.toml`, including `accept = ["code", "code:qualname-glob"]` for findings judged intentional (counted, not reported).
