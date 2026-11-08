#!/usr/bin/env bash
# Install footprint (fresh venvs) and model download sizes. Prints one JSON object.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"                  # scripts/eval/semantic
VP="$(cd "$HERE/../../.." && pwd)"                      # the vibepiler checkout
EVAL="${VIBEPILER_EVAL_WORK:-$VP/.eval/semantic}"      # data, models, clones, raw outputs (gitignored)
mkdir -p "$EVAL" && cd "$EVAL"
[ -d venv-vp-base ] || { uv venv -q --python 3.12 venv-vp-base && VIRTUAL_ENV="$EVAL/venv-vp-base" uv pip install -q "$VP"; }
[ -d venv-vp-semantic ] || { uv venv -q --python 3.12 venv-vp-semantic && VIRTUAL_ENV="$EVAL/venv-vp-semantic" uv pip install -q "$VP[semantic]"; }
# HF hub layout keeps each file once in blobs/ and links it from snapshots/: count the snapshot files, dereferenced, once
mb() { if [ -d "$1/snapshots" ]; then find -L "$1/snapshots" -type f -exec stat -L -f %z {} + | awk '{s+=$1} END {printf "%d", s/1048576}'; else du -sk "$1" | awk '{printf "%d", $1/1024}'; fi; }
echo "{"
echo " \"venv_mb\": {\"vibepiler\": $(mb venv-vp-base), \"vibepiler[semantic]\": $(mb venv-vp-semantic), \"sentence-transformers+torch (fp32 baseline)\": $(mb venv-torch)},"
echo " \"model_mb\": {"
echo "  \"CodeRankEmbed int8 ONNX (vibepiler cache)\": $(mb cache/vibepiler/models/nomic-ai__CodeRankEmbed),"
echo "  \"jina-v2-base-code int8 ONNX\": $(mb cache/vibepiler/models/jinaai__jina-embeddings-v2-base-code),"
b=$(ls -d cache/vibepiler/models/*bge* 2>/dev/null | head -1); echo "  \"bge-small-en-v1.5 (fastembed, its quantized ONNX)\": $( [ -n "$b" ] && mb "$b" || echo null),"
echo "  \"potion-code-16M-v2 (HF hub)\": $(mb cache/hf/hub/models--minishlab--potion-code-16M-v2),"
echo "  \"CodeRankEmbed fp32 safetensors + remote code (HF hub)\": $(mb cache/hf/hub/models--nomic-ai--CodeRankEmbed)"
echo " }"
echo "}"
