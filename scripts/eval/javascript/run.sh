#!/bin/sh
# One entrypoint: clone pinned corpus, install deps with scripts disabled, install tools, bench, analyze, render.
# usage: sh scripts/eval/javascript/run.sh [--skip-setup]
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)             # scripts/eval/javascript
ROOT=$(cd "$HERE/../../.." && pwd)            # the vibepiler checkout
EVAL="${VIBEPILER_EVAL_WORK:-$ROOT/.eval/javascript}"  # clones, tools, raw outputs (gitignored)
mkdir -p "$EVAL"
cd "$EVAL"
if [ "${1:-}" != "--skip-setup" ]; then
  mkdir -p repos tools out
  # comparison tools, versions recorded in out/machine.json
  (cd tools && [ -f package.json ] || echo '{"name":"eval-tools","private":true}' > package.json)
  (cd tools && npm install --ignore-scripts --no-audit --no-fund knip@6.44.2 ts-prune@0.10.3 ts-unused-exports@11.0.1 \
     depcheck@1.4.7 jscpd@5.4.3 madge@8.0.0 eslint@10.14.0 eslint-plugin-sonarjs@4.2.2 typescript-eslint@8.72.1 \
     @typescript-eslint/parser@8.72.1 typescript@6.0.3 pnpm@12.18.0)
  cp "$HERE/eslint.config.mjs" tools/eslint.config.mjs   # must sit next to tools/node_modules to resolve its plugins
  url() { case "$1" in
    zustand) echo https://github.com/pmndrs/zustand;; release-it) echo https://github.com/release-it/release-it;;
    taxonomy) echo https://github.com/shadcn-ui/taxonomy;; excalidraw) echo https://github.com/excalidraw/excalidraw;;
    scuttlepay) echo https://github.com/jarrodwatts/scuttlepay;; tower-of-time) echo https://github.com/maciej-trebacz/tower-of-time-game;;
  esac; }
  while read -r name sha; do
    if [ ! -d "repos/$name/.git" ]; then
      git init -q "repos/$name" && git -C "repos/$name" fetch -q --depth 1 "$(url "$name")" "$sha" && git -C "repos/$name" checkout -q FETCH_HEAD
    fi
    # dependencies for knip/depcheck/ts tools: lifecycle scripts never run
    if [ ! -d "repos/$name/node_modules" ]; then
      if [ "$name" = excalidraw ]; then
        (cd "repos/$name" && npm install --ignore-scripts --no-audit --no-fund --legacy-peer-deps)
      else
        (cd "repos/$name" && "$EVAL/tools/node_modules/.bin/pnpm" install --ignore-scripts --config.engine-strict=false \
           --config.manage-package-manager-versions=false)
      fi
    fi
  done < "$HERE/pins.txt"
fi
python3 -I "$HERE/machine.py"
python3 -I "$HERE/bench.py" --warm 3
python3 -I "$HERE/analyze.py"
python3 -I "$HERE/report.py"
