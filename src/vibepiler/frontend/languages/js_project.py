"""What a JS/TS project's own config says about its modules: tsconfig `paths`, the packages of a workspace, and the
files package.json and CI run. Read once per project root; nothing here needs node_modules or a build.

- `paths_candidates`: `@excalidraw/utils/x` through `"@excalidraw/utils/*": ["./packages/utils/*"]` (with `extends`).
- `package_candidates`: `@scuttlepay/shared` (a workspace package) or `zustand/middleware` (the project importing
  itself) to the package's source. Its `main`/`exports` point at the build (`dist/index.js`); the source is tried
  under the same path, without the build folder, and under `src/`.
- `project_entries`: the modules a package exposes (`main`, `module`, `exports`, its `./*` subpaths) and the files its
  `bin`, its `scripts` and the CI workflows run. A private package below the root is internal to the workspace: it
  exposes nothing, since everything that uses it is here and links to it.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

SKIP_DIRS = {"node_modules", ".git", "dist", "build", "out", ".next", ".turbo", "coverage", ".venv", "venv", "__pycache__"}
BUILD_DIRS = {"dist", "build", "lib", "esm", "cjs", "es", "umd", "out", "prod", "dev", "types", "module", "commonjs"}
CONFIG_NAMES = ("tsconfig.json", "jsconfig.json")
_SOURCE_FILE = re.compile(r"""(?<![\w@/.-])(\.{0,2}/?[\w@.-]+(?:/[\w@.-]+)*\.(?:[cm]?[jt]sx?))(?![\w/])""")
_DECLARATION = re.compile(r"\.d\.[cm]?ts$")


@dataclass
class JsPackage:
    dir: str  # relative to the root, "" for the root itself
    name: str | None
    entries: list[list[str]] = field(default_factory=list)  # per path it exposes: candidate modules (no extension), build path first
    subpaths: list[list[str]] = field(default_factory=list)  # per `"./*"` pattern: candidate folders whose every module is public
    internal: bool = False  # private and below the root: a workspace package only its siblings use
    runs: list[list[str]] = field(default_factory=list)  # per file `bin` or `scripts` run: candidate modules (no extension)


# --------------------------------------------------------------------------- reading
_STRING_OR_COMMENT = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?(?:\*/|$)', re.S)


def _read(path: Path) -> dict:
    """A package.json or a tsconfig, which is JSON with comments and trailing commas: drop the comments (never inside a
    string), then the commas. {} when it is missing or broken."""
    try:
        text = _STRING_OR_COMMENT.sub(lambda m: m.group(0) if m.group(0).startswith('"') else "", path.read_text(encoding="utf-8"))
        data = json.loads(re.sub(r",(\s*[}\]])", r"\1", text))
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


@cache
def _package_dirs(root: Path) -> tuple[str, ...]:
    """Every folder holding a package.json, outside dependencies and build output."""
    found = []
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith("."))
        if "package.json" in files:
            rel = os.path.relpath(d, root)
            found.append("" if rel == "." else rel.replace(os.sep, "/"))
    return tuple(found)


# --------------------------------------------------------------------------- tsconfig paths
@cache
def _config_paths(root: Path, config: str, depth: int = 0) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """`paths` of one tsconfig, its targets made relative to the root, merged over what it `extends`."""
    data = _read(root / config)
    here = posixpath.dirname(config)
    merged: dict[str, tuple[str, ...]] = {}
    if depth < 5:
        for parent in _extended(here, data.get("extends")):
            merged.update(dict(_config_paths(root, parent, depth + 1)))
    merged.update(_own_paths(here, data.get("compilerOptions") or {}))
    return tuple(merged.items())


def _extended(here: str, extends) -> list[str]:  # noqa: ANN001 - a string or a list of them
    """The project configs a tsconfig `extends` (`./tsconfig.base`); a package's preset (`@tsconfig/node20`) is not ours."""
    names = [extends] if isinstance(extends, str) else extends if isinstance(extends, list) else []
    out = [posixpath.normpath(posixpath.join(here, e)) for e in names if isinstance(e, str) and e.startswith(".")]
    return [p if p.endswith(".json") else f"{p}.json" for p in out]


