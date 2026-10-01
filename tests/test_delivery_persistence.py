from __future__ import annotations

import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from deep_research.artifacts import ArtifactStore
from deep_research.models import Report
from deep_research.persistence.repository import RunDetail
from deep_research.workbench.delivery_store import (
    INDEX,
    build_or_load,
    delivery_store,
    load_version,
    workspace_files,
)
from deep_research.workbench.publish import DeliveryBundle, DeliveryFile
from deep_research.workbench.workspace import read_workspace_file


def detail(body="version one"):
    return RunDetail(
        id="run-one",
        query="q",
        status="done",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        report=Report(query="q", markdown=body, citations=[]),
    )


def bundle(run):
    return DeliveryBundle(
        "test",
        "title",
        [DeliveryFile("report.md", "md", "title", "source", run.report.markdown.encode())],
        [],
        "pass",
        "2026-01-01T00:00:00Z",
    )


def test_restart_and_versioned_download_reuse_exact_saved_bytes(tmp_path):
    first = build_or_load(detail(), str(tmp_path), None, bundle)

    def forbidden(_):
        raise AssertionError("a saved version must not render again")

    restored = build_or_load(detail(), str(tmp_path), None, forbidden)
    second = build_or_load(detail("version two"), str(tmp_path), None, bundle)
    assert first.content_version == restored.content_version != second.content_version
    assert restored.files[0].data == first.files[0].data == b"version one"
    assert (
        load_version(detail(), str(tmp_path), first.content_version).files[0].data == b"version one"
    )
    files = workspace_files(detail(), str(tmp_path))
    assert len(files) == 2
    for record in files:
        data, _, _ = read_workspace_file(detail(), str(tmp_path), record["path"])
        assert data in {b"version one", b"version two"}


def test_corrupted_version_is_not_overwritten_or_silently_regenerated(tmp_path):
    build_or_load(detail(), str(tmp_path), None, bundle)
    store, _ = delivery_store(detail(), str(tmp_path))
    path = workspace_files(detail(), str(tmp_path))[0]["path"]
    store.absolute_path(path).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="校验失败"):
        build_or_load(detail(), str(tmp_path), None, bundle)


def test_failed_commit_does_not_advertise_partial_files(tmp_path, monkeypatch):
    original = ArtifactStore.write_control_json

    def fail(self, name, *args, **kwargs):
        if name == INDEX:
            raise OSError("simulated commit failure")
        return original(self, name, *args, **kwargs)

    monkeypatch.setattr(ArtifactStore, "write_control_json", fail)
    with pytest.raises(OSError):
        build_or_load(detail(), str(tmp_path), None, bundle)
    assert workspace_files(detail(), str(tmp_path)) == []
    monkeypatch.setattr(ArtifactStore, "write_control_json", original)
    assert build_or_load(detail(), str(tmp_path), None, bundle).files[0].data == b"version one"


def _process_build(root: str) -> str:
    def build(run):
        with (Path(root) / "build-count.txt").open("a") as handle:
            handle.write("render\n")
        time.sleep(0.2)
        return bundle(run)

    return build_or_load(detail(), root, None, build).content_version


def test_two_processes_share_one_render(tmp_path):
    with ProcessPoolExecutor(2, mp_context=multiprocessing.get_context("spawn")) as pool:
        versions = list(pool.map(_process_build, [str(tmp_path), str(tmp_path)]))
    assert versions[0] == versions[1]
    assert (tmp_path / "build-count.txt").read_text().splitlines() == ["render"]


def test_nested_workspace_and_legacy_storage_remain_readable(tmp_path):
    root = tmp_path / ("nested-" + "x" * max(1, 139 - len(str(tmp_path))))
    first = build_or_load(detail(), str(root), None, bundle)
    store, slug = delivery_store(detail(), str(root))
    assert len(str(store.absolute_path(workspace_files(detail(), str(root))[0]["path"]))) < 250
    assert load_version(detail(), str(root), first.content_version).files[0].data == b"version one"

    # Simulate an already published version using the previous full-digest path.
    legacy_store, legacy_slug = delivery_store(detail(), str(tmp_path))
    registry = first.registry()
    legacy_store.write(
        legacy_slug,
        f"deliverables-{first.content_version}",
        "report.md",
        b"version one",
        area="output",
        update_manifest=False,
    )
    legacy_store.write_control_json(INDEX, {"versions": {first.content_version: registry}})
    assert (
        load_version(detail(), str(tmp_path), first.content_version).files[0].data == b"version one"
    )


def test_storage_prefix_collision_does_not_overwrite_an_existing_version(tmp_path, monkeypatch):
    from deep_research.workbench import delivery_store as module

    prefix = "a" * 16
    monkeypatch.setattr(module, "delivery_fingerprint", lambda run: prefix + "1" * 48)
    first = build_or_load(detail(), str(tmp_path), None, bundle)
    monkeypatch.setattr(module, "delivery_fingerprint", lambda run: prefix + "2" * 48)
    second = build_or_load(detail("second"), str(tmp_path), None, bundle)
    assert (
        load_version(detail(), str(tmp_path), first.content_version).files[0].data == b"version one"
    )
    assert load_version(detail(), str(tmp_path), second.content_version).files[0].data == b"second"
