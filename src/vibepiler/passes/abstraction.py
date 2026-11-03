"""abstraction: layers of indirection that do not pay for themselves."""

from __future__ import annotations

from collections import Counter

from vibepiler.ir.model import EdgeKind, Node, NodeKind
from vibepiler.ir.program import Program
from vibepiler.passes.base import Pass
from vibepiler.passes.diagnostic import Diagnostic, Severity
from vibepiler.passes.entry_points import is_dunder

ABSTRACT_BASES = {"ABC", "ABCMeta", "Protocol", "Interface", "abc.ABC", "typing.Protocol", "Generic"}
DATA_DECORATORS = {"dataclass", "dataclasses.dataclass", "attr.s", "attrs.define", "define", "frozen", "pydantic.dataclasses.dataclass", "total_ordering"}
DATA_BASES = {"BaseModel", "pydantic.BaseModel", "Enum", "IntEnum", "StrEnum", "enum.Enum", "NamedTuple", "TypedDict", "Exception", "BaseException", "Error", "TypedDict", "BaseSettings", "Model", "models.Model", "Base", "DeclarativeBase", "Struct", "Schema"}


def _bases(n: Node) -> list[str]:
    return list(n.attrs.get("bases", []))


COMPOSED_STEPS = 3  # a caller with this many single-use helpers is decomposed on purpose


