#!/usr/bin/env python3
"""Check that direct requirements are represented by compatible locked versions."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
LOCKED_PACKAGE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9_.-]*)==([^\s;\\]+)")


def _requirements(path: Path, seen: set[Path] | None = None) -> list[Requirement]:
    seen = set() if seen is None else seen
    path = path.resolve()
    if path in seen:
        return []
    seen.add(path)

    result: list[Requirement] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.split(" #", 1)[0].rstrip()
        if line.startswith("-r ") or line.startswith("--requirement "):
            included = line.split(maxsplit=1)[1].strip()
            result.extend(_requirements(path.parent / included, seen))
            continue
        if line.startswith("-"):
            continue
        result.append(Requirement(line))
    return result


def _locked(path: Path) -> dict[str, Version]:
    locked: dict[str, Version] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        match = LOCKED_PACKAGE.match(raw)
        if not match:
            continue
        name, version = match.groups()
        normalized = canonicalize_name(name)
        parsed = Version(version)
        previous = locked.get(normalized)
        if previous is not None and previous != parsed:
            raise ValueError(
                f"{path.name}: multiple versions for {normalized}: {previous}, {parsed}"
            )
        locked[normalized] = parsed
    return locked


def _check(requirements_path: Path, lock_path: Path) -> list[str]:
    requirements = _requirements(requirements_path)
    locked = _locked(lock_path)
    errors: list[str] = []
    for requirement in requirements:
        name = canonicalize_name(requirement.name)
        version = locked.get(name)
        if version is None:
            errors.append(f"{lock_path.name}: missing direct dependency {requirement.name}")
        elif requirement.specifier and version not in requirement.specifier:
            errors.append(
                f"{lock_path.name}: {requirement.name} locks {version}, "
                f"outside {requirement.specifier}"
            )
    return errors


def main() -> int:
    errors: list[str] = []
    lock_pairs = (
        ("requirements.txt", "requirements.lock"),
        ("requirements-dev.txt", "requirements-dev.lock"),
    )
    for requirements_name, lock_name in lock_pairs:
        requirements_path = ROOT / requirements_name
        lock_path = ROOT / lock_name
        if not requirements_path.is_file() or not lock_path.is_file():
            errors.append(f"missing lock input: {requirements_name} or {lock_name}")
            continue
        try:
            errors.extend(_check(requirements_path, lock_path))
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
    if errors:
        for error in errors:
            print(f"dependency lock check failed: {error}", file=sys.stderr)
        return 1
    print("dependency locks contain compatible versions for all direct requirements")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
