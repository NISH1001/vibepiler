#!/usr/bin/env bash
# One entrypoint for the whole semantic-tier evaluation. Seeds are fixed in the scripts (sample.py: 0 and 1000+seed;
# metrics.py bootstrap: 0). Downloads go to data/ (untrusted, read as text only) and cache/ (models, isolated
# XDG_CACHE_HOME/HF_HOME so the user's ~/.cache is never touched). Takes ~1.5 h on an M3 Max, mostly embedding.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"                  # scripts/eval/semantic
VP="$(cd "$HERE/../../.." && pwd)"                      # the vibepiler checkout
EVAL="${VIBEPILER_EVAL_WORK:-$VP/.eval/semantic}"      # data, models, clones, raw outputs (gitignored)
mkdir -p "$EVAL" && cd "$EVAL"
export XDG_CACHE_HOME="$EVAL/cache" HF_HOME="$EVAL/cache/hf"
UVP=(uv run --project "$VP" --extra semantic python -I)
mkdir -p data/codenet data/gptclonebench out repos tools

# --- data (pinned): CodeNet Python800 (HF mirror of IBM's tarball; the IBM CDN host no longer resolves), GPTCloneBench
[ -d data/codenet/Project_CodeNet_Python800 ] || { curl -sL -o data/codenet/Python800.tar.gz \
  https://huggingface.co/datasets/qiankunmu/Project_CodeNet_Python800_and_Java250/resolve/9e4c7b4da87e8ed66a9311d643d3edee675929a2/Project_CodeNet_Python800.tar.gz
  echo "39297d11df8030ce0b6619e678547a738d74c4715c5c9a5be0af83941a5587b0  data/codenet/Python800.tar.gz" | shasum -a 256 -c
  tar xzf data/codenet/Python800.tar.gz -C data/codenet; }
[ -d data/gptclonebench/standalone ] || { curl -sL -o data/gptclonebench/semantic.zip \
  https://github.com/srlabUsask/GPTCloneBench/raw/main/GPTCloneBench_semantic_standalone_clones.zip
  (cd data/gptclonebench && unzip -q semantic.zip 'standalone/true_semantic_clones/py/*' 'standalone/README.md'); }

# --- repos, pinned
[ -d repos/rich ] || git -c advice.detachedHead=false clone -q --depth 1 --branch v13.9.4 https://github.com/Textualize/rich repos/rich
[ -d repos/django ] || git -c advice.detachedHead=false clone -q --depth 1 --branch 4.2.16 https://github.com/django/django repos/django
[ -d repos/vibepiler ] || { git clone -q "$VP" repos/vibepiler && git -C repos/vibepiler checkout -q d9ca2bb; }

# --- PyTorch baseline env (separate; torch never enters the vibepiler env)
[ -x venv-torch/bin/python ] || { uv venv -q --python 3.12 venv-torch
  VIRTUAL_ENV="$EVAL/venv-torch" uv pip install -q torch==2.15.0 sentence-transformers==5.7.0 transformers==4.57.6 einops numpy; }
[ -x tools/node_modules/.bin/jscpd ] || (cd tools && npm init -y >/dev/null && npm i -s jscpd@4.3.0)

# --- 1. quality
python3 -I "$HERE/sample.py" data/codenet/Project_CodeNet_Python800 out/samples.json
python3 -I "$HERE/sample_gcb.py" data/gptclonebench out/samples_gcb.json
for m in nomic-ai/CodeRankEmbed:cre_int8 jinaai/jina-embeddings-v2-base-code:jina_int8 minishlab/potion-code-16M-v2:potion BAAI/bge-small-en-v1.5:bge_small; do
  "${UVP[@]}" "$HERE/embed_vibepiler.py" "${m%%:*}" out/samples.json "out/emb_${m##*:}.npz"
  "${UVP[@]}" "$HERE/embed_vibepiler.py" "${m%%:*}" out/samples_gcb.json "out/emb_${m##*:}_gcb.npz"
done
venv-torch/bin/python -I "$HERE/embed_torch.py" out/samples.json out/emb_cre_fp32.npz nomic-ai/CodeRankEmbed 3c4b60807d71f79b43f3c4363786d9493691f8b1
venv-torch/bin/python -I "$HERE/embed_torch.py" out/samples_gcb.json out/emb_cre_fp32_gcb.npz nomic-ai/CodeRankEmbed 3c4b60807d71f79b43f3c4363786d9493691f8b1
"${UVP[@]}" "$HERE/metrics.py" out
"${UVP[@]}" "$HERE/structural.py" out >/dev/null
for mt in 50 20; do
  tools/node_modules/.bin/jscpd out/dense_files --format python --min-tokens $mt --reporters json --output out/jscpd_mt$mt --silent >/dev/null 2>&1 || true
  "${UVP[@]}" "$HERE/structural.py" out out/jscpd_mt$mt/jscpd-report.json >/dev/null && cp out/structural.json out/structural_mt$mt.json
done

# --- 2. speed and footprint
for r in vibepiler rich django; do "${UVP[@]}" "$HERE/repo_texts.py" repos/$r out/repo_${r}_texts.json; done
rm -f out/cli_cache.jsonl
caffeinate -i bash "$HERE/bench_repos.sh" | tee out/bench.jsonl   # caffeinate: a sleeping laptop ruins timings
bash "$HERE/cli_cache.sh"
bash "$HERE/footprint.sh" | tee out/footprint.json

# --- 3. in-the-wild pairs (hand labels in scripts/eval/semantic/wild_labels.json)
"${UVP[@]}" "$HERE/wild_pairs.py" out
"${UVP[@]}" "$HERE/wild_int8_fp32.py" out
python3 -I "$HERE/caller_pairs.py" out
"${UVP[@]}" "$HERE/closure_check.py" repos vibepiler rich django > out/closure_check.jsonl
"${UVP[@]}" "$HERE/inspect_fp.py" out cre_int8 0 4 > out/codenet_sparse_fp_examples.txt
"${UVP[@]}" "$HERE/inspect_gcb.py" out cre_int8 > out/gcb_label_noise.txt
"${UVP[@]}" "$HERE/collect.py" out
