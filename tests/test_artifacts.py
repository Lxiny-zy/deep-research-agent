from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

import pytest

from deep_research.artifacts import (
    ArtifactIntegrityError,
    ArtifactManifest,
    ArtifactStore,
    ArtifactValidationError,
    ManifestError,
    PathTraversalError,
)


def test_write_records_roots_hash_size_mime_and_manifest(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)

    record = store.write_text(
        "topic-slug",
        "research",
        "notes.md",
        "hello\n",
        metadata={"source": "test"},
    )

    path = tmp_path / "work" / "topic-slug" / "research" / "notes.md"
    assert path.is_file()
    assert record.path == "work/topic-slug/research/notes.md"
    assert record.size_bytes == path.stat().st_size == 6
    assert record.sha256 == hashlib.sha256(b"hello\n").hexdigest()
    assert record.mime_type == "text/markdown"
    assert store.absolute_path(record) == path.resolve()

    manifest_path = store.manifest_path("topic-slug")
    assert manifest_path == tmp_path / ".framework" / "manifests" / "topic-slug.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["artifacts"][0]["sha256"] == record.sha256
    loaded = store.load_manifest("topic-slug")
    assert loaded.get(record.path) == record


def test_output_tree_and_nested_names_are_canonical(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    record = store.write_bytes(
        "topic",
        "final",
        "figures/result.png",
        b"PNG",
        output=True,
    )

    assert record.area == "output"
    assert record.path == "output/topic/final/figures/result.png"
    assert (tmp_path / record.path).is_file()
    assert store.output_dir("topic", "final") == tmp_path / "output" / "topic" / "final"


@pytest.mark.skipif(os.name != "nt", reason="Windows extended path aliases")
def test_concurrent_path_resolution_alias_does_not_bypass_or_break_containment(
    tmp_path, monkeypatch
):
    from deep_research.artifacts import _contained

    base = tmp_path / "framework"
    original = Path.resolve

    def mixed_resolve(path, *args, **kwargs):
        resolved = original(path, *args, **kwargs)
        return Path("\\\\?\\" + str(resolved)) if path == base else resolved

    monkeypatch.setattr(Path, "resolve", mixed_resolve)
    assert _contained(base / "deliveries/render.lock", base) == base / "deliveries/render.lock"
    with pytest.raises(PathTraversalError):
        _contained(tmp_path / "outside.txt", base)


@pytest.mark.parametrize(
    ("slug", "stage", "name"),
    [
        ("../escape", "research", "x.txt"),
        ("topic", "..", "x.txt"),
        ("topic", "research", "../x.txt"),
        ("topic", "research", "/tmp/x.txt"),
        ("topic", "research", "C:\\temp\\x.txt"),
        ("topic", "research", "nested//x.txt"),
    ],
)
def test_path_traversal_is_rejected(tmp_path: Path, slug: str, stage: str, name: str) -> None:
    store = ArtifactStore(tmp_path)
    with pytest.raises(PathTraversalError):
        store.write_text(slug, stage, name, "blocked")
    assert not list(tmp_path.rglob("x.txt"))


def test_symlink_stage_cannot_escape_workspace(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    outside = tmp_path.parent / f"artifact-outside-{os.getpid()}"
    outside.mkdir()
    try:
        (tmp_path / "work" / "topic").mkdir(parents=True)
        try:
            (tmp_path / "work" / "topic" / "research").symlink_to(outside, target_is_directory=True)
        except (NotImplementedError, OSError):
            pytest.skip("symlinks are unavailable on this platform")

        with pytest.raises(PathTraversalError):
            store.write_text("topic", "research", "x.txt", "blocked")
        assert not (outside / "x.txt").exists()
    finally:
        # Keep the test independent of a user's temp-directory cleanup policy.
        for child in outside.iterdir():
            child.unlink()
        outside.rmdir()


def test_symlink_artifact_root_cannot_escape_workspace(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    outside = tmp_path.parent / f"artifact-root-outside-{os.getpid()}"
    outside.mkdir()
    try:
        try:
            (tmp_path / "work").symlink_to(outside, target_is_directory=True)
        except (NotImplementedError, OSError):
            pytest.skip("symlinks are unavailable on this platform")

        with pytest.raises(PathTraversalError):
            store.write_text("topic", "research", "x.txt", "blocked")
        assert not (outside / "topic" / "research" / "x.txt").exists()
    finally:
        for child in outside.rglob("*"):
            if child.is_file() or child.is_symlink():
                child.unlink()
        for child in sorted(outside.rglob("*"), reverse=True):
            if child.is_dir():
                child.rmdir()
        outside.rmdir()


def test_size_and_mime_limits_fail_before_commit(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path, max_bytes=8)

    with pytest.raises(ArtifactValidationError, match="maximum size"):
        store.write_text("topic", "research", "large.txt", "123456789")
    assert not (tmp_path / "work/topic/research/large.txt").exists()

    with pytest.raises(ArtifactValidationError, match="MIME"):
        store.write_text(
            "topic",
            "research",
            "notes.md",
            "text",
            expected_mime="application/pdf",
        )
    assert not (tmp_path / "work/topic/research/notes.md").exists()

    with pytest.raises(ArtifactValidationError, match="not allowed"):
        store.write_text(
            "topic",
            "research",
            "notes.md",
            "text",
            allowed_mime_types=("application/json",),
        )


@pytest.mark.parametrize("long_path", [False, True])
def test_atomic_replacement_keeps_previous_file_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, long_path: bool,
) -> None:
    from deep_research.artifacts import _native_fs_path

    root = tmp_path / ("a" * 90) / ("b" * 90) / ("c" * 90) if long_path else tmp_path
    store = ArtifactStore(root)
    first = store.write_text("topic", "research", "notes.txt", "old")
    target = Path(_native_fs_path(store.absolute_path(first)))

    original_replace = os.replace

    def fail_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        if os.path.samefile(destination, target):
            raise OSError("simulated atomic rename failure")
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="atomic rename failure"):
        store.write_text("topic", "research", "notes.txt", "new")

    assert target.read_text(encoding="utf-8") == "old"
    assert store.verify_manifest("topic")
    assert not [item for item in target.parent.iterdir() if item.name.endswith(".tmp")]


def test_long_paths_preserve_manifest_control_files_and_overwrite_guard(tmp_path: Path) -> None:
    # Exercise paths beyond Windows MAX_PATH without modifying system settings.
    store = ArtifactStore(tmp_path / ("a" * 90) / ("b" * 90) / ("c" * 90))
    record = store.write_text("topic", "research", "notes.txt", "old")
    assert len(str(store.absolute_path(record))) > 260
    assert store.read_text(record) == "old"
    assert store.verify_manifest("topic")
    assert store.register("topic", "research", "notes.txt").sha256 == record.sha256
    with pytest.raises(FileExistsError):
        store.write_text("topic", "research", "notes.txt", "new", overwrite=False)
    replacement = store.write_text("topic", "research", "notes.txt", "new")
    assert store.read_text(replacement) == "new"
    assert store.verify_manifest("topic")
    store.write_control_text("checkpoints/state.json", '{"round": 1}')
    assert store.read_control_text("checkpoints/state.json") == '{"round": 1}'
    assert store.remove(replacement)
    assert store.list_artifacts("topic") == []


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing semantics")
def test_atomic_replacement_recovers_after_reader_releases_file(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path)
    record = store.write_text("topic", "research", "notes.txt", "old")
    original_replace = os.replace
    busy = threading.Event()
    errors = []

    def observe(source, destination):
        try:
            original_replace(source, destination)
        except PermissionError:
            busy.set()
            raise

    def write():
        try:
            store.write_text("topic", "research", "notes.txt", "new")
        except Exception as exc:
            errors.append(exc)

    monkeypatch.setattr(os, "replace", observe)
    reader = store.open(record)
    writer = threading.Thread(target=write)
    try:
        writer.start()
        assert busy.wait(2), "The held reader must exercise a sharing failure"
    finally:
        reader.close()
        writer.join(3)
    assert not writer.is_alive() and not errors
    assert store.read_text(record) == "new"
    assert store.verify_manifest("topic")


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing retry limit")
def test_persistent_replace_denial_is_bounded_and_preserves_old_file(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path)
    record = store.write_text("topic", "research", "notes.txt", "old")
    denial = PermissionError(13, "persistent denial")
    denial.winerror = 5
    attempts = []

    def deny(source, destination):
        attempts.append(destination)
        raise denial

    monkeypatch.setattr(os, "replace", deny)
    with pytest.raises(PermissionError, match="persistent denial") as raised:
        store.write_text("topic", "research", "notes.txt", "new")
    assert raised.value is denial and len(attempts) == 6
    assert store.read_text(record) == "old" and store.verify_manifest("topic")
    assert not list(store.absolute_path(record).parent.glob(".dra-*.tmp"))


def test_register_and_verify_detect_tampering(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    path = store.path_for("topic", "research", "result.json", create=True)
    path.write_text('{"ok": true}', encoding="utf-8")
    record = store.register("topic", "research", "result.json")
    assert store.verify(record)

    path.write_text('{"ok": false}', encoding="utf-8")
    assert not store.verify(record)
    with pytest.raises(ArtifactIntegrityError):
        store.verify_artifact(record)
    assert not store.verify_manifest("topic")


def test_manifest_round_trip_and_corrupt_manifest_error(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    record = store.write_text("topic", "research", "result.txt", "ok")
    original = store.load_manifest("topic")
    restored = ArtifactManifest.from_json(original.to_json())
    assert restored.to_dict() == original.to_dict()

    store.manifest_path("topic").write_text("{bad", encoding="utf-8")
    with pytest.raises(ManifestError, match="valid JSON"):
        store.load_manifest("topic")
    assert record.path.startswith("work/topic/research/")
