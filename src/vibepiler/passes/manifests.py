"""Read declared dependencies from project manifests (pyproject.toml, requirements*.txt, package.json)."""

from __future__ import annotations

import json
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# python import name -> distribution name, for the common mismatches
PY_IMPORT_TO_DIST: dict[str, str] = {
    "PIL": "pillow", "yaml": "pyyaml", "sklearn": "scikit-learn", "cv2": "opencv-python", "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv", "jwt": "pyjwt", "attr": "attrs", "dateutil": "python-dateutil", "google": "google",
    "Crypto": "pycryptodome", "OpenSSL": "pyopenssl", "psycopg2": "psycopg2-binary", "MySQLdb": "mysqlclient",
    "serial": "pyserial", "usb": "pyusb", "gi": "pygobject", "wx": "wxpython", "sqlalchemy": "sqlalchemy",
    "git": "gitpython", "magic": "python-magic", "docx": "python-docx", "pptx": "python-pptx", "fitz": "pymupdf",
    "ldap": "python-ldap", "socks": "pysocks", "zmq": "pyzmq", "kafka": "kafka-python", "telegram": "python-telegram-bot",
    "discord": "discord.py", "markdown": "markdown", "nacl": "pynacl", "lxml": "lxml", "Levenshtein": "python-levenshtein",
    "multipart": "python-multipart", "jose": "python-jose", "slugify": "python-slugify", "snappy": "python-snappy",
    "websocket": "websocket-client", "sentry_sdk": "sentry-sdk", "pkg_resources": "setuptools", "setuptools": "setuptools",
    "streamlit": "streamlit", "anthropic": "anthropic", "openai": "openai", "langchain_core": "langchain-core",
    "langchain_openai": "langchain-openai", "langchain_community": "langchain-community", "langchain_anthropic": "langchain-anthropic",
    "pydantic_settings": "pydantic-settings", "typing_extensions": "typing-extensions", "jinja2": "jinja2", "flask_cors": "flask-cors",
    "flask_sqlalchemy": "flask-sqlalchemy", "flask_login": "flask-login", "flask_migrate": "flask-migrate", "rest_framework": "djangorestframework",
    "corsheaders": "django-cors-headers", "decouple": "python-decouple", "ruamel": "ruamel.yaml", "tree_sitter_language_pack": "tree-sitter-language-pack",
    "tree_sitter": "tree-sitter", "pytest_asyncio": "pytest-asyncio", "pytest_cov": "pytest-cov", "mcp": "mcp", "chromadb": "chromadb",
    "sentence_transformers": "sentence-transformers", "huggingface_hub": "huggingface-hub", "faiss": "faiss-cpu", "tiktoken": "tiktoken",
    "playwright": "playwright", "bson": "pymongo", "pymongo": "pymongo", "redis": "redis", "boto3": "boto3", "botocore": "botocore",
}

# packages that are legitimately declared without ever being imported (tools, plugins, servers)
PY_TOOL_PACKAGES = {
    "pytest", "pytest-cov", "pytest-asyncio", "pytest-mock", "pytest-xdist", "black", "ruff", "flake8", "mypy", "isort", "pylint", "pre-commit",
    "coverage", "tox", "nox", "pip", "setuptools", "wheel", "build", "twine", "hatch", "hatchling", "poetry", "uvicorn", "gunicorn", "hypercorn",
    "daphne", "ipython", "jupyter", "jupyterlab", "notebook", "ipykernel", "sphinx", "mkdocs", "mkdocs-material", "pyright", "bandit",
    "safety", "pip-tools", "pip-audit", "watchdog", "watchfiles", "python-lsp-server", "debugpy", "pdbpp", "celery", "flower",
    "uvloop", "httptools", "psycopg2-binary", "psycopg", "mysqlclient", "pymysql", "asyncpg", "aiosqlite", "python-multipart",
    "email-validator", "cryptography", "bcrypt", "passlib", "certifi", "charset-normalizer", "idna", "urllib3", "pytest-django",
    "django-extensions", "whitenoise", "alembic", "sqlalchemy-utils", "greenlet", "ruff-lsp", "pyinstaller", "nuitka", "cython",
    "numpy" if False else "", "pywin32", "colorama", "tqdm" if False else "", "typing-extensions", "importlib-metadata", "setuptools-scm",
    "pydantic-core", "pywin32-ctypes", "mkdocstrings", "mkdocstrings-python", "pytest-timeout", "pytest-benchmark", "pytest-sugar",
    "pytest-env", "pytest-randomly", "pytest-rerunfailures", "pytest-html", "pytest-watch", "locust", "pyinstrument", "scalene",
    "memray", "py-spy", "line-profiler", "snakeviz", "types-requests", "types-pyyaml", "types-setuptools", "pyupgrade", "autoflake",
    "autopep8", "yapf", "pydocstyle", "interrogate", "vulture", "radon", "xenon", "commitizen", "bump2version", "bumpver",
    "python-semantic-release", "ipywidgets", "nbconvert", "nbformat", "papermill", "docker", "docker-compose", "ansible", "fabric",
    "invoke", "pre-commit-hooks", "detect-secrets", "gitlint", "codespell", "vibepiler",
}
PY_TOOL_PACKAGES.discard("")

