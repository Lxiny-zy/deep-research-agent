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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..artifacts import ArtifactStore
from ..persistence.repository import RunDetail
from .gates import GateResult
from .publish import DeliveryBundle, DeliveryFile, delivery_fingerprint

INDEX = "deliveries/index.json"


class DeliveryConflict(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


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
        # Both flock and Windows byte-range locks work beyond EOF. Writing a
        # sentinel before acquiring the lock races with another opener: its
        # Windows lock can deny our buffered flush (and even handle.close()).
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
    context = registry.get("_render_context", {})
    if context and _digest(context) != registry.get("render_context_sha256"):
        raise ValueError("交付定稿快照校验失败")
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
        input_version=registry.get("input_version", version),
        parent_version=registry.get("parent_version", ""),
        attempt=registry.get("attempt", 1),
        failures=registry.get("failures", []),
        render_context=context,
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
        current = index.get("current", {}).get(version, version)
        previous = index["versions"].get(current)
        if previous is not None:
            return _load(store, slug, current, previous)
        if current != version:
            raise ValueError("当前交付版本不存在")
        from .render_progress import checkpoint_rendering

        with checkpoint_rendering(store, slug, version, index):
            bundle = build(detail)
            bundle.content_version = bundle.input_version = version
            _commit(store, slug, index, bundle)
        return bundle


def _commit(store: ArtifactStore, slug: str, index: dict, bundle: DeliveryBundle) -> None:
    from .completion import validate_bundle_files

    validate_bundle_files(bundle)
    version = bundle.content_version
    if version in index["versions"]:
        raise ValueError("不得覆盖已有交付版本")
    registry = bundle.registry()
    if bundle.render_context:
        registry["_render_context"] = bundle.render_context
        registry["render_context_sha256"] = _digest(bundle.render_context)
    used = {_stage(v, entry) for v, entry in index["versions"].items()}
    pending = index.get("pending", {}).get(version)
    if pending is not None:
        stage = _stage(version, pending)
        if pending.get("input_version") != version or stage in used:
            raise ValueError("未完成交付的目录与已登记版本冲突")
    else:
        stage = f"d-{version[:16]}"
        suffix = 0
        while stage in used:
            suffix += 1
            stage = f"d-{version[:16]}-{suffix}"
    registry["storage_stage"] = stage
    for file, item in zip(bundle.files, registry["items"], strict=True):
        if pending and pending.get("files", {}).get(file.name, {}).get("sha256") == file.sha256:
            path = store.path_for(slug, stage, file.name, area="output")
            try:
                if store.read_bytes(path) == file.data:
                    continue
            except FileNotFoundError:
                pass
        store.write(
            slug,
            stage,
            file.name,
            file.data,
            area="output",
            mime_type=item["mime_type"],
            update_manifest=False,
        )
    index["versions"][version] = registry
    index.setdefault("current", {})[bundle.input_version] = version
    index.get("pending", {}).pop(version, None)
    store.write_control_json(INDEX, index)


def current_version(detail: RunDetail, artifact_root: str) -> str | None:
    store, _ = delivery_store(detail, artifact_root)
    index = _index(store)
    source = delivery_fingerprint(detail)
    version = index.get("current", {}).get(source, source)
    if version not in index["versions"]:
        if version != source:
            raise ValueError("当前交付版本不存在")
        return None
    return str(version)


def retry_format(
    detail: RunDetail,
    artifact_root: str,
    quota: int | None,
    version: str,
    target: str,
    request_id: str,
) -> DeliveryBundle:
    from .delivery_render import render_bundle

    store, slug = delivery_store(detail, artifact_root, quota)
    with _lock(store):
        index = _index(store)
        requests = index.setdefault("retry_requests", {})
        duplicate = requests.get(request_id)
        if duplicate is not None:
            if duplicate["base"] != version or duplicate["format"] != target:
                raise DeliveryConflict(
                    "delivery_request_conflict", "同一重试请求不能用于其他版本或格式"
                )
            saved = duplicate["result"]
            return _load(store, slug, saved, index["versions"][saved])
        registry = index["versions"].get(version)
        if registry is None:
            raise FileNotFoundError("交付版本不存在")
        previous = _load(store, slug, version, registry)
        source = delivery_fingerprint(detail)
        if previous.input_version != source:
            raise DeliveryConflict(
                "delivery_source_changed", "研究定稿或渲染版本已变化，请刷新最新交付物"
            )
        if index.get("current", {}).get(source, source) != version:
            raise DeliveryConflict("delivery_version_changed", "交付物已有新版本，请刷新后再重试")
        if not previous.render_context:
            raise DeliveryConflict("delivery_legacy_version", "此历史版本没有可复用的定稿快照")
        if not any(f["format"] == target and f.get("retryable") for f in previous.failures):
            raise DeliveryConflict(
                "delivery_not_retryable", "该格式未生成失败，或需要先修订研究内容"
            )
        bundle = render_bundle(
            previous.render_context,
            previous.files,
            retry_format=target,
            previous_failures=previous.failures,
        )
        bundle.input_version = source
        bundle.parent_version = version
        bundle.content_version = _digest([source, version, target, request_id])
        bundle.attempt = previous.attempt + 1
        bundle.generated_at = datetime.now(UTC).isoformat()
        requests[request_id] = {"base": version, "format": target, "result": bundle.content_version}
        _commit(store, slug, index, bundle)
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
