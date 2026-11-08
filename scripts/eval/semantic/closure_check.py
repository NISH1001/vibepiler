"""Bug check: symbol_vectors means to skip nested closures, but its filter only looks for a parent of kind
'function', so a closure inside a *method* is embedded. Counts them per repo, and among the reported pairs."""
import json, sys
from pathlib import Path
from vibepiler import Compiler
from vibepiler.ir.model import CALLABLE_KINDS
for r in sys.argv[2:]:
    p = Compiler(Path(sys.argv[1]) / r, use_cache=False).run(opt_level=0, emit=()).program
    g = p.graph
    nodes = [n for n in g.nodes(CALLABLE_KINDS) if n.loc >= 2 and not g.node(n.module).attrs.get("is_test")]
    in_method = [n for n in nodes if g.parent(n.id) is not None and g.parent(n.id).kind.value == "method"]
    in_func = [n for n in nodes if g.parent(n.id) is not None and g.parent(n.id).kind.value == "function"]
    ir = Path(sys.argv[1]).parent / "out" / "cli" / f"{r}_cre_int8" / "ir.json"
    ids = {n.id for n in in_method}
    pairs = [d for d in json.loads(ir.read_text())["diagnostics"] if d["code"] == "semantic-duplicate"] if ir.exists() else []
    hit = sum(any(i in ids for i in d["data"]["ids"]) for d in pairs)
    print(json.dumps({"repo": r, "closures_in_methods_embedded": len(in_method), "closures_in_functions_skipped": len(in_func), "reported_pairs": len(pairs), "pairs_with_a_closure_in_a_method": hit}))
