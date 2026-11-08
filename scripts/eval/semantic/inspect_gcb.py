"""GPTCloneBench label noise check: how many cross-group mutual-NN pairs define a function with the same name
(i.e. likely the same task split across two SemanticCloneBench originals)."""
import hashlib, json, re, sys, collections
from pathlib import Path
import numpy as np
OUT = Path(sys.argv[1]); key = sys.argv[2]
items = json.loads((OUT / "samples_gcb.json").read_text())["dense"]
by = {}
for f in (OUT / f"emb_{key}_gcb.npz", OUT / f"emb_{key}_gcb2.npz"):
    d = np.load(f) if f.exists() else None
    if d is None: continue
    by.update(zip(d["hashes"].tolist(), d["vectors"]))
v = np.stack([by[hashlib.sha1(x["text"].encode()).hexdigest()] for x in items]); v /= np.linalg.norm(v, axis=1, keepdims=True)
s = v @ v.T; np.fill_diagonal(s, -np.inf); nn = s.argmax(1)
name = lambda t: (re.search(r"def\s+(\w+)", t) or re.search(r"class\s+(\w+)", t) or [None, None])[1]
fp = [(i, int(nn[i])) for i in range(len(items)) if nn[i] > i and nn[nn[i]] == i and s[i, nn[i]] >= 0.72 and items[i]["label"] != items[nn[i]]["label"]]
same = sum(name(items[i]["text"]) == name(items[j]["text"]) for i, j in fp)
sizes = collections.Counter(x["label"] for x in items)
print(f"{key}: mutual-NN FP pairs >=0.72: {len(fp)}, same top-level name: {same} ({same/len(fp):.0%}); group size median {int(np.median(list(sizes.values())))}, max {max(sizes.values())}")
labels_by_name = collections.defaultdict(set)
for x in items: labels_by_name[name(x["text"])].add(x["label"])
print("names spanning >1 group:", sum(len(v) > 1 for v in labels_by_name.values()), "of", len(labels_by_name))
for i, j in fp[:2]:
    print("---", items[i]["text"][:250], "\n ~~~\n", items[j]["text"][:250])
