from pathlib import Path

from scripts.check_dependency_locks import _check, _check_shared_versions


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


def test_shared_locks_accept_matching_versions_and_independent_packages(tmp_path: Path) -> None:
    runtime = _write(tmp_path / "runtime.lock", "shared_package==1.5\nruntime-only==2\n")
    workbench = _write(tmp_path / "workbench.lock", "shared-package==1.5\ncharts==3\n")

    assert _check_shared_versions([runtime, workbench]) == []


def test_shared_locks_reject_conflicts_even_when_direct_requirements_pass(tmp_path: Path) -> None:
    requirements = _write(tmp_path / "requirements.txt", "shared>=1,<2\n")
    runtime = _write(tmp_path / "runtime.lock", "shared==1.5\n")
    workbench = _write(tmp_path / "workbench.lock", "shared==1.6\n")

    assert _check(requirements, runtime) == []
    assert _check(requirements, workbench) == []
    assert _check_shared_versions([runtime, workbench]) == [
        "shared: runtime.lock locks 1.5, but workbench.lock locks 1.6"
    ]


def test_dependency_check_main_rejects_cross_group_conflict(tmp_path: Path, monkeypatch) -> None:
    from scripts import check_dependency_locks

    for group in ("", "-pdf", "-workbench", "-dev"):
        _write(tmp_path / f"requirements{group}.txt", "shared>=1,<2\n")
        version = "1.6" if group == "-workbench" else "1.5"
        _write(tmp_path / f"requirements{group}.lock", f"shared=={version}\n")
    monkeypatch.setattr(check_dependency_locks, "ROOT", tmp_path)

    assert check_dependency_locks.main() == 1


def test_repository_lock_groups_use_compatible_versions() -> None:
    root = Path(__file__).resolve().parents[1]

    assert _check_shared_versions(sorted(root.glob("requirements*.lock"))) == []
