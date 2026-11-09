#!/usr/bin/env bash
# Reproduce the Python evaluation: clone the pinned corpus, install the tools, time, analyze, sample.
# Cloned code is only ever passed to tools as a path; none of it is executed.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"            # scripts/eval/python
ROOT="$(cd "$HERE/../../.." && pwd)"           # the vibepiler checkout
E="${VIBEPILER_EVAL_WORK:-$ROOT/.eval/python}"  # clones, tools, raw outputs (gitignored)
mkdir -p "$E/repos" "$E/out"
cd "$E"
clone() {  # name url sha
  [ -d "repos/$1" ] && return
  git init -q "repos/$1" && git -C "repos/$1" fetch -q --depth 1 "$2" "$3" && git -C "repos/$1" checkout -q FETCH_HEAD
}
clone requests  https://github.com/psf/requests          0e322af87745eff34caffe4df68456ebc20d9068   # v2.32.3
clone flask     https://github.com/pallets/flask         ab8149664182b662453a563161aa89013c806dc9   # 3.1.0
clone httpx     https://github.com/encode/httpx          26d48e0634e6ee9cdc0533996db289ce4b430177   # 0.28.1
clone rich      https://github.com/Textualize/rich       43d3b04725ab9731727fb1126e35980c62f32377   # v13.9.4
clone black     https://github.com/psf/black             1b2427a2b785cc4aac97c19bb4b9a0de063f9547   # 24.10.0
clone django    https://github.com/django/django         2d4add11fd57b05f7ea48e8b3e89e743c9871aa3   # 5.1.4
clone gpython   https://github.com/igaurab/gpython       c55944f5c44d6b2cf1c3cda931b7469cb19bf6b6   # main
clone vibepiler https://github.com/NISH1001/vibepiler    d9ca2bbfce3bae1356bb3fa18673563d67fb8d5c   # main (local HEAD at eval time)

# every tool, not just the first: a half-installed venv made the others exit 127 and broke analyze.py
ok=1; for t in vulture deadcode ruff deptry pylint radon complexipy; do [ -x tools/bin/$t ] || ok=0; done
[ $ok = 1 ] || { { [ -x tools/bin/python ] || uv venv -q tools; } && uv pip install -q -p tools/bin/python \
  vulture==2.16 deadcode==2.6.0 ruff==0.17.4 deptry==0.25.1 creosote==5.2.0 pylint==4.1.3 radon==6.0.1 complexipy==8.3.0; }
[ -x node/node_modules/.bin/jscpd ] || npm install -s --prefix node jscpd@4.0.5

python3 -I "$HERE/bench.py"          # timings -> out/timings.json, raw tool outputs -> out/<repo>/<tool>/run<N>/
python3 -I "$HERE/analyze.py"        # determinism, agreement -> out/analysis.json, out/disagreements.json
python3 -I "$HERE/sample.py" > out/samples.json   # what was hand-verified (labels are in RESULTS.md / results.json)
python3 -I "$HERE/report.py"         # tables -> results.json and the tables in RESULTS.md
