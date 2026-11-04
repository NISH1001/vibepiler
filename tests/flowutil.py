"""Test helper: the function body, adapter and source of a snippet, for the flow and dataflow tests."""

from vibepiler.compiler import Compiler


def body(code, name="f", language="python"):
    prog = Compiler(code, language=language).run(opt_level=0, emit=()).program
    n = next(x for x in prog.graph.nodes() if x.name == name and x.kind.value in ("function", "method"))
    sd, sf = prog.symbol_def(n), prog.files[0]
    spec = prog.spec_for(n)
    return spec, sf, sd.node, spec.body_statements(sd.node, sf)