JS_TOOL_PACKAGES_PREFIXES = ("@types/", "eslint", "prettier", "typescript", "vite", "vitest", "jest", "@vitejs/", "@testing-library/", "babel", "@babel/",
                             "webpack", "rollup", "esbuild", "tsx", "ts-node", "nodemon", "husky", "lint-staged", "tailwindcss", "postcss", "autoprefixer",
                             "@tailwindcss/", "turbo", "concurrently", "cross-env", "rimraf", "dotenv-cli", "@playwright/", "playwright", "cypress",
                             "storybook", "@storybook/", "@eslint/", "eslint-", "@typescript-eslint/", "tsup", "tsc-alias", "serve", "http-server",
                             "npm-run-all", "wrangler", "vercel", "netlify-cli", "drizzle-kit", "prisma", "@prisma/", "supabase", "@next/",
                             "sharp", "@swc/", "swc", "biome", "@biomejs/", "oxlint", "knip", "madge", "depcheck", "release-it", "semantic-release",
                             "@commitlint/", "commitlint", "pnpm", "yarn", "npm", "bun-types", "@cloudflare/", "miniflare", "electron-builder",
                             "@electron-forge/", "ts-jest", "@jest/", "jsdom", "happy-dom", "msw", "sass", "less", "stylus", "@sveltejs/",
                             "svelte-check", "vue-tsc", "@vue/", "nuxt", "@nuxt/", "astro", "@astrojs/", "expo", "@expo/", "metro", "react-scripts",
                             "@craco/", "craco", "patch-package", "postinstall-postinstall", "shx", "ncp", "copyfiles", "mkdirp", "wait-on", "open-cli")
JS_IMPLICIT_PACKAGES = {"react-dom", "next", "typescript", "@types/node", "@types/react", "@types/react-dom", "tslib"}

NODE_BUILTINS = {
    "assert", "async_hooks", "buffer", "child_process", "cluster", "console", "constants", "crypto", "dgram", "diagnostics_channel", "dns",
    "domain", "events", "fs", "http", "http2", "https", "inspector", "module", "net", "os", "path", "perf_hooks", "process", "punycode",
    "querystring", "readline", "repl", "stream", "string_decoder", "sys", "timers", "tls", "trace_events", "tty", "url", "util", "v8", "vm",
    "wasi", "worker_threads", "zlib", "test",
}


@dataclass
class Manifest:
    file: str
    language: str  # "python" | "javascript"
    dependencies: dict[str, str] = field(default_factory=dict)  # normalized name -> raw spec
    dev_dependencies: dict[str, str] = field(default_factory=dict)


def normalize_py(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _py_req_name(spec: str) -> str | None:
    spec = spec.strip()
    if not spec or spec.startswith(("#", "-", "git+", "http")):
        return None
    m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", spec)
    return normalize_py(m.group(1)) if m else None


def read_manifests(root: Path) -> list[Manifest]:
    out: list[Manifest] = []
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError):
            data = {}
        m = Manifest(file="pyproject.toml", language="python")
        proj = data.get("project", {})
        for spec in proj.get("dependencies", []) or []:
            n = _py_req_name(spec)
            if n:
                m.dependencies[n] = spec
        for group, specs in (proj.get("optional-dependencies", {}) or {}).items():
            for spec in specs:
                n = _py_req_name(spec)
                if n:
                    m.dev_dependencies[n] = spec
        for group, specs in (data.get("dependency-groups", {}) or {}).items():
            for spec in specs:
                if isinstance(spec, str):
                    n = _py_req_name(spec)
                    if n:
                        m.dev_dependencies[n] = spec
        poetry = data.get("tool", {}).get("poetry", {})
        for name, spec in (poetry.get("dependencies", {}) or {}).items():
            if name.lower() != "python":
                m.dependencies[normalize_py(name)] = str(spec)
        for group in (poetry.get("group", {}) or {}).values():
            for name in (group.get("dependencies", {}) or {}):
                m.dev_dependencies[normalize_py(name)] = ""
        for name in (poetry.get("dev-dependencies", {}) or {}):
            m.dev_dependencies[normalize_py(name)] = ""
        if m.dependencies or m.dev_dependencies:
            out.append(m)
    for req in sorted(root.glob("requirements*.txt")) + sorted((root / "requirements").glob("*.txt")) if (root / "requirements").is_dir() else sorted(root.glob("requirements*.txt")):
        m = Manifest(file=req.relative_to(root).as_posix(), language="python")
        dev = "dev" in req.name or "test" in req.name
        for line in req.read_text(encoding="utf-8", errors="replace").splitlines():
            n = _py_req_name(line.split("#")[0])
            if n:
                (m.dev_dependencies if dev else m.dependencies)[n] = line.strip()
        if m.dependencies or m.dev_dependencies:
            out.append(m)
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        m = Manifest(file="package.json", language="javascript")
        for name, spec in (data.get("dependencies", {}) or {}).items():
            m.dependencies[name] = str(spec)
        for key in ("devDependencies", "peerDependencies", "optionalDependencies"):
            for name, spec in (data.get(key, {}) or {}).items():
                m.dev_dependencies[name] = str(spec)
        if m.dependencies or m.dev_dependencies:
            out.append(m)
    return out


def py_dist_for_import(import_name: str) -> str:
    return normalize_py(PY_IMPORT_TO_DIST.get(import_name, import_name))


def is_py_stdlib(name: str) -> bool:
    return name in sys.stdlib_module_names or name in {"__future__", "__main__"}


def js_package_for_specifier(spec: str) -> str | None:
    if spec.startswith((".", "/", "~", "@/", "#")):
        return None
    if spec.startswith("node:"):
        return None
    parts = spec.split("/")
    name = "/".join(parts[:2]) if spec.startswith("@") else parts[0]
    if name in NODE_BUILTINS:
        return None
    return name


def is_js_tool_package(name: str) -> bool:
    return name.startswith(JS_TOOL_PACKAGES_PREFIXES) or name in JS_IMPLICIT_PACKAGES
