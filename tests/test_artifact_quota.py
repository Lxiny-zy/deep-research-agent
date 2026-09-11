from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from deep_research import artifact_lifecycle
from deep_research.artifacts import ArtifactStore, ArtifactValidationError


def test_shared_quota_prevents_concurrent_overcommit_and_reclaims_removal(tmp_path):
    def write(index):
        store = ArtifactStore(tmp_path / str(index), quota_root=tmp_path, max_total_bytes=100)
        try:
            return store, store.write_bytes(
                "topic", "final", "r.txt", b"x" * 60, update_manifest=False
            )
        except ArtifactValidationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(write, [1, 2]))
    successful = [item for item in outcomes if item is not None]
    assert len(successful) == 1
    store, record = successful[0]
    assert artifact_lifecycle.disk_usage(tmp_path) == 60
    store.remove(record, update_manifest=False)
    assert write(3) is not None


def test_failed_atomic_replacement_keeps_prior_file_and_allowance(tmp_path):
    store = ArtifactStore(tmp_path, max_total_bytes=100)
    record = store.write_bytes("topic", "final", "r.txt", b"a" * 70, update_manifest=False)
    with pytest.raises(ArtifactValidationError):
        store.write_bytes("topic", "final", "r.txt", b"b" * 101, update_manifest=False)
    assert store.read_bytes(record) == b"a" * 70
    store.write_bytes("topic", "final", "r.txt", b"b" * 100, update_manifest=False)
    assert artifact_lifecycle.disk_usage(tmp_path) == 100


def test_interrupted_write_recounts_disk_instead_of_trusting_stale_ledger(tmp_path):
    (tmp_path / "external.bin").write_bytes(b"x" * 90)
    (tmp_path / ".artifact-quota-state.json").write_text(
        json.dumps({"version": 1, "used": 0, "scanned_at": 0, "dirty": True}), encoding="utf-8"
    )
    store = ArtifactStore(tmp_path, max_total_bytes=100)
    with pytest.raises(ArtifactValidationError):
        store.write_bytes("topic", "final", "r.txt", b"x" * 20, update_manifest=False)
    assert artifact_lifecycle.disk_usage(tmp_path) == 90


def test_regular_writes_do_not_rescan_all_history(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path, max_total_bytes=10_000)
    scans = 0
    original = artifact_lifecycle.disk_usage

    def observe(root):
        nonlocal scans
        scans += 1
        return original(root)

    monkeypatch.setattr(artifact_lifecycle, "disk_usage", observe)
    for index in range(5):
        store.write_bytes("topic", "final", f"{index}.txt", b"text")
    assert scans == 1
    assert artifact_lifecycle.reconcile_quota(tmp_path) == original(tmp_path)
