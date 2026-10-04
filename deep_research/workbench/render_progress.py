"""Checkpoint completed files in an unpublished delivery candidate."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from ..artifacts import ArtifactStore

if TYPE_CHECKING:
    from .publish import DeliveryFile

logger = logging.getLogger(__name__)


class RenderProgressError(RuntimeError):
    """A required format checkpoint cannot be safely saved or restored."""


class RenderProgress:
    def __init__(
        self, store: ArtifactStore, slug: str, version: str, index: dict[str, Any]
    ) -> None:
        from .delivery_store import _stage

        self.store, self.slug, self.version, self.index = store, slug, version, index
        self._active = False
        if not re.fullmatch(r"[0-9a-f]{64}", version) or version in index["versions"]:
            raise RenderProgressError("格式检查点不能覆盖已登记版本")
        pending = index.setdefault("pending", {})
        if not isinstance(pending, dict):
            raise RenderProgressError("未完成交付的检查点登记损坏")
        used = {_stage(key, item) for key, item in index["versions"].items()}
        entry = pending.get(version)
        if entry is not None:
            if (
                not isinstance(entry, dict)
                or entry.get("input_version") != version
                or not isinstance(entry.get("files"), dict)
                or not isinstance(entry.get("state", {}), dict)
            ):
                raise RenderProgressError("交付格式检查点与当前输入不一致")
            try:
                stage = _stage(version, entry)
            except ValueError as exc:
                raise RenderProgressError("交付格式检查点目录无效") from exc
            other_pending = {_stage(key, item) for key, item in pending.items() if key != version}
            if stage in used | other_pending:
                raise RenderProgressError("检查点目录已用于正式交付，拒绝覆盖")
            self.entry = entry
        else:
            reserved = {_stage(key, item) for key, item in pending.items()}
            stage = f"d-{version[:16]}"
            suffix = 0
            while (
                stage in used | reserved
                or store.path_for(slug, stage, "candidate", area="output").parent.exists()
            ):
                suffix += 1
                stage = f"d-{version[:16]}-{suffix}"
            self.entry = {
                "input_version": version,
                "storage_stage": stage,
                "files": {},
                "state": {},
            }
            pending[version] = self.entry
            self._write_index()
        self.stage = stage

    def _ensure_pending(self) -> None:
        if (
            not self._active
            or _CURRENT.get() is not self
            or self.version in self.index["versions"]
            or self.index.get("pending", {}).get(self.version) is not self.entry
        ):
            raise RenderProgressError("格式检查点已结束，拒绝修改已提交文件")

    def _write_index(self) -> None:
        from .delivery_store import INDEX

        try:
            self.store.write_control_json(INDEX, self.index)
        except Exception as exc:
            raise RenderProgressError("交付格式检查点保存失败，已停止继续渲染") from exc

    def flush(self) -> None:
        self._ensure_pending()
        self._write_index()

    def load(self, name: str, fmt: str, title: str, role: str) -> DeliveryFile | None:
        from .completion import file_issues
        from .publish import DeliveryBundle, DeliveryFile

        self._ensure_pending()
        record = self.entry["files"].get(name)
        if not isinstance(record, dict):
            return None
        if any(
            record.get(key) != value
            for key, value in {"name": name, "format": fmt, "title": title, "role": role}.items()
        ):
            return None
        try:
            path = self.store.path_for(self.slug, self.stage, name, area="output")
            data = self.store.read_bytes(path)
        except FileNotFoundError:
            return None
        except Exception as exc:
            raise RenderProgressError("已完成格式的检查点无法读取") from exc
        if len(data) != record.get("size") or hashlib.sha256(data).hexdigest() != record.get(
            "sha256"
        ):
            logger.warning("discarding damaged unpublished format checkpoint: %s", name)
            return None
        file = DeliveryFile(name, fmt, title, role, data)
        if file_issues(DeliveryBundle("", "", [file], [], "pass", "")):
            logger.warning("discarding unreadable unpublished format checkpoint: %s", name)
            return None
        return file

    def save(self, file: DeliveryFile) -> None:
        from .completion import file_issues
        from .publish import DeliveryBundle

        self._ensure_pending()
        # Invalid output remains a renderer failure. It must never become a
        # reusable successful checkpoint, nor stop other formats from rendering.
        if file_issues(DeliveryBundle("", "", [file], [], "pass", "")):
            return
        try:
            self.store.write(
                self.slug, self.stage, file.name, file.data, area="output", update_manifest=False
            )
            self.entry["files"][file.name] = file.record()
            self.flush()
        except RenderProgressError:
            raise
        except Exception as exc:
            raise RenderProgressError("已生成格式无法保存，已停止继续渲染") from exc

    def assets(self, fmt: str, role: str) -> dict[str, DeliveryFile]:
        files = {}
        for name, record in self.entry["files"].items():
            if (
                isinstance(record, dict)
                and record.get("format") == fmt
                and record.get("role") == role
                and isinstance(record.get("title"), str)
            ):
                file = self.load(name, fmt, record["title"], role)
                if file is not None:
                    files[name] = file
        return files

    def load_state(self, name: str) -> dict[str, Any] | None:
        self._ensure_pending()
        record = self.entry.get("state", {}).get(name)
        if record is None:
            return None
        if not isinstance(record, dict) or not isinstance(record.get("value"), dict):
            raise RenderProgressError("交付准备检查点无法解析")
        payload = json.dumps(record["value"], ensure_ascii=False, sort_keys=True).encode()
        if hashlib.sha256(payload).hexdigest() != record.get("sha256"):
            raise RenderProgressError("交付准备检查点校验失败")
        return deepcopy(record["value"])

    def save_state(self, name: str, value: dict[str, Any]) -> None:
        self._ensure_pending()
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
        self.entry.setdefault("state", {})[name] = {
            "value": deepcopy(value),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        self.flush()


_CURRENT: ContextVar[RenderProgress | None] = ContextVar("delivery_render_progress", default=None)


def current_progress() -> RenderProgress | None:
    progress = _CURRENT.get()
    return progress if progress is not None and progress._active else None


@contextmanager
def checkpoint_rendering(
    store: ArtifactStore, slug: str, version: str, index: dict[str, Any]
) -> Iterator[RenderProgress]:
    progress = RenderProgress(store, slug, version, index)
    token = _CURRENT.set(progress)
    progress._active = True
    try:
        yield progress
    finally:
        progress._active = False
        _CURRENT.reset(token)


def render_file(
    name: str,
    fmt: str,
    title: str,
    role: str,
    build: Callable[[], bytes],
    *,
    checkpoint: bool = True,
) -> DeliveryFile:
    from .publish import DeliveryFile

    progress = current_progress() if checkpoint else None
    if progress is not None and (cached := progress.load(name, fmt, title, role)) is not None:
        return cached
    file = DeliveryFile(name, fmt, title, role, build())
    if progress is not None:
        progress.save(file)
    return file