def _own_paths(here: str, opts: dict) -> dict[str, tuple[str, ...]]:
    paths = opts.get("paths")
    if not isinstance(paths, dict):
        return {}
    base = posixpath.normpath(posixpath.join(here, opts.get("baseUrl") or "."))
    return {pattern: tuple(posixpath.normpath(posixpath.join(base, t)) for t in targets if isinstance(t, str)) for pattern, targets in paths.items() if isinstance(targets, list)}


@cache
def _nearest_config(root: Path, rel_dir: str) -> str | None:
    d = rel_dir
    while True:
        for name in CONFIG_NAMES:
            if (root / d / name).is_file():
                return posixpath.join(d, name) if d else name
        if d in ("", "."):
            return None
        d = posixpath.dirname(d)


def paths_candidates(root: Path, rel_dir: str, spec: str) -> list[str]:
    """Where the nearest tsconfig's `paths` send `spec` (relative to the root, extension kept for the caller to strip)."""
    config = _nearest_config(root, rel_dir)
    if config is None:
        return []
    out: list[str] = []
    for pattern, targets in _config_paths(root, config):
        if (middle := _match(pattern, spec)) is not None:
            out += [t.replace("*", middle, 1) for t in targets]
    return out


def _match(pattern: str, spec: str) -> str | None:
    """What `*` stands for when `spec` matches a `paths` pattern ("" for an exact pattern); None when it does not."""
    if "*" not in pattern:
        return "" if spec == pattern else None
    prefix, suffix = pattern.split("*", 1)
    if spec.startswith(prefix) and spec.endswith(suffix) and len(spec) >= len(prefix) + len(suffix):
        return spec[len(prefix) : len(spec) - len(suffix)]
    return None


# --------------------------------------------------------------------------- packages
def _to_source(pkg_dir: str, target: str) -> list[str]:
    """A path package.json gives (`./dist/prod/index.js`) and the source files it is built from, without extension."""
    p = _DECLARATION.sub("", target[2:] if target.startswith("./") else target)
    p = posixpath.splitext(p)[0] if posixpath.splitext(p)[1] in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts") else p
    parts = [x for x in p.split("/") if x not in ("", ".")]
    stripped = list(parts)
    while len(stripped) > 1 and stripped[0] in BUILD_DIRS:
        stripped.pop(0)
    return list(dict.fromkeys(posixpath.join(pkg_dir, c) for c in ("/".join(parts), "/".join(stripped), f"src/{'/'.join(stripped)}")))


def _leaves(value) -> list[str]:  # noqa: ANN001 - an `exports` condition tree
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _leaves(v)]
    if isinstance(value, list):
        return [s for v in value for s in _leaves(v)]
    return []


def _script_files(root: Path, pkg_dir: str, commands: list[str]) -> list[str]:
    """Source files a shell command runs (`node ./scripts/build.js`, `tsx src/seed.ts`), relative to the root: only
    paths that are files of the project, so a version number or a `.d.ts` name in the text is not one."""
    out = []
    for cmd in commands:
        for m in _SOURCE_FILE.finditer(cmd):
            path = posixpath.normpath(posixpath.join(pkg_dir, m.group(1)))
            if not path.startswith("..") and not _DECLARATION.search(path) and path not in out and (root / path).is_file():
                out.append(path)
    return out


@cache
def packages(root: Path) -> tuple[JsPackage, ...]:
    return tuple(pkg for d in _package_dirs(root) if (pkg := _read_package(root, d)) is not None)


def _read_package(root: Path, d: str) -> JsPackage | None:
    """One package.json: its name, what it exposes (mapped back to source) and the files it runs."""
    data = _read(root / d / "package.json")
    if not data:
        return None
    name = data.get("name")
    pkg = JsPackage(dir=d, name=name if isinstance(name, str) else None, internal=bool(d) and data.get("private") is True)
    main_paths = [v for k in ("source", "module", "main", "types", "typings") if isinstance(v := data.get(k), str)]
    exported, patterns = _split_exports(data.get("exports"))
    pkg.entries = [_to_source(d, p) for p in dict.fromkeys(main_paths + exported) if ".." not in p]
    for leaf in patterns:
        if (folders := _subpath_folders(d, leaf)) and folders not in pkg.subpaths:
            pkg.subpaths.append(folders)
    pkg.runs = [_to_source(d, b) for b in _strings(data.get("bin")) if ".." not in b]  # a bin may point at the build, like `main`
    pkg.runs += [[posixpath.splitext(f)[0]] for f in _script_files(root, d, _strings(data.get("scripts")))]
    return pkg


