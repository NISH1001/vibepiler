"""The compilation unit: the program graph plus the per-file facts behind it."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vibepiler.config import Config
from vibepiler.frontend.facts import FileFacts
from vibepiler.frontend.languages import get_language
from vibepiler.frontend.languages.base import LanguageSpec
from vibepiler.frontend.loader import SourceFile
from vibepiler.ir.graph import ProgramGraph
from vibepiler.ir.model import Node
from vibepiler.passes.diagnostic import Diagnostic


@dataclass
class Program:
    root: Path
    graph: ProgramGraph
    files: list[SourceFile]
    facts: dict[str, FileFacts]  # module node id -> facts
    config: Config = field(default_factory=Config)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def facts_for(self, module_id: str) -> FileFacts:
        return self.facts[module_id]

    def spec_for(self, node: Node | str) -> LanguageSpec:
        n = self.graph.node(node) if isinstance(node, str) else node
        lang = n.language or self.graph.node(n.module).language  # type: ignore[arg-type]
        return get_language(lang)  # type: ignore[arg-type]

    def symbol_def(self, node: Node | str):
        """The SymbolDef (with its tree-sitter node) behind a symbol node, if any."""
        n = self.graph.node(node) if isinstance(node, str) else node
        if n.module is None or n.module not in self.facts:
            return None
        return self.facts[n.module].defs_by_qualname().get(n.qualname)
