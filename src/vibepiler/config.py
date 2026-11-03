"""Project configuration: CLI flags merged with `[tool.vibepiler]` from pyproject.toml or vibepiler.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Thresholds:
    complexity: int = 10  # cyclomatic complexity per function
    function_loc: int = 60  # lines per function
    nesting: int = 4  # max nesting depth
    params: int = 6  # parameters per function
    duplicate_min_nodes: int = 25  # min AST size for clone detection
    near_duplicate_similarity: float = 0.85
    god_module_fan: int = 10  # fan-in AND fan-out above this => god module
    hotspot_top_n: int = 10
    single_use_helper_max_loc: int = 8
    semantic_similarity: float = 0.88  # -O3: cosine similarity for semantic duplicates
    responsibility_similarity: float = 0.70  # -O3: symbols closer than this belong to the same concern


@dataclass
class Config:
    ignore: list[str] = field(default_factory=list)
    languages: list[str] | None = None
    opt_level: int = 2
    treat_exports_as_roots: bool | None = None  # None = per-language default
    entry_patterns: list[str] = field(default_factory=list)  # extra glob/regex of entry qualnames
    thresholds: Thresholds = field(default_factory=Thresholds)
    use_gitignore: bool = True
    embedding_model: str = "BAAI/bge-small-en-v1.5"  # -O3 backend model (fastembed)

    @classmethod
    def load(cls, root: Path, **overrides: Any) -> Config:
        data: dict[str, Any] = {}
        for candidate in (root / "vibepiler.toml", root / "pyproject.toml"):
            if candidate.is_file():
                try:
                    raw = tomllib.loads(candidate.read_text(encoding="utf-8"))
                except (tomllib.TOMLDecodeError, OSError):
                    continue
                if candidate.name == "vibepiler.toml":
                    data = raw
                else:
                    data = raw.get("tool", {}).get("vibepiler", {})
                if data:
                    break
        cfg = cls()
        thresholds = data.pop("thresholds", {})
        for k, v in data.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        for k, v in thresholds.items():
            if hasattr(cfg.thresholds, k):
                setattr(cfg.thresholds, k, v)
        for k, v in overrides.items():
            if v is not None and hasattr(cfg, k):
                setattr(cfg, k, v)
        return cfg
