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
def _jsonc(text: str) -> dict:
    """tsconfig is JSON with comments and trailing commas."""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        else:
            out.append(c)
        i += 1
    data = json.loads(re.sub(r",(\s*[}\]])", r"\1", "".join(out)))
    return data if isinstance(data, dict) else {}


def _read(path: Path) -> dict:
    try:
        return _jsonc(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return {}


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
    inherited: dict[str, tuple[str, ...]] = {}
    extends = data.get("extends")
    for ext in [extends] if isinstance(extends, str) else extends if isinstance(extends, list) else []:
        if isinstance(ext, str) and ext.startswith(".") and depth < 5:
            parent = posixpath.normpath(posixpath.join(here, ext))
            parent = parent if parent.endswith(".json") else f"{parent}.json"
            inherited.update(dict(_config_paths(root, parent, depth + 1)))
    opts = data.get("compilerOptions") or {}
    paths = opts.get("paths")
    if isinstance(paths, dict):
        base = posixpath.normpath(posixpath.join(here, opts.get("baseUrl") or "."))
        for pattern, targets in paths.items():
            if isinstance(targets, list):
                inherited[pattern] = tuple(posixpath.normpath(posixpath.join(base, t)) for t in targets if isinstance(t, str))
    return tuple(inherited.items())


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
        star = pattern.find("*")
        if star < 0:
            if spec == pattern:
                out += targets
            continue
        prefix, suffix = pattern[:star], pattern[star + 1 :]
        if spec.startswith(prefix) and spec.endswith(suffix) and len(spec) >= len(prefix) + len(suffix):
            middle = spec[len(prefix) : len(spec) - len(suffix)]
            out += [t.replace("*", middle, 1) for t in targets]
    return out


# --------------------------------------------------------------------------- packages
def _to_source(pkg_dir: str, target: str) -> list[str]:
    """A path package.json gives (`./dist/prod/index.js`) and the source files it is built from, without extension."""
    p = _DECLARATION.sub("", target[2:] if target.startswith("./") else target)
    p = posixpath.splitext(p)[0] if posixpath.splitext(p)[1] in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts") else p
    parts = [x for x in p.split("/") if x not in ("", ".")]
    stripped = list(parts)
    while len(stripped) > 1 and stripped[0] in BUILD_DIRS:
        stripped.pop(0)
    out = []
    for cand in ("/".join(parts), "/".join(stripped), f"src/{'/'.join(stripped)}"):
        full = posixpath.join(pkg_dir, cand) if pkg_dir else cand
        if full not in out:
            out.append(full)
    return out


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
    found = []
    for d in _package_dirs(root):
        data = _read(root / d / "package.json")
        if not data:
            continue
        name = data.get("name") if isinstance(data.get("name"), str) else None
        pkg = JsPackage(dir=d, name=name, internal=bool(d) and data.get("private") is True)
        exports = data.get("exports")
        main_paths = [data.get(k) for k in ("source", "module", "main", "types", "typings") if isinstance(data.get(k), str)]
        if isinstance(exports, str):
            main_paths.append(exports)
        elif isinstance(exports, dict):
            keyed = any(k.startswith(".") for k in exports)
            for key, value in exports.items() if keyed else [(".", exports)]:
                if key == "./package.json":
                    continue
                if "*" in key:
                    for leaf in _leaves(value):
                        if "*" not in leaf or ".." in leaf:
                            continue
                        folder = posixpath.dirname(leaf.split("*", 1)[0].removeprefix("./"))
                        # the package folder itself would make every module public: only a folder below it counts
                        folders = [f for f in dict.fromkeys(posixpath.dirname(x) for x in _to_source(d, f"{folder}/x.js" if folder else "x.js")) if f != d]
                        if folders and folders not in pkg.subpaths:
                            pkg.subpaths.append(folders)
                else:
                    main_paths += _leaves(value)
        for p in dict.fromkeys(main_paths):
            if ".." not in p:
                pkg.entries.append(_to_source(d, p))
        bins = data.get("bin")
        bin_paths = [bins] if isinstance(bins, str) else [v for v in bins.values() if isinstance(v, str)] if isinstance(bins, dict) else []
        scripts = data.get("scripts")
        commands = [v for v in scripts.values() if isinstance(v, str)] if isinstance(scripts, dict) else []
        pkg.runs = [_to_source(d, b) for b in bin_paths if ".." not in b]  # a bin may point at the build, like `main`
        pkg.runs += [[posixpath.splitext(f)[0]] for f in _script_files(root, d, commands)]
        found.append(pkg)
    return tuple(found)


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
        return [c for cands in pkg.entries for c in cands] + [posixpath.join(pkg.dir, "src/index") if pkg.dir else "src/index", posixpath.join(pkg.dir, "index") if pkg.dir else "index"]
    base = pkg.dir
    return [posixpath.join(base, sub) if base else sub, posixpath.join(base, "src", sub) if base else f"src/{sub}"] + [posixpath.join(f, sub) for folders in pkg.subpaths for f in folders]


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
