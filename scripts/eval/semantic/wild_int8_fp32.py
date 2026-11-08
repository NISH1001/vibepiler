"""On the real repos: does int8 change which pairs the mutual-NN + 0.72 rule reports, versus fp32? (rule only, before
vibepiler's dunder / same-name-method / structural-duplicate filters). Also top-10 neighbour overlap."""
import hashlib, json, sys
from pathlib import Path
import numpy as np
OUT = Path(sys.argv[1]); res = []
def load(r, k, texts):
    d = np.load(OUT / "bench" / f"{r}_{k}.npz"); by = dict(zip(d["hashes"].tolist(), d["vectors"]))
    v = np.stack([by[hashlib.sha1(t.encode()).hexdigest()] for t in texts]).astype(np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)
def rule(v, t=0.72):
    s = v @ v.T; np.fill_diagonal(s, -np.inf); nn = s.argmax(1)
    return s, {(i, int(nn[i])) for i in range(len(v)) if nn[i] > i and nn[nn[i]] == i and s[i, nn[i]] >= t}
for r in ("vibepiler", "rich", "django"):
    texts = json.loads((OUT / f"repo_{r}_texts.json").read_text())["texts"]
    sa, a = rule(load(r, "cre_int8", texts)); sb, b = rule(load(r, "cre_fp32", texts))
    ta, tb = np.argsort(-sa, 1)[:, :10], np.argsort(-sb, 1)[:, :10]
    ov = float(np.mean([len(set(x) & set(y)) / 10 for x, y in zip(ta, tb)]))
    rec = {"repo": r, "functions": len(texts), "int8_pairs": len(a), "fp32_pairs": len(b), "both": len(a & b), "jaccard": round(len(a & b) / max(1, len(a | b)), 3), "overlap_at_10": round(ov, 3)}
    res.append(rec); print(json.dumps(rec))
(OUT / "wild_int8_fp32.json").write_text(json.dumps(res, indent=1))
