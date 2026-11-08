"""Show the highest-similarity false positives of the mutual-NN rule on a sparse CodeNet sample (for the write-up)."""
import hashlib, json, sys
from pathlib import Path
import numpy as np
OUT = Path(sys.argv[1]); key = sys.argv[2]; seed = sys.argv[3] if len(sys.argv) > 3 else "0"
items = json.loads((OUT / "samples.json").read_text())["sparse"][seed]
d = np.load(OUT / f"emb_{key}.npz"); by = dict(zip(d["hashes"].tolist(), d["vectors"]))
v = np.stack([by[hashlib.sha1(x["text"].encode()).hexdigest()] for x in items]); v /= np.linalg.norm(v, axis=1, keepdims=True)
s = v @ v.T; np.fill_diagonal(s, -np.inf); nn = s.argmax(1)
fp = sorted([(s[i, nn[i]], i, nn[i]) for i in range(len(items)) if nn[i] > i and nn[nn[i]] == i and items[i]["label"] != items[nn[i]]["label"]], reverse=True)
lens = [min(len(items[i]["text"]), len(items[j]["text"])) for _, i, j in fp]
tps = [min(len(items[i]["text"]), len(items[j]["text"])) for i in range(len(items)) for j in [nn[i]] if j > i and nn[j] == i and items[i]["label"] == items[j]["label"]]
print("FP pairs", len(fp), "median shorter-program chars", int(np.median(lens)), "| TP median", int(np.median(tps)))
for sim, i, j in fp[: int(sys.argv[4]) if len(sys.argv) > 4 else 4]:
    print(f"--- {sim:.3f} {items[i]['id']} vs {items[j]['id']}"); print(items[i]["text"][:300]); print("  ~~~"); print(items[j]["text"][:300])
