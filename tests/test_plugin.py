"""The Claude Code plugin and its skills: installable from the repo, portable to other harnesses, and in step with the CLI."""

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vibepiler import __version__
from vibepiler.cli import app

ROOT = Path(__file__).parent.parent
SKILLS = sorted((ROOT / "skills").glob("*/SKILL.md"))
PORTABLE = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}  # agentskills.io; others break some harnesses


def frontmatter(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path}: frontmatter must be the first thing in the file"
    head, body = text[4:].split("\n---\n", 1)
    fields = dict(re.match(r"([a-z-]+):\s*(.*)", line).groups() for line in head.splitlines() if re.match(r"[a-z-]+:", line))
    return fields, body


def test_the_repo_is_a_marketplace_offering_itself_as_the_plugin():
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert market["name"] == "vibepiler" and market["owner"]["name"]
    (entry,) = market["plugins"]
    assert entry["name"] == plugin["name"] == "vibepiler" and entry["source"] == "./"
    assert plugin["version"] == __version__  # the plugin ships with the release it drives


def test_the_skills_are_compile_and_cleanup():
    assert [p.parent.name for p in SKILLS] == ["cleanup", "compile"]


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.parent.name)
def test_each_skill_is_portable_and_small(skill):
    fields, body = frontmatter(skill)
    assert set(fields) <= PORTABLE, set(fields) - PORTABLE
    assert fields["name"] == skill.parent.name and re.fullmatch(r"[a-z0-9-]{1,64}", fields["name"])
    assert 1 <= len(fields["description"]) <= 1024
    assert len(body.splitlines()) < 500


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.parent.name)
def test_every_file_a_skill_points_to_exists(skill):
    folder = skill.parent
    for doc in [skill, *folder.glob("references/*.md")]:
        for link in re.findall(r"\]\(([^)#]+\.md)\)", doc.read_text(encoding="utf-8")):
            assert (doc.parent / link).is_file(), f"{doc.relative_to(ROOT)} links to missing {link}"


def _cli_flags() -> set[str]:
    flags = set()
    for cmd in ("compile", "size", "similar", "graph", "paths"):
        out = CliRunner().invoke(app, [cmd, "--help"], terminal_width=200).output
        flags |= set(re.findall(r"(?<![\w-])(--[a-z][a-z-]+|-[A-Za-z])\b", out))
    return flags


def test_every_vibepiler_flag_the_skills_name_exists():
    known = _cli_flags()
    for doc in (ROOT / "skills").rglob("*.md"):
        for line in doc.read_text(encoding="utf-8").splitlines():
            for cmd in re.findall(r"vibepiler (?:compile|size|similar|graph|paths)[^`\n]*", line):
                for flag in re.findall(r"(?<![\w-])(--[a-z][a-z-]+|-[A-Za-z])(?=[\s=\]`,]|$)", cmd):
                    assert flag in known, f"{doc.relative_to(ROOT)}: `{flag}` is not a vibepiler flag ({cmd.strip()})"


def test_the_skills_pin_the_release_they_describe():
    for doc in (ROOT / "skills").rglob("*.md"):
        for pin in re.findall(r"github\.com/NISH1001/vibepiler@v([\d.]+)", doc.read_text(encoding="utf-8")):
            assert pin == __version__, f"{doc.relative_to(ROOT)} pins v{pin}, the release is v{__version__}"


def test_the_readme_says_how_to_add_vibepiler_to_a_harness():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "/plugin marketplace add NISH1001/vibepiler" in readme and "/plugin install vibepiler@vibepiler" in readme
    assert "/vibepiler:compile" in readme and "/vibepiler:cleanup" in readme
    assert ".agents/skills" in readme  # other harnesses: copy the folder
