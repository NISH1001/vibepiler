# vibepiler semantic tier (-O3): evaluation

vibepiler at `d9ca2bb` (v0.4.5 + 2 commits). Embeddings come from vibepiler's own code (`get_embedder` + `embed_texts`, length-sorted batches, 4096-token cap). The only exception is the fp32 baseline, which runs in a separate sentence-transformers venv using the same texts, cap and batching (`batches()` copied verbatim). Raw numbers are in `results.json` and the entrypoint is `scripts/eval/semantic/run_all.sh`. Seeds: sampling 0 / 1000+seed, bootstrap 0.

**Machine**: Apple M3 Max (14 cores: 10 performance, 4 efficiency), 36 GB, macOS 15.8, CPU only. Versions: fastembed 0.9.1, onnxruntime 1.31.1, model2vec 0.10.0, torch 2.15.0, sentence-transformers 5.7.0, transformers 4.57.6 (transformers 5.x breaks CodeRankEmbed's remote code: `NomicBertModel has no attribute get_extended_attention_mask`).

## 1. Quality on labelled semantic clones

**Datasets** (read as text only, never executed):
- **CodeNet Python800**. The IBM CDN host no longer resolves, so the data comes from the HF mirror `qiankunmu/Project_CodeNet_Python800_and_Java250` (sha256 pinned). There are 800 problems and 239,865 accepted solutions after exact-duplicate removal.
  - *dense*: 100 problems × 20 solutions = 2,000 programs and 19,000 same-problem pairs.
  - *sparse* (repo-like): 200 problems give a twin pair and 600 other problems give one distractor each, so 1,000 programs and 200 positive pairs. There are 5 seeds; mean ± sd is reported.
- **GPTCloneBench** (ICSME'23), standalone `true_semantic_clones/py/*/T4`. Each file holds a SemanticCloneBench function plus a GPT-3 rewrite. 487 of 6,116 files could not be split and were skipped. The same original appears in several spellings (`print x` vs `print(x)`), so 579 variants were merged (same name and ≥0.6 token-trigram Jaccard). That leaves 717 groups and 6,843 functions, with a median group size of 8.

### Retrieval (MAP@R with 95% bootstrap CI over problems; P@1)

| model | CodeNet MAP@R | CodeNet P@1 | GCB-T4 MAP@R | GCB-T4 P@1 |
|---|---|---|---|---|
| CodeRankEmbed int8 ONNX (default) | **0.559** [0.522, 0.598] | 0.887 | **0.838** [0.816, 0.860] | 0.930 |
| CodeRankEmbed fp32 (torch) | 0.580 [0.545, 0.616] | 0.900 | 0.842 [0.818, 0.863] | 0.931 |
| jina-v2-base-code int8 | 0.574 [0.540, 0.608] | 0.907 | 0.815 [0.790, 0.838] | 0.924 |
| potion-code-16M-v2 (static) | 0.247 [0.220, 0.276] | 0.666 | 0.697 [0.673, 0.723] | 0.890 |
| bge-small-en-v1.5 (text control) | 0.247 [0.215, 0.279] | 0.644 | 0.821 [0.798, 0.843] | 0.923 |

On CodeNet the three code transformers are tied (the CIs overlap) and land at about 2.3× the static and text models. GCB is easier: GPT rewrites keep the function name and signature, so even the text model scores 0.82. Treat GCB as an upper bound and CodeNet as the real type-4 test.

### Pair detection: what the mutual-NN rule buys

The rules compared:
- (a) vibepiler's rule: mutual nearest neighbours and cosine ≥ the per-model threshold.
- (b) a plain threshold at the same value.
- (c) a plain threshold at the best-F1 value, chosen with hindsight.
- (d) mutual NN at the best threshold.

These are raw rules, without vibepiler's dunder, same-name-method and structural filters.

**CodeNet sparse** (the repo-like setting, 200 twins hidden among 1,000 programs, 5 seeds):

| model (t) | (a) mutual-NN @t P / R / F1 | (b) plain @t P / R / F1 (reported) | (c) best plain F1 | (d) best mutual-NN F1 |
|---|---|---|---|---|
| CodeRankEmbed int8 (0.72) | **0.41±.03 / 0.32 / 0.36** (156) | 0.055 / 0.44 / 0.10 (1,627) | 0.25 (t≈0.80) | 0.38 (t≈0.63–0.72) |
| CodeRankEmbed fp32 (0.72) | 0.42 / 0.32 / 0.36 (152) | 0.055 / 0.46 / 0.10 (1,680) | 0.26 | 0.38 |
| jina int8 (0.82) | 0.41 / 0.24 / 0.30 (116) | 0.21 / 0.28 / 0.24 (266) | 0.26 | 0.37 (t≈0.71) |
| potion (0.90) | 0.23 / **0.04** / 0.07 (33) | 0.15 / 0.04 / 0.07 | 0.08 | 0.14 |
| bge-small (0.88) | 0.17 / 0.13 / 0.15 (144) | 0.006 / 0.35 / 0.01 (11,188) | 0.10 | 0.15 |

**CodeNet dense** (20 solutions per problem, so every program has 19 twins). CodeRankEmbed int8 gets (a) P 0.92, R 0.017, 352 pairs; (b) P 0.64, R 0.42, 12,349 pairs; (c) best F1 0.51 at t=0.71. On **GCB** it gets (a) P 0.95, R 0.032; (b) P 0.65, R 0.87; (c) best F1 0.75. Under the mutual-NN rule each function is in at most one pair, so on dense data its recall is capped at n/2 ÷ positives (5% on CodeNet dense). The quantity that matters there is *function coverage*: the share of functions placed in a correct pair is 33% on CodeNet dense and 45% on GCB.

Findings:
- **The rule does its job.** At the same 0.72 threshold it raises precision 7.5× (0.055 → 0.41) and cuts the list 10× (1,627 → 156) for a 0.12 drop in recall. Its F1 (0.36) beats the best plain threshold you could pick with hindsight (0.25). 0.72 is close to the best mutual-NN threshold (0.63–0.72), so the CodeRankEmbed default is well calibrated.
- **Absolute precision on CodeNet sparse is only 0.41.** The top false positives are dataset artefacts: identical competitive-programming header templates, and 2-line `map(int, input().split())` programs from different problems (examples in `out/codenet_sparse_fp_examples.txt`). Real application code has less of this, but shared boilerplate is a real false-positive mode.
- **Per-model thresholds for the alternatives are miscalibrated.**
  - potion at 0.90 is nearly silent: it finds 4% of twins, against a best-F1 threshold of about 0.85–0.91 for a plain threshold and about 0.72 for mutual NN.
  - jina at 0.82 loses a quarter of CodeRankEmbed's recall; its best mutual-NN threshold is about 0.71.
  - bge at 0.88 sits below its own negative-pair p99 (0.89). With a plain threshold that would report 11k pairs per 1,000 functions.
- **The mutual-NN rule helps every model.** The ranking still holds, and the code-trained transformers are the only usable ones.

### int8 vs fp32 (same model, CodeRankEmbed)

| | CodeNet | GCB-T4 | real repos (vibepiler / rich / django) |
|---|---|---|---|
| ΔMAP@R (int8 − fp32) | **−0.022** (CIs overlap) | −0.004 | – |
| ΔP@1 | −0.013 | −0.001 | – |
| top-10 neighbour overlap | 0.884 | 0.909 | 0.864 / 0.872 / 0.866 |
| top-1 agreement | 0.776 | 0.765 | – |
| cosine(int8 vec, fp32 vec), mean / min | 0.928 / 0.836 | 0.936 / 0.884 | – |
| pairwise-cosine Pearson | 0.975 | 0.974 | – |
| mutual-NN@0.72 pair sets, Jaccard | – | – | 0.84 / 0.84 / 0.83 (django: 1,500 vs 1,561 pairs) |

int8 costs about 2 MAP points on the harder set and almost nothing on the easier one. The pair-detection rule is unaffected (sparse F1 0.360 vs 0.361). About 1 in 6 reported pairs changes between runtimes, and that churn sits at the margin near the threshold.

### Structural tools on the same type-4 clones (CodeNet dense, 19,000 same-problem pairs)

| detector | pairs reported | same-problem (precision) | recall |
|---|---|---|---|
| vibepiler exact shape hash | 34 | 34 (1.00) | 0.18% |
| vibepiler 4-gram shingle Jaccard ≥ 0.85 (its near-duplicate rule) | 66 | 66 (1.00) | 0.35% |
| same rule, ≥ 0.5 | 1,180 | 925 (0.78) | 4.9% |
| same rule, ≥ 0.3 | 11,885 | 4,019 (0.34) | 21% |
| jscpd 4.3.0, default min-tokens 50 | 38 | 12 (0.32) | 0.06% |
| jscpd, min-tokens 20 | 136 | 59 (0.43) | 0.31% |
| *CodeRankEmbed int8, plain t=0.72* | 12,349 | 7,917 (0.64) | 42% |

At their default settings the structural detectors find fewer than 0.4% of same-task pairs. The shingle rule reaches about 5% recall only by giving up precision, falling to 0.78. jscpd's hits are mostly shared I/O boilerplate across *different* problems. The shingle rule is applied to whole programs, without the 25-node minimum. jscpd scanned 1,775 of the 2,000 files.

**Caveats.** CodeNet solutions are whole stdin/stdout programs, not functions in an application. They are short (median 202 chars), template-heavy and come from many authors. GCB rewrites keep identifiers, which inflates every model. Neither dataset has the repo-level cues vibepiler's filters use (class membership, dunder methods, structural duplicates).

## 2. Speed and footprint

These are the texts vibepiler -O3 embeds per repo (dumped through its `EMBEDDER_FACTORY` hook): vibepiler @d9ca2bb, rich v13.9.4, django 4.2.16. Each model ran in its own fresh process, one at a time, with the cache off and the model already on disk.

| model / runtime | vibepiler (573 fn) | rich (930 fn) | django (8,102 fn) | warm load | peak RSS (django) |
|---|---|---|---|---|---|
| CodeRankEmbed int8, fastembed | 24.6 fn/s | 31.5 | 38.7 | 0.4–0.6 s | 3.0 GB |
| CodeRankEmbed fp32, torch | 17.1 | 21.6 | 27.3 | 4.7–5.3 s | 6.2 GB |
| jina-v2-code int8, fastembed | 27.2 | 31.0 | 39.8 | 0.3 s | 5.1 GB |
| bge-small (Qdrant quantized ONNX, **512-token cap**) | 47.7 | 63.2 | 75.0 | 0.2 s | 1.0 GB |
| potion-code-16M static | 13,700 | 14,800 | 19,000 | 0.4 s | 0.2 GB |

- On real repos int8 is **1.4× faster** than fp32, not 2× as the README says (33 vs 16). It uses about half the memory (1.3–3.0 GB vs 5.0–6.2 GB) and loads about 10× faster.
- **Cold start including the first download**, on the CodeNet runs: CodeRankEmbed 10.9 s, jina 6.5 s, bge 3.1 s, potion 2.6 s.
- **Install footprint** (fresh Python 3.12 venvs): `vibepiler` 22 MB, `vibepiler[semantic]` **166 MB**, sentence-transformers + torch **912 MB**.
- **Model download sizes**:
  - CodeRankEmbed int8 ONNX: 132 MB.
  - fp32 safetensors: 522 MB, plus remote code (which needs transformers < 5).
  - jina int8: 156 MB.
  - bge-small quantized: 64 MB.
  - potion: 31 MB.
- **CodeNet fp32 timing is void.** That run took 4.8 h (0.4 fn/s), most likely because the machine slept. The vectors are deterministic and unaffected, and later timings ran under `caffeinate`.

**Embedding cache.** These are `vibepiler compile -O3` runs (CodeRankEmbed, all passes, `--emit json,md`). The cache is isolated with `XDG_CACHE_HOME` pointing at this directory, so the user's `~/.cache` was never touched.

| repo | 1st run (empty cache) | 2nd run (warm) | speedup | peak RSS 1st / 2nd |
|---|---|---|---|---|
| vibepiler | 31.0 s | 6.8 s | 4.6× | 1.3 / 0.65 GB |
| rich | 37.5 s | 8.0 s | 4.7× | 1.7 / 0.85 GB |
| django | 264.0 s | 50.8 s | 5.2× | 4.5 / 3.4 GB |

The warm run is the cost of the rest of the compiler plus model load, so embedding is about 80% of a cold -O3 run.

## 3. In-the-wild precision (top 5 `semantic-duplicates` per repo, CodeRankEmbed int8, hand-labelled)

| repo | # | sim | pair | label | reason |
|---|---|---|---|---|---|
| vibepiler | 1 | .907 | JS `constant` / Python `constant` | TP | same constant-condition folding, per grammar |
| vibepiler | 2 | .769 | `BloatPass._unused` / `_undeclared` | FP | mirror-image checks |
| vibepiler | 3 | .764 | `_module_edges` / `_symbol_edges` | unclear | same loop shape, different edge sets |
| vibepiler | 4 | .753 | `Compiler.run` / `Compiler._run` | FP | one-line wrapper and its target |
| vibepiler | 5 | .743 | `statements` / `_block` | FP | `_block` calls `statements` |
| rich | 1 | .950 | `Align…blank_lines` / `VerticalCenter…blank_lines` | TP | near-copy closures (inside methods, see bug 1) |
| rich | 2 | .929 | `foo` / `bar` (examples/) | FP | deliberate mutual recursion demo |
| rich | 3 | .903 | `Segment._split_cells` / `split_cells` | TP | logic repeated with a fast path (intentional, for caching) |
| rich | 4 | .886 | `print_json` / `Console.print_json` | FP | module function delegates to the method |
| rich | 5 | .884 | `cached_cell_len` / `cell_len` | TP | fallback body re-implemented |
| django | 1 | 1.00 | `QuerySet` / `RawQuerySet` `__aiter__.generator` | TP | identical closures |
| django | 2 | .956 | `quote_name` / `_quote_name` | TP | identical 1-line delegators (low value) |
| django | 3 | .953 | `many_to_one` / `one_to_many` | FP | inverses of each other |
| django | 4 | .950 | `date_trunc_sql` / `datetime_trunc_sql` | TP | identical bodies |
| django | 5 | .947 | MySQL / Oracle geometry `converter` | TP | differ only in `value` vs `value.read()` |

That is **8 TP, 6 FP, 1 unclear**, for an estimated precision of **0.53–0.60** (15 pairs, so this is a rough estimate). By repo: django 4/5, rich 3/5, vibepiler 1/5. vibepiler is small and already deduplicated by eight rounds of self-compilation. There are two systematic false-positive classes:
- **Caller/callee or wrapper pairs.** One of the pair calls the other in 31 of 177 reported pairs (vibepiler 6/10, rich 14/67, django 11/100; `out/caller_pairs.json` in the work dir). That count misses unresolved calls such as `get_console().print_json`.
- **Inverse or mirror pairs**: `_unused`/`_undeclared`, `many_to_one`/`one_to_many`.

## Bugs and issues found

1. **Closures inside methods are embedded.** `symbol_vectors` drops a node whose parent `kind.value == "function"`, but a closure in a *method* has a parent of kind `"method"`. As a result 32 method closures in rich and 103 in django are embedded (`out/closure_check.jsonl`). They appear in 2/67 rich and 9/100 django findings, including 3 of the top 15. The README says nested closures are skipped. Fix: test `in CALLABLE_KINDS`.
2. **Silent truncation at `MAX_REPORTED = 100`.** django hits the cap: before filters the raw rule finds 1,500 pairs. Both `metrics["semantic_duplicates"]["pairs"]` and the report header then say 100, and the waste estimate ("+2547 lines if the semantic pairs merge") counts only those 100. The cap should be shown, and the true count kept.
3. **The rule cannot see wrappers.** 18% of reported pairs are a function and its direct callee. Suppressing pairs joined by a `calls` edge is cheap and would remove most of the vibepiler false positives.
4. **Per-model thresholds** (see §1). potion 0.90 is close to useless (4% of twins found) and jina 0.82 is too strict. Mutual-NN best-F1 thresholds on CodeNet are potion about 0.72, jina about 0.71 and bge about 0.90.
5. **README numbers to update**:
   - int8 vs fp32 is 1.4×, not 2×.
   - vibepiler now embeds 573 functions, not 1,037.
   - bge-small through fastembed is Qdrant's quantized ONNX with a 512-token cap, so "embedded whole" does not hold for it.
   - The jina int8 run peaked at 5.1 GB RSS on django, against 3.0 GB for CodeRankEmbed.

## Reproduce

`bash scripts/eval/semantic/run_all.sh` from the repo root takes about 1.5 h on this machine. Everything it downloads or builds goes to `.eval/semantic/` (gitignored), or to `$VIBEPILER_EVAL_WORK`: `data/` and `cache/` (datasets and models; the user's `~/.cache` is never touched), `repos/`, `venv-torch/` and the footprint venvs, and the raw outputs in `out/`. It writes `results.json` here. The hand labels are in `scripts/eval/semantic/wild_labels.json`.
