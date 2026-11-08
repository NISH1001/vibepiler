#!/usr/bin/env bash
# vibepiler compile -O3, twice per repo: first with an empty embedding cache, then warm. Wall time + peak RSS.
# The embedding cache lives under $XDG_CACHE_HOME/vibepiler/embeddings; XDG_CACHE_HOME points at this eval's own
# cache dir, so clearing it never touches the user's ~/.cache. Models stay downloaded (cold start = load, not download).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"                  # scripts/eval/semantic
VP="$(cd "$HERE/../../.." && pwd)"                      # the vibepiler checkout
EVAL="${VIBEPILER_EVAL_WORK:-$VP/.eval/semantic}"      # data, models, clones, raw outputs (gitignored)
export XDG_CACHE_HOME="$EVAL/cache" HF_HOME="$EVAL/cache/hf"
MODEL="${MODEL:-nomic-ai/CodeRankEmbed}"
TAG="${TAG:-cre_int8}"
mkdir -p "$EVAL/out/cli"
for r in ${REPOS:-vibepiler rich django}; do
  rm -rf "$XDG_CACHE_HOME/vibepiler/embeddings"
  for run in 1 2; do
    /usr/bin/time -l uv run --project "$VP" --extra semantic vibepiler compile "$EVAL/repos/$r" -O3 --model "$MODEL" \
      -o "$EVAL/out/cli/${r}_${TAG}" --emit json,md -q > "$EVAL/out/cli/${r}_${TAG}_run$run.log" 2> "$EVAL/out/cli/${r}_${TAG}_run$run.time" || true
    wall=$(grep -E ' real ' "$EVAL/out/cli/${r}_${TAG}_run$run.time" | awk '{print $1}')
    rss=$(grep 'maximum resident set size' "$EVAL/out/cli/${r}_${TAG}_run$run.time" | awk '{print $1}')
    echo "{\"repo\": \"$r\", \"model\": \"$MODEL\", \"run\": $run, \"wall_s\": $wall, \"peak_rss_mb\": $((rss / 1048576))}" | tee -a "$EVAL/out/cli_cache.jsonl"
  done
done
