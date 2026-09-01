from __future__ import annotations

from pathlib import Path

import pytest

from deep_research.skills import (
    SkillContractError,
    SkillError,
    SkillNotFoundError,
    SkillResolver,
    default_skill_resolver,
)


def _write_skill(path: Path, *, name: str | None = None, description: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = ""
    if name is not None:
        frontmatter = f"---\nname: {name}\ndescription: {description}\n---\n"
    path.write_text(frontmatter + "Use this skill.\n", encoding="utf-8")


def test_discover_supports_production_and_extracted_framework_layouts(tmp_path: Path) -> None:
    claude_root = tmp_path / ".claude" / "skills"
    framework_root = tmp_path / "framework" / "skills"
    _write_skill(
        claude_root / "academic-search" / "SKILL.md",
        name="academic-search",
        description="Search peer-reviewed sources",
    )
    _write_skill(framework_root / "skill_pdf-extract.md")

    resolver = SkillResolver((claude_root, framework_root))
    discovered = resolver.discover()

    assert set(discovered) == {"academic-search", "pdf-extract"}
    assert discovered["academic-search"].description == "Search peer-reviewed sources"
    assert discovered["academic-search"].path == claude_root / "academic-search" / "SKILL.md"
    assert discovered["academic-search"].reference.endswith(
        ".claude/skills/academic-search/SKILL.md"
    )
    assert discovered["pdf-extract"].path == framework_root / "skill_pdf-extract.md"


def test_first_trusted_root_wins_and_discovery_is_cached(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_skill(first / "shared" / "SKILL.md", name="shared", description="first")
    _write_skill(second / "shared" / "SKILL.md", name="shared", description="second")
    resolver = SkillResolver((first, second))

    metadata = resolver.resolve("shared")
    assert metadata.description == "first"

    _write_skill(first / "new-skill" / "SKILL.md", name="new-skill")
    assert "new-skill" not in resolver.discover()
    assert "new-skill" in resolver.discover(refresh=True)


def test_resolve_aliases_and_validate_references_deduplicate_in_order(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root / "skill_one.md")
    _write_skill(root / "two" / "SKILL.md", name="two")
    resolver = SkillResolver((root,))

    assert resolver.resolve("skill_one.md").name == "one"
    result = resolver.validate_references(["two", "one", "two", "skill_one.md"])
    assert [item.name for item in result] == ["two", "one"]

    with pytest.raises(SkillNotFoundError, match="not installed"):
        resolver.resolve("missing")
    with pytest.raises(SkillError, match="invalid skill name"):
        resolver.resolve("../escape")


def test_explicit_skill_reference_contract_accepts_paths_and_rejects_implicit_mentions(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".claude" / "skills"
    _write_skill(root / "academic-search" / "SKILL.md", name="academic-search")
    resolver = SkillResolver((root,))

    resolver.require_explicit_references(
        "Read `.claude/skills/academic-search/SKILL.md` before searching.",
        ["academic-search"],
    )
    resolver.require_explicit_references(
        "Read SKILL.md before searching.", ["academic-search"], allow_basename=True
    )
    with pytest.raises(SkillContractError, match="explicitly reference"):
        resolver.require_explicit_references("Search academic papers.", ["academic-search"])
    with pytest.raises(SkillContractError, match="non-empty"):
        resolver.require_explicit_references("", ["academic-search"])


def test_render_contract_is_deterministic_and_preserves_declaration_order(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root / "skill_alpha.md")
    _write_skill(root / "skill_beta.md")
    resolver = SkillResolver((root,))

    rendered = resolver.render_contract(["beta", "alpha"])

    assert rendered.splitlines() == [
        f"- beta: read `{(root / 'skill_beta.md').as_posix()}`",
        f"- alpha: read `{(root / 'skill_alpha.md').as_posix()}`",
    ]


def test_invalid_files_are_ignored_and_default_resolver_uses_project_roots(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root / "skill_valid.md")
    _write_skill(root / "skill bad.md")
    _write_skill(root / "skill_invalid.md", name="../bad")
    (root / "README.md").write_text("not a skill", encoding="utf-8")

    resolver = SkillResolver((root,))
    assert set(resolver.discover()) == {"valid"}

    project_skill = tmp_path / ".claude" / "skills" / "project-skill" / "SKILL.md"
    _write_skill(project_skill, name="project-skill")
    default = default_skill_resolver(tmp_path)
    assert default.resolve("project-skill").path == project_skill
