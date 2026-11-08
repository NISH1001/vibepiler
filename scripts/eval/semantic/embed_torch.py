"""fp32 PyTorch baseline: nomic-ai/CodeRankEmbed via sentence-transformers (trust_remote_code, as its model card shows).

usage: embed_torch.py SAMPLES.json OUT.npz [MODEL] [REVISION]
Runs in the separate venv-torch (never in the vibepiler env). Same texts, same 4096-token cap, same
length-sorted batching as vibepiler (batches() is copied verbatim so the comparison is apples to apples).
Documents (code) get no query prefix, as in vibepiler.
"""

from __future__ import annotations

import hashlib
import json
import resource
import sys
import time
from pathlib import Path

import numpy as np

BATCH_MAX, BATCH_CHAR_BUDGET = 32, 32 * 800
BATCH_SQUARE_BUDGET = BATCH_MAX * 800**2


def batches(texts, max_batch=BATCH_MAX, char_budget=BATCH_CHAR_BUDGET):  # copied from vibepiler.semantic.embed
    order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
    out, i = [], 0
    while i < len(order):
        j = i + 1
        while j < len(order) and j - i < max_batch and (w := len(texts[order[j]])) * (j - i + 1) <= char_budget and (j - i + 1) * w * w <= BATCH_SQUARE_BUDGET:
            j += 1
        out.append(order[i:j])
        i = j
    return out


def load(model: str, revision: str | None):
    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(max(1, torch.get_num_threads()))
    m = SentenceTransformer(model, trust_remote_code=True, revision=revision, device="cpu")
    m.max_seq_length = 4096
    return m


def embed(m, texts):
    vecs = np.zeros((len(texts), m.get_sentence_embedding_dimension()), dtype=np.float32)
    for b in batches(texts):
        vecs[b] = m.encode([texts[k] for k in b], batch_size=len(b), normalize_embeddings=True, convert_to_numpy=True)
    return vecs


def main() -> None:
    samples, out = Path(sys.argv[1]), Path(sys.argv[2])
    model = sys.argv[3] if len(sys.argv) > 3 else "nomic-ai/CodeRankEmbed"
    revision = sys.argv[4] if len(sys.argv) > 4 else None
    data = json.loads(samples.read_text())
    texts = data["texts"] if "texts" in data else list({x["text"]: None for x in data["dense"]} | {x["text"]: None for v in data["sparse"].values() for x in v})
    t0 = time.perf_counter()
    m = load(model, revision)
    t_load = time.perf_counter() - t0
    t1 = time.perf_counter()
    vecs = embed(m, texts)
    t_embed = time.perf_counter() - t1
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1 << 20 if sys.platform == "darwin" else 1 << 10)
    np.savez(out, hashes=np.array([hashlib.sha1(t.encode()).hexdigest() for t in texts]), vectors=vecs)
    import torch
    rec = {"model": model, "runtime": f"sentence-transformers/torch {torch.__version__} fp32", "n": len(texts), "load_s": round(t_load, 2),
           "embed_s": round(t_embed, 2), "per_s": round(len(texts) / t_embed, 1), "peak_rss_mb": round(rss), "threads": torch.get_num_threads()}
    out.with_suffix(".json").write_text(json.dumps(rec))
    print(json.dumps(rec))


if __name__ == "__main__":
    main()
