"""JS/TS linker gaps from the evaluation: tsconfig `paths`, workspace and self imports, what a package exposes
(`main`, `exports`, re-exports), and the files `bin`, `scripts`, CI and tool configs run."""

import json

from vibepiler import compile as vcompile

FINDINGS = ("dead-symbol", "unreachable-symbol", "unreachable-module", "unused-export", "test-only")


def run(tmp_path, files: dict[str, str | dict]) -> tuple[set[str], list[str]]:
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content) if isinstance(content, dict) else content)
    res = vcompile(str(tmp_path))
    dead = {d.node_id.split(":", 1)[1] for d in res.diagnostics if d.code in FINDINGS and d.node_id}
    deps = [d.message for d in res.diagnostics if d.code in ("undeclared-dependency", "unused-dependency")]
    return dead, deps


def test_tsconfig_paths_with_extends_and_comments(tmp_path):
    dead, _ = run(tmp_path, {
        "tsconfig.base.json": '{\n  // shared\n  "compilerOptions": {"baseUrl": ".", "paths": {"@acme/utils/*": ["./packages/utils/*"],},},\n}\n',
        "tsconfig.json": '{"extends": "./tsconfig.base.json"}',
        "packages/utils/shape.ts": "export const polygon = () => 1;\nexport const unused = () => 2;\n",
        "src/main.ts": "import { polygon } from '@acme/utils/shape';\nconsole.log(polygon());\n",
    })
    assert "packages/utils/shape.ts::polygon" not in dead
    assert "packages/utils/shape.ts::unused" in dead


def test_a_workspace_package_resolves_to_its_source_and_stays_a_dependency(tmp_path):
    dead, deps = run(tmp_path, {
        "package.json": {"name": "app", "private": True, "dependencies": {"@acme/shared": "workspace:*"}},
        "packages/shared/package.json": {"name": "@acme/shared", "private": True, "main": "./dist/index.js"},
        "packages/shared/src/index.ts": "export * from './errors';\n",
        "packages/shared/src/errors.ts": "export class AcmeError extends Error {}\nexport const isAcmeError = (e: unknown) => e instanceof AcmeError;\nexport type Unused = string;\n",
        "src/main.ts": "import { isAcmeError } from '@acme/shared';\nconsole.log(isAcmeError(1));\n",
    })
    assert {"packages/shared/src/errors.ts::isAcmeError", "packages/shared/src/errors.ts::AcmeError"}.isdisjoint(dead)
    assert "packages/shared/src/errors.ts::Unused" in dead  # a private workspace package's exports are not API
    assert deps == []  # `@acme/shared` is imported, so its declaration is used


def test_a_library_entry_passes_on_what_it_re_exports(tmp_path):
    dead, deps = run(tmp_path, {
        "package.json": {"name": "lib", "main": "./index.js", "exports": {".": "./index.js"}},
        "src/index.ts": "export * from './vanilla';\nexport { create } from './react';\n",
        "src/vanilla.ts": "export const createStore = () => impl();\nconst impl = () => 1;\n",
        "src/react.ts": "export const create = () => 2;\nexport const internal = () => 3;\n",
        "tests/basic.test.ts": "import { create } from 'lib';\ntest('x', () => create());\n",
    })
    assert {"src/vanilla.ts::createStore", "src/vanilla.ts::impl", "src/react.ts::create"}.isdisjoint(dead)
    assert "src/react.ts::internal" in dead  # not re-exported by the entry
    assert not any("lib" in d for d in deps)  # importing yourself is not an undeclared dependency


def test_exports_star_makes_every_module_a_public_subpath(tmp_path):
    dead, _ = run(tmp_path, {
        "package.json": {"name": "lib", "private": True, "main": "./index.js", "exports": {".": "./index.js", "./*": "./*.js"}},
        "src/index.ts": "export const create = () => 1;\n",
        "src/react/shallow.ts": "import { compare } from '../vanilla/shallow';\nexport const useShallow = () => compare();\n",
        "src/vanilla/shallow.ts": "export const compare = () => 2;\n",
    })
    assert {"src/react/shallow.ts", "src/react/shallow.ts::useShallow", "src/vanilla/shallow.ts::compare"}.isdisjoint(dead)


def test_a_barrel_inside_an_app_is_not_api(tmp_path):
    dead, _ = run(tmp_path, {
        "package.json": {"name": "app"},
        "src/main.ts": "import { shout } from './utils';\nconsole.log(shout('x'));\n",
        "src/utils/index.ts": "export * from './strings';\n",
        "src/utils/strings.ts": "export const shout = (s: string) => s;\nexport const whisper = (s: string) => s;\n",
    })
    assert "src/utils/strings.ts::whisper" in dead


def test_bin_scripts_ci_and_config_paths_are_entry_points(tmp_path):
    dead, _ = run(tmp_path, {
        "package.json": {"name": "cli", "bin": {"cli": "bin/cli.js"}, "scripts": {"seed": "tsx --env-file=.env src/db/seed.ts", "v": "node -e 1 && echo 4.5"}},
        "bin/cli.js": "const { run } = require('../lib/run');\nrun();\n",
        "lib/run.js": "exports.run = () => 1;\n",
        "src/db/seed.ts": "const seed = () => 1;\nseed();\n",
        ".github/workflows/ci.yml": "jobs:\n  a:\n    steps:\n      - run: node scripts/check.js\n",
        "scripts/check.js": "const helper = require('./helper');\nhelper();\n",
        "scripts/helper.js": "module.exports = () => 1;\n",
        "vitest.config.ts": "export default { test: { setupFiles: ['./setupTests.ts'] } };\n",
        "setupTests.ts": "export const setup = () => 1;\n",
    })
    assert {"bin/cli.js", "src/db/seed.ts", "scripts/check.js", "scripts/helper.js", "setupTests.ts"}.isdisjoint(dead)
    assert "lib/run.js::run" not in dead
