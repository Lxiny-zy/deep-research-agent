from __future__ import annotations

import hashlib
import json
import os
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


def test_atomic_replacement_keeps_previous_file_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ArtifactStore(tmp_path)
    first = store.write_text("topic", "research", "notes.txt", "old")
    target = store.absolute_path(first)

    original_replace = os.replace

    def fail_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        if Path(destination) == target:
            raise OSError("simulated atomic rename failure")
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="atomic rename failure"):
        store.write_text("topic", "research", "notes.txt", "new")

    assert target.read_text(encoding="utf-8") == "old"
    assert not [item for item in target.parent.iterdir() if item.name.endswith(".tmp")]


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