class AbstractionPass(Pass):
    name = "abstraction"
    description = "Interfaces with one implementation, classes that are really functions, single-use helpers, deep inheritance, tiny modules."
    analogue = "indirection / devirtualization"
    cost = 1
    requires = ("entry-points",)

    def run(self, program: Program) -> list[Diagnostic]:
        g = program.graph
        th = program.config.thresholds
        diags: list[Diagnostic] = []

        for cls in g.nodes(NodeKind.CLASS):
            diags += self._check_class(program, cls)
        diags += self._single_use_helpers(program)
        diags += self._tiny_modules(program)
        return diags

    def _check_class(self, program: Program, cls: Node) -> list[Diagnostic]:
        g = program.graph
        subclasses = [g.node(e.src) for e in g.in_edges(cls.id, EdgeKind.INHERITS)]
        methods = [c for c in g.children(cls.id) if c.kind is NodeKind.METHOD]
        abstract_like = self._is_abstract_like(cls, methods)
        direct_uses = [e for e in g.in_edges(cls.id, (EdgeKind.CALLS, EdgeKind.REFERENCES))]
        return self._interfaces(program, cls, subclasses, abstract_like, direct_uses) + self._shape(cls, subclasses, methods) + self._depth(program, cls)

    def _interfaces(self, program: Program, cls: Node, subclasses: list[Node], abstract_like: bool, direct_uses: list) -> list[Diagnostic]:
        """An interface with one implementation, a base with one subclass, an interface nobody implements."""
        diags: list[Diagnostic] = []
        if abstract_like and len(subclasses) == 1:
            diags.append(
                self.diag(
                    "single-implementation",
                    Severity.WARNING,
                    f"abstract class `{cls.qualname}` has exactly one implementation (`{subclasses[0].qualname}`)",
                    node=cls,
                    suggestion=f"Delete `{cls.name}` and use `{subclasses[0].name}` directly; add the interface back when a second implementation exists.",
                    implementations=[s.qualname for s in subclasses],
                )
            )
        elif not abstract_like and len(subclasses) == 1 and not direct_uses and not self._is_data_class(cls) and not program.graph.has_external_base(cls.id):
            diags.append(
                self.diag(
                    "single-subclass",
                    Severity.INFO,
                    f"`{cls.qualname}` exists only to be subclassed by `{subclasses[0].qualname}`",
                    node=cls,
                    suggestion="Merge the two classes unless the split is intentional.",
                    implementations=[s.qualname for s in subclasses],
                )
            )
        if abstract_like and not subclasses and not direct_uses and not cls.attrs.get("is_root"):
            diags.append(self.diag("unimplemented-interface", Severity.WARNING, f"abstract class `{cls.qualname}` has no implementation", node=cls, suggestion="Delete it."))
        return diags

    def _shape(self, cls: Node, subclasses: list[Node], methods: list[Node]) -> list[Diagnostic]:
        """A class that is really a function, or a namespace of static methods."""
        diags: list[Diagnostic] = []
        # class that is really a function
        if (
            not _bases(cls)
            and not subclasses
            and not self._is_data_class(cls)
            and not cls.attrs.get("decorators")
            and methods
        ):
            real = [m for m in methods if not is_dunder(m.name)]
            static = [m for m in real if any(d in ("staticmethod", "classmethod") for d in m.attrs.get("decorators", []))]
            has_state = any(m.name in ("__init__", "constructor") for m in methods)
            if len(real) == 1 and not any("property" in d for d in real[0].attrs.get("decorators", [])):
                diags.append(
                    self.diag(
                        "class-as-function",
                        Severity.INFO,
                        f"class `{cls.qualname}` has a single method `{real[0].name}`" + (" plus a constructor" if has_state else ""),
                        node=cls,
                        suggestion=f"Replace it with a function `{real[0].name}(...)`" + (" that takes what the constructor stored." if has_state else "."),
                        method=real[0].qualname,
                    )
                )
            elif real and len(static) == len(real) and not has_state:
                diags.append(
                    self.diag(
                        "namespace-class",
                        Severity.INFO,
                        f"class `{cls.qualname}` only holds {len(real)} static methods",
                        node=cls,
                        suggestion="Use module-level functions instead; the module is already a namespace.",
                    )
                )
        return diags

    def _depth(self, program: Program, cls: Node) -> list[Diagnostic]:
        diags: list[Diagnostic] = []
        depth = self._inheritance_depth(program, cls)
        cls.attrs["inheritance_depth"] = depth
        if depth >= 3:
            diags.append(self.diag("deep-inheritance", Severity.INFO, f"`{cls.qualname}` sits {depth} levels deep in an inheritance chain", node=cls, depth=depth))
        return diags

    def _single_use_helpers(self, program: Program) -> list[Diagnostic]:
        """A helper called exactly once is indirection, unless its caller is composed of such steps on purpose."""
        g = program.graph
        th = program.config.thresholds
        diags: list[Diagnostic] = []
        cands: list[tuple[Node, Node]] = []
        for fn in g.nodes(NodeKind.FUNCTION):
            if fn.attrs.get("is_root") or fn.attrs.get("decorators") or fn.attrs.get("recursive") or not fn.attrs.get("reachable"):
                continue
            if g.parent(fn.id) is not None and g.parent(fn.id).kind is not NodeKind.MODULE:  # type: ignore[union-attr]
                continue
            calls = g.in_edges(fn.id, EdgeKind.CALLS)
            refs = g.in_edges(fn.id, EdgeKind.REFERENCES)
            if len(calls) != 1 or refs or calls[0].count != 1:
                continue
            caller = g.node(calls[0].src)
            if caller.module != fn.module or fn.loc > th.single_use_helper_max_loc:
                continue
            cands.append((fn, caller))
        per_caller = Counter(c.id for _, c in cands)
        for fn, caller in cands:
            if per_caller[caller.id] >= COMPOSED_STEPS:
                continue  # a function made of named steps: decomposition, not indirection
            diags.append(
                self.diag(
                    "single-use-helper",
                    Severity.INFO,
                    f"`{fn.qualname}` ({fn.loc} lines) is called exactly once, from `{caller.qualname}`",
                    node=fn,
                    suggestion="Inline it unless the name documents something the code does not.",
                    caller=caller.qualname,
                    loc=fn.loc,
                )
            )
        return diags

    def _tiny_modules(self, program: Program) -> list[Diagnostic]:
        g = program.graph
        diags: list[Diagnostic] = []
        for m in g.nodes(NodeKind.MODULE):
            syms = [c for c in g.children(m.id)]
            importers = {e.src for e in g.in_edges(m.id, EdgeKind.IMPORTS) if not e.attrs.get("implicit")}
            if m.attrs.get("is_root") or m.attrs.get("is_entry_module") or m.attrs.get("is_test"):
                continue
            if len(syms) <= 1 and m.attrs.get("loc", 0) < 15 and len(importers) == 1:
                importer = g.node(next(iter(importers)))
                diags.append(
                    self.diag(
                        "tiny-module",
                        Severity.INFO,
                        f"module `{m.name}` has {len(syms)} symbol(s) in {m.attrs.get('loc', 0)} lines and one importer (`{importer.name}`)",
                        node=m,
                        suggestion=f"Move its contents into `{importer.name}`.",
                        importer=importer.file,
                    )
                )

        program.metrics["abstraction"] = {"count": len(diags)}
        return diags

    @staticmethod
    def _is_abstract_like(cls: Node, methods: list[Node]) -> bool:
        if any(b.split(".")[-1] in ABSTRACT_BASES or b in ABSTRACT_BASES for b in _bases(cls)):
            return True
        if any("abstractmethod" in d for m in methods for d in m.attrs.get("decorators", [])):
            return True
        if cls.attrs.get("is_interface") or cls.attrs.get("is_abstract"):
            return True
        name = cls.name
        return name.startswith("Abstract") or name.endswith(("Interface", "Base", "Protocol", "ABC"))

    @staticmethod
    def _is_data_class(cls: Node) -> bool:
        decs = cls.attrs.get("decorators", [])
        if any(d in DATA_DECORATORS or d.split(".")[-1] in DATA_DECORATORS for d in decs):
            return True
        if any(b in DATA_BASES or b.split(".")[-1] in DATA_BASES or b.endswith(("Error", "Exception", "Model", "Enum", "Schema")) for b in _bases(cls)):
            return True
        return False


    @staticmethod
    def _inheritance_depth(program: Program, cls: Node, depth: int = 0) -> int:
        g = program.graph
        if depth > 10:
            return depth
        best = depth
        for e in g.out_edges(cls.id, EdgeKind.INHERITS):
            t = g.node(e.dst)
            if t.kind is NodeKind.CLASS:
                best = max(best, AbstractionPass._inheritance_depth(program, t, depth + 1))
        return best
