"""Compile remote repositories: `owner/repo`, GitHub URLs or any git URL are shallow-cloned into a cache."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

_SHORTHAND = re.compile(r"^(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?(?:@(?P<ref>[^@/]+))?$")
_GITHUB_WEB = re.compile(r"^(?:https?://)?(?:www\.)?github\.com/(?P<owner>[^/]+)/(?P<repo>[^/#?]+?)(?:\.git)?(?:/(?:tree|blob|commit)/(?P<ref>[^/]+)(?:/.*)?)?/?$")


def default_cache() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(Path.home(), ".cache")
    return Path(base) / "vibepiler"


def parse_remote(spec: str) -> tuple[str, str | None] | None:
    """Return (git url, ref) when `spec` names a remote repository, else None (it is a local path)."""
    spec = spec.strip()
    if not spec or Path(spec).exists():
        return None
    if spec.startswith("git@") or "://" in spec:
        m = _GITHUB_WEB.match(spec)
        if m:
            return f"https://github.com/{m['owner']}/{m['repo']}" + (".git" if spec.endswith(".git") else ""), m["ref"]
        return spec, None
    m = _GITHUB_WEB.match(spec)
    if m:
        return f"https://github.com/{m['owner']}/{m['repo']}", m["ref"]
    m = _SHORTHAND.match(spec)
    if m and not spec.startswith((".", "/", "~")):
        return f"https://github.com/{m['owner']}/{m['repo']}", m["ref"]
    return None


def repo_name(url: str) -> str:
    tail = url.rstrip("/").split("/")[-1].split(":")[-1]
    return tail[:-4] if tail.endswith(".git") else tail


def _slug(url: str) -> str:
    clean = re.sub(r"^(https?://|git@|file://)", "", url).replace(":", "/")
    clean = clean[:-4] if clean.endswith(".git") else clean
    return re.sub(r"[^A-Za-z0-9_.-]+", "__", clean.strip("/"))


def fetch_remote(url: str, ref: str | None, cache: Path | None = None, refresh: bool = False, log=print) -> Path:  # noqa: ANN001
    """Shallow-clone `url` (at `ref`, if given) into the cache and return the checkout path."""
    cache = cache or default_cache()
    dest = cache / "repos" / (_slug(url) + (f"@{ref}" if ref else ""))
    if refresh and dest.exists():
        shutil.rmtree(dest)
    if (dest / ".git").exists():
        subprocess.run(["git", "-C", str(dest), "pull", "-q", "--ff-only"], check=False, capture_output=True)
        log(f"using cached clone {dest}")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "clone", "-q", "--depth", "1"]
    if ref:
        cmd += ["--branch", ref]
    cmd += [url, str(dest)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"git clone failed for {url}: {res.stderr.strip()}")
    log(f"cloned {url}" + (f"@{ref}" if ref else "") + f" into {dest}")
    return dest
