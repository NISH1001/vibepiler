"""Quality metrics on the CodeNet samples from the saved vectors (pure numpy; runs in either env).

usage: metrics.py OUT_DIR   (reads OUT_DIR/samples.json and OUT_DIR/emb_*.npz, writes OUT_DIR/quality.json)
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

OUT = Path(sys.argv[1])
MODELS = {  # key: (label, vibepiler per-model semantic_similarity threshold)
    "cre_int8": ("CodeRankEmbed int8 ONNX (default)", 0.72),
    "cre_fp32": ("CodeRankEmbed fp32 torch", 0.72),
    "jina_int8": ("jina-v2-base-code int8 ONNX", 0.82),
    "potion": ("potion-code-16M-v2 static", 0.90),
    "bge_small": ("bge-small-en-v1.5 (text control)", 0.88),
}
RNG = np.random.default_rng(0)


def h(t: str) -> str:
    return hashlib.sha1(t.encode()).hexdigest()


def vectors(key: str, items: list[dict]) -> np.ndarray | None:
    by = {}
    for f in (OUT / f"emb_{key}.npz", OUT / f"emb_{key}_gcb.npz", OUT / f"emb_{key}_gcb2.npz"):
        if f.exists():
            d = np.load(f)
            by.update(zip(d["hashes"].tolist(), d["vectors"]))
    if not all(h(x["text"]) in by for x in items):
        return None
    v = np.stack([by[h(x["text"])] for x in items]).astype(np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def sim_matrix(v: np.ndarray) -> np.ndarray:
    s = v @ v.T
    np.fill_diagonal(s, -np.inf)
    return s


def retrieval(s: np.ndarray, labels: np.ndarray) -> dict:
    """MAP@R (Musgrave et al. 2020) and P@1, with a 95% bootstrap CI over problems."""
    order = np.argsort(-s, axis=1, kind="stable")
    ap, p1 = np.zeros(len(labels)), np.zeros(len(labels))
    for q in range(len(labels)):
        R = int((labels == labels[q]).sum() - 1)
        rel = labels[order[q, :R]] == labels[q]
        prec = np.cumsum(rel) / np.arange(1, R + 1)
        ap[q] = (prec * rel).sum() / R
        p1[q] = rel[0]
    probs = np.unique(labels)
    groups = [np.nonzero(labels == p)[0] for p in probs]
    boot = []
    for _ in range(1000):
        pick = RNG.integers(0, len(probs), len(probs))
        idx = np.concatenate([groups[i] for i in pick])
        boot.append(ap[idx].mean())
    return {"map_at_r": round(float(ap.mean()), 4), "map_at_r_ci95": [round(float(x), 4) for x in np.percentile(boot, [2.5, 97.5])], "p_at_1": round(float(p1.mean()), 4)}


def prf(tp: int, npred: int, npos: int) -> dict:
    p = tp / npred if npred else 0.0
    r = tp / npos if npos else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(2 * p * r / (p + r), 4) if p + r else 0.0, "reported": int(npred), "tp": int(tp)}


def best_f1(sims: np.ndarray, pos: np.ndarray, npos: int) -> dict:
    order = np.argsort(-sims, kind="stable")
    tp = np.cumsum(pos[order])
    k = np.arange(1, len(order) + 1)
    f1 = 2 * tp / (k + npos)
    i = int(np.argmax(f1))
    return prf(int(tp[i]), i + 1, npos) | {"threshold": round(float(sims[order[i]]), 4)}


def pairs(s: np.ndarray, labels: np.ndarray, t: float) -> dict:
    n = len(labels)
    iu = np.triu_indices(n, 1)
    sims, pos = s[iu], labels[iu[0]] == labels[iu[1]]
    npos = int(pos.sum())
    nn = np.argmax(s, axis=1)
    mutual = [(i, int(nn[i])) for i in range(n) if nn[i] > i and nn[nn[i]] == i]
    msim = np.array([s[i, j] for i, j in mutual])
    mpos = np.array([labels[i] == labels[j] for i, j in mutual])
    keep = msim >= t
    covered = len({x for (i, j), k, p in zip(mutual, keep, mpos) if k and p for x in (i, j)})
    has_twin = int(sum((labels == l).sum() > 1 for l in labels))
    neg = sims[~pos]
    return {
        "n": n, "positives": npos, "mutual_nn_pairs": len(mutual), "mutual_nn_pair_precision_any_threshold": round(float(mpos.mean()), 4),
        "recall_ceiling_mutual_nn": round(min(n // 2, npos) / npos, 4),
        "a_mutual_nn_at_model_t": prf(int((mpos & keep).sum()), int(keep.sum()), npos) | {"function_coverage": round(covered / has_twin, 4)},
        "b_plain_threshold_at_model_t": prf(int((pos & (sims >= t)).sum()), int((sims >= t).sum()), npos),
        "c_plain_threshold_best_f1": best_f1(sims, pos, npos),
        "d_mutual_nn_best_f1": best_f1(msim, mpos, npos) if len(msim) else None,
        "neg_p99": round(float(np.quantile(neg, 0.99)), 4), "neg_p999": round(float(np.quantile(neg, 0.999)), 4),
        "pos_median": round(float(np.median(sims[pos])), 4), "neg_median": round(float(np.median(neg)), 4),
    }


def overlap_at_k(sa: np.ndarray, sb: np.ndarray, k: int = 10) -> float:
    ta, tb = np.argsort(-sa, axis=1)[:, :k], np.argsort(-sb, axis=1)[:, :k]
    return float(np.mean([len(set(a) & set(b)) / k for a, b in zip(ta, tb)]))


def mean_std(rows: list[dict], path: list[str]) -> dict:
    vals = []
    for r in rows:
        x = r
        for p in path:
            x = x[p]
        vals.append(x)
    return {"mean": round(float(np.mean(vals)), 4), "std": round(float(np.std(vals)), 4)}


def evaluate(data: dict, with_int8_fp32: bool = True) -> dict:
    dense = data["dense"]
    dl = np.array([x["label"] for x in dense])
    res: dict = {"params": data["params"], "models": {}}
    S: dict[str, np.ndarray] = {}
    for key, (label, t) in MODELS.items():
        v = vectors(key, dense)
        if v is None:
            print(f"skip {key}: no vectors")
            continue
        s = sim_matrix(v)
        S[key] = s
        r = {"label": label, "threshold": t, "dense_retrieval": retrieval(s, dl), "dense_pairs": pairs(s, dl, t)}
        print(key, r["dense_retrieval"], {k: r["dense_pairs"][k] for k in ("a_mutual_nn_at_model_t", "b_plain_threshold_at_model_t", "c_plain_threshold_best_f1")})
        if data["sparse"]:
            sp = []
            for seed, items in data["sparse"].items():
                sl = np.array([x["label"] for x in items])
                sp.append(pairs(sim_matrix(vectors(key, items)), sl, t))
            r["sparse_pairs_per_seed"] = sp
            r["sparse_pairs_mean"] = {f"{rule}.{m}": mean_std(sp, [rule, m]) for rule in ("a_mutual_nn_at_model_t", "b_plain_threshold_at_model_t", "c_plain_threshold_best_f1", "d_mutual_nn_best_f1") for m in ("precision", "recall", "f1", "reported")}
            r["sparse_pairs_mean"]["a_mutual_nn_at_model_t.function_coverage"] = mean_std(sp, ["a_mutual_nn_at_model_t", "function_coverage"])
            print("  sparse", {k: r["sparse_pairs_mean"][k]["mean"] for k in r["sparse_pairs_mean"] if k.endswith(("precision", "recall", "f1"))})
        res["models"][key] = r
    if "cre_int8" in S and "cre_fp32" in S:
        a, b = S["cre_int8"], S["cre_fp32"]
        va, vb = vectors("cre_int8", dense), vectors("cre_fp32", dense)
        iu = np.triu_indices(len(dl), 1)
        res["int8_vs_fp32"] = {
            "overlap_at_10": round(overlap_at_k(a, b, 10), 4),
            "overlap_at_1": round(overlap_at_k(a, b, 1), 4),
            "self_cosine_mean": round(float((va * vb).sum(1).mean()), 4), "self_cosine_min": round(float((va * vb).sum(1).min()), 4),
            "pairwise_cosine_pearson": round(float(np.corrcoef(a[iu], b[iu])[0, 1]), 4),
            "pairwise_cosine_mean_abs_diff": round(float(np.abs(a[iu] - b[iu]).mean()), 4),
            "map_at_r_delta_int8_minus_fp32": round(res["models"]["cre_int8"]["dense_retrieval"]["map_at_r"] - res["models"]["cre_fp32"]["dense_retrieval"]["map_at_r"], 4),
            "p_at_1_delta_int8_minus_fp32": round(res["models"]["cre_int8"]["dense_retrieval"]["p_at_1"] - res["models"]["cre_fp32"]["dense_retrieval"]["p_at_1"], 4),
        }
        print("int8 vs fp32", res["int8_vs_fp32"])
    return res


def main() -> None:
    res = {}
    for name, f in (("codenet", "samples.json"), ("gptclonebench_t4", "samples_gcb.json")):
        if (OUT / f).exists():
            print("==", name)
            res[name] = evaluate(json.loads((OUT / f).read_text()))
    (OUT / "quality.json").write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
