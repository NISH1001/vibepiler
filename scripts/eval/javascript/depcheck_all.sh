#!/bin/sh
# depcheck once per package.json directory (run from inside it); one JSON object per line: {"dir": ..., "result": ...}
bin="$1"; shift
top=$(pwd)
for d in "$@"; do
  r=$(cd "$top/$d" && "$bin" --json 2>/dev/null)
  [ -z "$r" ] && r='null'
  printf '{"dir": "%s", "result": %s}\n' "$d" "$r"
done
