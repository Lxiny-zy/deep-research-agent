"""Immutable delivery versions, with a commit index shared by downloads and workspace.

The index is written last. Partially written candidates are never advertised.
Rendering is serialized across processes for one run; completed files are read,
not regenerated, even after the API process restarts.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..artifacts import ArtifactStore
from ..persistence.repository import RunDetail
from .gates import GateResult
from .publish import DeliveryBundle, DeliveryFile, delivery_fingerprint

INDEX = "deliveries/index.json"


def delivery_store(
    detail: RunDetail, artifact_root: str, quota: int | None = None
) -> tuple[ArtifactStore, str]:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", detail.id):
        raise ValueError("invalid run id")
    base = Path(artifact_root).resolve()
    root = (base / "runs" / detail.id).resolve()
    if not root.is_relative_to(base):
        raise ValueError("交付目录不能越出产物根目录")
    scratch = detail.orchestration.checkpoint.get("scratch", {}) if detail.orchestration else {}
    slug = scratch.get("_artifact_slug") or f"run-{detail.id}"
    store = ArtifactStore(root, max_bytes=None, max_total_bytes=quota, quota_root=artifact_root)
    store.manifest_path(slug)  # Validate the inherited slug before reading/writing.
    return store, slug


@contextmanager
def _lock(store: ArtifactStore) -> Iterator[None]:
    path = store.control_path("deliveries/render.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("delivery lock must not be a symlink")
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "r+b") as handle:
        if not path.stat().st_size:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + 300
        while True:
            try:
                handle.seek(0)
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise TimeoutError("交付物仍在生成，请稍后重试") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            handle.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _index(store: ArtifactStore) -> dict[str, Any]:
    path = store.control_path(INDEX)
    if not path.exists():
        return {"versions": {}}
    data = json.loads(store.read_control_text(INDEX))
    if not isinstance(data, dict) or not isinstance(data.get("versions"), dict):
        raise ValueError("交付版本登记损坏，拒绝覆盖已有文件")
    return data


def _stage(version: str, registry: dict) -> str:
    if not re.fullmatch("[0-9a-f]{64}", version):
        raise ValueError("invalid delivery version")
    stage = registry.get("storage_stage", f"deliverables-{version}")
    if stage != f"deliverables-{version}" and not re.fullmatch(
        rf"d-{version[:16]}(?:-[1-9][0-9]*)?", stage
    ):
        raise ValueError("invalid delivery storage stage")
    return stage


def _path(store: ArtifactStore, slug: str, version: str, name: str, registry: dict) -> str:
    path = store.path_for(slug, _stage(version, registry), name, area="output")
    return path.relative_to(store.workspace_root).as_posix()


def _read_file(store: ArtifactStore, path: str, record: dict) -> bytes:
    try:
        data = store.read_bytes(path)
    except FileNotFoundError as exc:
        raise ValueError("已登记交付文件缺失，未重新生成或覆盖原版本") from exc
    if len(data) != record["size"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
        raise ValueError("已登记交付文件校验失败，未重新生成或覆盖原版本")
    return data


def _load(store: ArtifactStore, slug: str, version: str, registry: dict) -> DeliveryBundle:
    if registry.get("content_version") != version:
        raise ValueError("交付版本与登记不一致")
    files = [
        DeliveryFile(
            name=item["name"],
            format=item["format"],
            title=item["title"],
            role=item["role"],
            data=_read_file(store, _path(store, slug, version, item["name"], registry), item),
            status=item["status"],
            issues=item.get("issues", []),
        )
        for item in registry["items"]
    ]
    return DeliveryBundle(
        template=registry["template"],
        title=registry["title"],
        files=files,
        gates=[GateResult(**gate) for gate in registry["gates"]],
        status=registry["status"],
        generated_at=registry["generated_at"],
        content_version=version,
    )


def load_version(detail: RunDetail, artifact_root: str, version: str) -> DeliveryBundle:
    store, slug = delivery_store(detail, artifact_root)
    registry = _index(store)["versions"].get(version)
    if registry is None:
        raise FileNotFoundError("交付版本不存在")
    return _load(store, slug, version, registry)


def build_or_load(
    detail: RunDetail,
    artifact_root: str,
    quota: int | None,
    build: Callable[[RunDetail], DeliveryBundle],
) -> DeliveryBundle:
    store, slug = delivery_store(detail, artifact_root, quota)
    version = delivery_fingerprint(detail)
    with _lock(store):
        index = _index(store)
        previous = index["versions"].get(version)
        if previous is not None:
            return _load(store, slug, version, previous)
        bundle = build(detail)
        bundle.content_version = version
        registry = bundle.registry()
        # Keep the public full digest, but avoid MAX_PATH failures in nested
        # Windows workspaces. Older committed versions retain their original path.
        used = {_stage(v, entry) for v, entry in index["versions"].items()}
        stage = f"d-{version[:16]}"
        suffix = 0
        while stage in used:
            suffix += 1
            stage = f"d-{version[:16]}-{suffix}"
        registry["storage_stage"] = stage
        for file, item in zip(bundle.files, registry["items"], strict=True):
            store.write(
                slug,
                stage,
                file.name,
                file.data,
                area="output",
                mime_type=item["mime_type"],
                update_manifest=False,
            )
        # No published version is overwritten. Failed writes before this point
        # leave only hidden candidates, and a retry can finish the same version.
        index["versions"][version] = registry
        store.write_control_json(INDEX, index)
        return bundle


def workspace_files(detail: RunDetail, artifact_root: str) -> list[dict[str, Any]]:
    store, slug = delivery_store(detail, artifact_root)
    files = []
    for version, registry in _index(store)["versions"].items():
        for item in registry["items"]:
            files.append(
                {
                    "path": _path(store, slug, version, item["name"], registry),
                    "area": "output",
                    "stage": f"交付版本 {version[:8]}",
                    "name": item["name"],
                    "size": item["size"],
                    "sha256": item["sha256"],
                    "mime_type": item["mime_type"],
                    "step": None,
                    "attempt": 1,
                    "created_at": registry["generated_at"],
                    "content_version": version,
                }
            )
    return files


def read_workspace_file(
    detail: RunDetail, artifact_root: str, path: str
) -> tuple[bytes, str] | None:
    store, slug = delivery_store(detail, artifact_root)
    for version, registry in _index(store)["versions"].items():
        for item in registry["items"]:
            if _path(store, slug, version, item["name"], registry) == path:
                return _read_file(store, path, item), item["mime_type"]
    return None