def _strings(value) -> list[str]:  # noqa: ANN001 - `bin`/`scripts`: a string or a map of them
    if isinstance(value, str):
        return [value]
    return [v for v in value.values() if isinstance(v, str)] if isinstance(value, dict) else []


def _split_exports(exports) -> tuple[list[str], list[str]]:  # noqa: ANN001 - the `exports` field, any shape
    """(paths it exposes, `*` patterns of its subpath keys like `"./*": "./dist/*.js"`)."""
    if isinstance(exports, str):
        return [exports], []
    if not isinstance(exports, dict):
        return [], []
    keyed = exports.items() if any(k.startswith(".") for k in exports) else [(".", exports)]  # `{import, require}` alone means "."
    paths = [leaf for key, value in keyed if "*" not in key and key != "./package.json" for leaf in _leaves(value)]
    patterns = [leaf for key, value in keyed if "*" in key for leaf in _leaves(value) if "*" in leaf and ".." not in leaf]
    return paths, patterns


def _subpath_folders(d: str, leaf: str) -> list[str]:
    """Where the modules of a `"./*"` subpath pattern live, build folder mapped to source. The package folder itself
    would make every module public, so only a folder below it counts."""
    folder = posixpath.dirname(leaf.split("*", 1)[0].removeprefix("./"))
    candidates = _to_source(d, posixpath.join(folder, "x.js"))
    return [f for f in dict.fromkeys(posixpath.dirname(c) for c in candidates) if f != d]


@cache
def _by_name(root: Path) -> dict[str, JsPackage]:
    return {p.name: p for p in packages(root) if p.name}


def package_candidates(root: Path, spec: str) -> list[str]:
    """`@scope/pkg` or `@scope/pkg/sub` when a package of this project has that name: where its source is."""
    names = _by_name(root)
    parts = spec.split("/")
    n = 2 if spec.startswith("@") else 1
    pkg = names.get("/".join(parts[:n]))
    if pkg is None:
        return []
    sub = "/".join(parts[n:])
    if not sub:
        return [c for cands in pkg.entries for c in cands] + [posixpath.join(pkg.dir, "src/index"), posixpath.join(pkg.dir, "index")]
    return [posixpath.join(pkg.dir, sub), posixpath.join(pkg.dir, "src", sub)] + [posixpath.join(f, sub) for folders in pkg.subpaths for f in folders]


def sibling_package(root: Path, spec: str, rel: str) -> str | None:
    """The name of the project package `spec` imports, unless it is the package `rel` belongs to (a self-import).
    Such an import resolves to our source, yet the importing package still declares it as a dependency."""
    parts = spec.split("/")
    name = "/".join(parts[: 2 if spec.startswith("@") else 1])
    pkg = _by_name(root).get(name)
    if pkg is None:
        return None
    owner = max((p for p in packages(root) if not p.dir or rel == p.dir or rel.startswith(f"{p.dir}/")), key=lambda p: len(p.dir), default=None)
    return None if owner is pkg else name


@cache
def workflow_files(root: Path) -> tuple[str, ...]:
    """Source files the CI workflows run (`node scripts/x.js` in a `run:` step), relative to the root."""
    wf = root / ".github" / "workflows"
    if not wf.is_dir():
        return ()
    commands = []
    for f in sorted(wf.glob("*.y*ml")):
        try:
            commands.append(f.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
    return tuple(_script_files(root, "", commands))


def project_entries(root: Path) -> list[tuple[str, list[str], bool]]:
    """(entry kind, candidate module paths without extension, whether each candidate is a folder prefix).
    The first candidate that names a module of ours is the one; a prefix marks every module under it."""
    out: list[tuple[str, list[str], bool]] = []
    for pkg in packages(root):
        if not pkg.internal:  # an internal package's users are its siblings, linked to it: its exports are not API
            out += [("package_entry", cands, False) for cands in pkg.entries]
            out += [("package_entry", folders, True) for folders in pkg.subpaths]
        out += [("package_script", cands, False) for cands in pkg.runs]
    out += [("ci_script", [posixpath.splitext(f)[0]], False) for f in workflow_files(root)]
    return out
