"""Explicit skill discovery and reference checking.

The planner contract treats skills as declared inputs, not a keyword-triggered
side effect.  ``SkillResolver`` understands both the extracted framework
layout (``framework/skills/skill_*.md``) and the production layout
(``.claude/skills/<name>/SKILL.md``), while keeping the resolution decision
explicit and auditable.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path


class SkillError(ValueError):
    """Base class for skill contract errors."""


class SkillNotFoundError(SkillError):
    """A plan references a skill that is not installed."""


class SkillContractError(SkillError):
    """A step does not explicitly reference the skill it declares."""


@dataclass(frozen=True, slots=True)
class SkillMetadata:
    name: str
    path: Path
    description: str = ""
    frontmatter: Mapping[str, str] = field(default_factory=dict)

    @property
    def reference(self) -> str:
        return self.path.as_posix()


def _frontmatter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    values: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, separator, value = line.partition(":")
        if separator and key.strip():
            values[key.strip().lower()] = value.strip().strip("'\"")
    return values


def _safe_name(value: str) -> str:
    name = value.strip().lower()
    if name.startswith("skill_"):
        name = name[6:]
    if name.endswith(".md"):
        name = name[:-3]
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
        raise SkillError(f"invalid skill name: {value!r}")
    return name


class SkillResolver:
    """Discover and resolve skills from trusted, read-only roots."""

    def __init__(self, roots: Iterable[str | Path] = ()) -> None:
        self.roots = tuple(Path(root).expanduser().resolve() for root in roots)
        self._cache: dict[str, SkillMetadata] | None = None

    def discover(self, *, refresh: bool = False) -> dict[str, SkillMetadata]:
        if self._cache is not None and not refresh:
            return dict(self._cache)
        found: dict[str, SkillMetadata] = {}
        for root in self.roots:
            if not root.exists():
                continue
            candidates: list[Path] = []
            if root.is_file() and root.name.lower() in {"skill.md", "skills.md"}:
                candidates.append(root)
            elif root.is_dir():
                # Production: <root>/<skill>/SKILL.md
                candidates.extend(path for path in root.glob("*/SKILL.md") if path.is_file())
                # Extracted framework: skill_<name>.md (and a few plain *.md files).
                candidates.extend(path for path in root.glob("skill_*.md") if path.is_file())
                candidates.extend(
                    path
                    for path in root.glob("*.md")
                    if path.is_file() and path.name not in {"README.md"}
                )
            for path in candidates:
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    continue
                meta = _frontmatter(text)
                raw_name = meta.get("name") or (
                    path.parent.name if path.name.upper() == "SKILL.MD" else path.stem
                )
                try:
                    name = _safe_name(raw_name)
                except SkillError:
                    continue
                # First trusted root wins, making precedence deterministic.
                found.setdefault(
                    name,
                    SkillMetadata(
                        name=name,
                        path=path,
                        description=meta.get("description", ""),
                        frontmatter=meta,
                    ),
                )
        self._cache = found
        return dict(found)

    def resolve(self, name: str) -> SkillMetadata:
        canonical = _safe_name(name)
        result = self.discover().get(canonical)
        if result is None:
            raise SkillNotFoundError(f"skill is not installed: {canonical}")
        return result

    def resolve_many(self, names: Iterable[str]) -> list[SkillMetadata]:
        return [self.resolve(name) for name in names]

    def validate_references(self, names: Iterable[str]) -> list[SkillMetadata]:
        """Resolve a declared list and return metadata in declaration order."""

        result: list[SkillMetadata] = []
        seen: set[str] = set()
        for name in names:
            canonical = _safe_name(name)
            if canonical in seen:
                continue
            seen.add(canonical)
            result.append(self.resolve(canonical))
        return result

    def require_explicit_references(
        self, prompt: str, names: Iterable[str], *, allow_basename: bool = True
    ) -> None:
        """Ensure a prompt names each declared ``SKILL.md`` explicitly."""

        if not isinstance(prompt, str) or not prompt.strip():
            raise SkillContractError("step prompt must be non-empty")
        for metadata in self.validate_references(names):
            expected = metadata.name.casefold()
            prompt_lower = prompt.casefold()
            patterns = (
                f"{expected}/skill.md",
                f"skill_{expected}.md",
                f"skill-{expected}.md",
                f"{expected}.md",
            )
            if not any(pattern in prompt_lower for pattern in patterns):
                if allow_basename and metadata.path.name.casefold() in prompt_lower:
                    continue
                raise SkillContractError(
                    f"step prompt must explicitly reference skill {metadata.name!r}"
                )

    def render_contract(self, names: Iterable[str]) -> str:
        """Render deterministic, auditable skill paths for a step prompt."""

        metadata = self.validate_references(names)
        return "\n".join(f"- {item.name}: read `{item.reference}`" for item in metadata)


def default_skill_resolver(project_root: str | Path = ".") -> SkillResolver:
    root = Path(project_root).expanduser().resolve()
    return SkillResolver((root / ".claude" / "skills", root / "framework" / "skills"))


__all__ = [
    "SkillContractError",
    "SkillError",
    "SkillMetadata",
    "SkillNotFoundError",
    "SkillResolver",
    "default_skill_resolver",
]
