"""Shared config for the JS/TS evaluation: corpus, paths, tool commands."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent  # scripts/eval/javascript
VIBEPILER_REPO = HERE.parents[2]  # this checkout
DOCS = VIBEPILER_REPO / "docs" / "eval" / "javascript"  # results.json and RESULTS.md
EVAL = Path(os.environ.get("VIBEPILER_EVAL_WORK", VIBEPILER_REPO / ".eval" / "javascript"))  # clones, tools, raw outputs (gitignored)
REPOS = EVAL / "repos"
TOOLS = EVAL / "tools"
OUT = EVAL / "out"
RAW = OUT / "raw"
BIN = TOOLS / "node_modules" / ".bin"
VIBEPILER = VIBEPILER_REPO / ".venv" / "bin" / "vibepiler"

# name -> (url, ref, kind, why)
CORPUS = {
    "zustand": ("https://github.com/pmndrs/zustand", "v5.1.0", "small library (TS)",
                "well-known small state library; package.json `exports` subpaths are a classic entry-point test"),
    "release-it": ("https://github.com/release-it/release-it", "21.1.2", "mid CLI (plain JS, ESM)",
                   "CLI by knip's own author, ships a knip.json: knip on home turf"),
    "taxonomy": ("https://github.com/shadcn-ui/taxonomy", "main", "mid Next.js 13 app (TS)",
                 "app-router + pages conventions, contentlayer, middleware: framework entry points"),
    "excalidraw": ("https://github.com/excalidraw/excalidraw", "v0.18.1", "large monorepo (TS, yarn workspaces)",
                   "~136k lines across 5 workspaces"),
    "scuttlepay": ("https://github.com/jarrodwatts/scuttlepay", "main", "vibe-coded Next.js + pnpm workspace (TS)",
                   "71 of its last 100 commits carry a Claude co-author trailer, ships CLAUDE.md, built for a Claude Code hackathon"),
    "tower-of-time": ("https://github.com/maciej-trebacz/tower-of-time-game", "main", "vibe-coded Phaser game (TS)",
                      "described as 'Vibe coded Tower Defense', ships PROMPTS.md with the prompts used, .cursorrules"),
}

EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")


def source_files(repo: str) -> list[str]:
    """Git-tracked JS/TS files, no .d.ts: the same set vibepiler reads (its meta.files matches)."""
    out = subprocess.run(["git", "ls-files"], cwd=REPOS / repo, capture_output=True, text=True, check=True).stdout
    return [f for f in out.splitlines() if f.endswith(EXTS) and not f.endswith(".d.ts")]


def manifests(repo: str) -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=REPOS / repo, capture_output=True, text=True, check=True).stdout
    return [f for f in out.splitlines() if f == "package.json" or f.endswith("/package.json")]


def loc(repo: str) -> tuple[int, int]:
    files = source_files(repo)
    n = 0
    for f in files:
        try:
            n += sum(1 for _ in open(REPOS / repo / f, encoding="utf-8", errors="replace"))
        except OSError:
            pass
    return len(files), n


def tool_commands(repo: str, outdir: Path) -> dict[str, dict]:
    """tool -> {cmd, cwd, out (stdout file or None), skip (reason or None)}."""
    root = REPOS / repo
    files = source_files(repo)
    has_tsconfig = (root / "tsconfig.json").exists()
    exts = "ts,tsx,js,jsx,mjs,cjs,mts,cts"
    cmds: dict[str, dict] = {}
    for lvl in ("O1", "O2"):
        cmds[f"vibepiler-{lvl}"] = dict(
            cmd=[str(VIBEPILER), "compile", str(root), f"-{lvl}", "-q", "-o", str(outdir / f"vibepiler-{lvl}")],
            cwd=EVAL, out=None)
    # knip (and depcheck's config "specials") load the repo's config files (next/vite/drizzle configs), i.e. run
    # repo code: both run inside a macOS sandbox with the network denied. Env stubs follow knip's known-issues advice
    # for configs that validate env vars at import time (scuttlepay).
    nonet = ["/usr/bin/sandbox-exec", "-f", str(HERE / "nonet.sb")]
    knip_env = {"SKIP_ENV_VALIDATION": "1", "DATABASE_URL": "postgres://localhost/stub"} if repo == "scuttlepay" else {}
    cmds["knip"] = dict(cmd=nonet + [str(BIN / "knip"), "--reporter", "json", "--no-progress", "--no-exit-code"],
                        cwd=root, out=outdir / "knip.json", env=knip_env)
    ts_skip = None if has_tsconfig else "no tsconfig.json (plain-JS repo)"
    cmds["ts-prune"] = dict(cmd=[str(BIN / "ts-prune"), "-p", "tsconfig.json"], cwd=root,
                            out=outdir / "ts-prune.txt", skip=ts_skip)
    cmds["ts-unused-exports"] = dict(cmd=[str(BIN / "ts-unused-exports"), "tsconfig.json", "--showLineNumber"],
                                     cwd=root, out=outdir / "ts-unused-exports.txt", skip=ts_skip)
    cmds["depcheck"] = dict(cmd=nonet + ["/bin/sh", str(HERE / "depcheck_all.sh"), str(BIN / "depcheck"),
                                 *[str(Path(m).parent) for m in manifests(repo)]],
                            cwd=root, out=outdir / "depcheck.jsonl")
    cmds["jscpd"] = dict(cmd=[str(BIN / "jscpd"), "-s", "-r", "json", "-o", str(outdir / "jscpd"),
                              "-i", "**/node_modules/**,**/*.d.ts", "-f", "typescript,tsx,javascript,jsx", "."],
                         cwd=root, out=None)
    madge = [str(BIN / "madge"), "--circular", "--json", "--extensions", exts]
    if has_tsconfig:
        madge += ["--ts-config", "tsconfig.json"]
    cmds["madge"] = dict(cmd=madge + files, cwd=root, out=outdir / "madge.json")
    cmds["eslint"] = dict(cmd=[str(BIN / "eslint"), "--no-config-lookup", "-c", str(TOOLS / "eslint.config.mjs"),
                               "-f", "json", "-o", str(outdir / "eslint.json"), *files],
                          cwd=root, out=None)
    for c in cmds.values():
        c.setdefault("skip", None)
        c.setdefault("env", {})
    return cmds


def load_json(p: Path, default=None):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return default


def git_sha(repo: str) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPOS / repo, capture_output=True, text=True).stdout.strip()


ENV = {**os.environ, "NO_COLOR": "1", "FORCE_COLOR": "0"}
