from pathlib import Path

from scripts.check_dependency_locks import _check


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_dependency_lock_check_accepts_compatible_direct_dependency(tmp_path: Path) -> None:
    requirements = _write(tmp_path / "requirements.txt", "example[extra]>=1,<2\n")
    lock = _write(tmp_path / "requirements.lock", "example==1.5.0\n")

    assert _check(requirements, lock) == []


def test_dependency_lock_check_reports_missing_and_incompatible_versions(tmp_path: Path) -> None:
    requirements = _write(tmp_path / "requirements.txt", "first>=2\nsecond>=1\n")
    lock = _write(tmp_path / "requirements.lock", "first==1.9\n")

    assert _check(requirements, lock) == [
        "requirements.lock: first locks 1.9, outside >=2",
        "requirements.lock: missing direct dependency second",
    ]


def test_dependency_lock_check_follows_requirement_includes(tmp_path: Path) -> None:
    _write(tmp_path / "base.txt", "base-package==3\n")
    requirements = _write(tmp_path / "dev.txt", "-r base.txt\ndev-package>=4\n")
    lock = _write(tmp_path / "dev.lock", "base-package==3\ndev-package==4.1\n")

    assert _check(requirements, lock) == []
