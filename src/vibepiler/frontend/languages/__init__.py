"""Language adapter registry."""

from __future__ import annotations

from vibepiler.frontend.languages.base import LanguageSpec

_REGISTRY: dict[str, LanguageSpec] = {}


def register(spec: LanguageSpec) -> LanguageSpec:
    _REGISTRY[spec.name] = spec
    return spec


def get_language(name: str) -> LanguageSpec:
    _load()
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"no language adapter for {name!r}; known: {sorted(_REGISTRY)}") from exc


def available() -> list[str]:
    _load()
    return sorted(_REGISTRY)


def _load() -> None:
    if _REGISTRY:
        return
    from vibepiler.frontend.languages import javascript, python  # noqa: F401

    register(python.PythonSpec())
    register(javascript.JavaScriptSpec())
    register(javascript.TypeScriptSpec())
