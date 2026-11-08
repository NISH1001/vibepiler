#!/usr/bin/env bash
# Throughput / load time / peak RSS per model and runtime, on the exact texts vibepiler -O3 embeds for each repo.
# One fresh process per (repo, model), run one after another (nothing else running), cache off, models already on disk.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"                  # scripts/eval/semantic
VP="$(cd "$HERE/../../.." && pwd)"                      # the vibepiler checkout
EVAL="${VIBEPILER_EVAL_WORK:-$VP/.eval/semantic}"      # data, models, clones, raw outputs (gitignored)
export XDG_CACHE_HOME="$EVAL/cache" HF_HOME="$EVAL/cache/hf"
mkdir -p "$EVAL/out/bench"
for r in ${REPOS:-vibepiler rich django}; do
  T="$EVAL/out/repo_${r}_texts.json"
  for m in nomic-ai/CodeRankEmbed:cre_int8 jinaai/jina-embeddings-v2-base-code:jina_int8 minishlab/potion-code-16M-v2:potion BAAI/bge-small-en-v1.5:bge_small; do
    uv run --project "$VP" --extra semantic python -I "$HERE/embed_vibepiler.py" "${m%%:*}" "$T" "$EVAL/out/bench/${r}_${m##*:}.npz" 2>/dev/null | tail -1
  done
  "$EVAL/venv-torch/bin/python" -I "$HERE/embed_torch.py" "$T" "$EVAL/out/bench/${r}_cre_fp32.npz" nomic-ai/CodeRankEmbed 3c4b60807d71f79b43f3c4363786d9493691f8b1 2>/dev/null | tail -1
done
