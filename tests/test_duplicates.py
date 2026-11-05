"""Clone detection: exact copies by shape hash, near copies by an exact similarity join (no cap on repo size)."""

import random
import time

from vibepiler.ir.builder import build_program
from vibepiler.passes.duplicates import similar_pairs
from vibepiler.passes.registry import run_pass


def brute(sets, t):
    out = []
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            sim = len(sets[i] & sets[j]) / len(sets[i] | sets[j])
            if sim >= t:
                out.append((i, j))
    return sorted(out)


def test_similarity_join_finds_exactly_the_pairs_brute_force_finds():
    rng = random.Random(0)
    base = [{f"s{rng.randrange(400)}" for _ in range(rng.randrange(20, 60))} for _ in range(60)]
    sets = []
    for b in base:  # each base set and a few mutated copies of it, so some pairs sit near the threshold
        sets.append(b)
        for _ in range(3):
            m = set(b)
            for _ in range(rng.randrange(0, 12)):
                m.discard(rng.choice(sorted(m))) if rng.random() < 0.5 and len(m) > 5 else m.add(f"s{rng.randrange(400)}")
            sets.append(m)
    for t in (0.6, 0.85, 0.95):
        got = sorted((i, j) for i, j, _ in similar_pairs(sets, t))
        assert got == brute(sets, t), t


def test_similarity_join_has_no_size_cap():
    rng = random.Random(1)
    sets = [{f"f{k}:{rng.randrange(10**9)}" for _ in range(40)} for k in range(6000)]  # 6000 unrelated functions
    sets[4321] = set(sets[17]) | {"extra"}  # ...and one near copy among them
    t0 = time.perf_counter()
    pairs = similar_pairs(sets, 0.85)
    assert [(i, j) for i, j, _ in pairs] == [(17, 4321)]
    assert time.perf_counter() - t0 < 10


def test_near_copies_are_reported_with_their_ids(tmp_path):
    body = (
        "    total, seen, worst = 0, set(), None\n"
        "    for x in xs:\n"
        "        if x in seen:\n"
        "            continue\n"
        "        seen.add(x)\n"
        "        if x > limit:\n"
        "            total += x * 2\n"
        "        elif worst is None or x < worst:\n"
        "            worst = x\n"
        "        else:\n"
        "            total -= 1\n"
        "    return {'total': total, 'worst': worst, 'count': len(seen)}\n"
    )
    (tmp_path / "a.py").write_text("def score(xs, limit):\n" + body)
    (tmp_path / "b.py").write_text("def rank(xs, limit):\n" + body.replace("            continue\n", "            print(x)\n            continue\n"))
    prog = build_program(tmp_path)
    near = [d for d in run_pass("duplicates", prog) if d.code == "near-duplicate-function"]
    assert len(near) == 1 and sorted(near[0].data["members"]) == ["rank", "score"]
    assert all(prog.graph.has_node(i) for i in near[0].data["ids"])
    assert prog.metrics["duplicates"]["near_pairs"] == 1


def test_an_accept_rule_matches_either_side_of_a_pair():
    from vibepiler.passes.diagnostic import Diagnostic, Severity
    from vibepiler.passes.registry import is_accepted

    d = Diagnostic(pass_name="duplicates", code="near-duplicate-function", severity=Severity.INFO, message="m",
                   data={"qualname": "Node.to_dict", "members": ["Node.to_dict", "Diagnostic.to_dict"]})
    assert is_accepted(d, ["near-duplicate-function:Diagnostic.to_dict"])
    assert not is_accepted(d, ["near-duplicate-function:Other.*"])
