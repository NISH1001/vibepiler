"""Embed the CodeNet samples with vibepiler's own shipped embedding code (get_embedder + embed_texts).

usage: embed_vibepiler.py MODEL SAMPLES.json OUT.npz
Run under the vibepiler project env (uv run --project <vibepiler> --extra semantic python -I ...), with
XDG_CACHE_HOME / HF_HOME pointed at the eval dir so model downloads and caches stay isolated.
Writes hashes + unit vectors, and a timing record (load s, embed s, functions/s, peak RSS).
"""

from __future__ import annotations

import hashlib
import json
import resource
import sys
import time
from pathlib import Path

import numpy as np

from vibepiler.semantic.embed import get_embedder, embed_texts

model, samples, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
data = json.loads(samples.read_text())
texts = data["texts"] if "texts" in data else list({x["text"]: None for x in data["dense"]} | {x["text"]: None for v in data["sparse"].values() for x in v})

t0 = time.perf_counter()
emb = get_embedder(model, 4096)  # vibepiler's default embedding_max_tokens
t_load = time.perf_counter() - t0
assert emb is not None, "embedding backend missing"
t1 = time.perf_counter()
vecs = embed_texts(emb, texts, use_cache=False)  # length-sorted batches exactly as shipped; no cache so timing is honest
t_embed = time.perf_counter() - t1
rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1 << 20 if sys.platform == "darwin" else 1 << 10)
np.savez(out, hashes=np.array([hashlib.sha1(t.encode()).hexdigest() for t in texts]), vectors=vecs.astype(np.float32))
rec = {"model": model, "runtime": type(emb).__name__, "name": emb.name, "n": len(texts), "chars": sum(map(len, texts)),
       "load_s": round(t_load, 2), "embed_s": round(t_embed, 2), "per_s": round(len(texts) / t_embed, 1), "peak_rss_mb": round(rss)}
out.with_suffix(".json").write_text(json.dumps(rec))
print(json.dumps(rec))
