"""Dump exactly the texts vibepiler -O3 would embed for a repo (same node selection, same symbol_text, same char cap).

usage: repo_texts.py REPO_DIR OUT.json
The embedder is swapped for a recorder through vibepiler's own EMBEDDER_FACTORY hook, with the cache off.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

import vibepiler.semantic.embed as E
from vibepiler import Compiler


class Recorder:
    name = "recorder"

    def __init__(self) -> None:
        self.texts: list[str] = []

    def embed(self, texts):
        self.texts.extend(texts)
        return np.zeros((len(texts), 4), dtype=np.float32) + 1


rec = Recorder()
E.EMBEDDER_FACTORY = lambda model: rec
repo, out = Path(sys.argv[1]), Path(sys.argv[2])
res = Compiler(repo, use_cache=False).run(opt_level=2, emit=())
nodes, _, _ = E.symbol_vectors(res.program)
texts = [E.symbol_text(res.program, n, max_chars=E.chars_for(4096)) for n in nodes]
out.write_text(json.dumps({"repo": str(repo), "qualnames": [n.qualname for n in nodes], "texts": texts}))
print(repo.name, len(texts), "functions", sum(map(len, texts)), "chars")
