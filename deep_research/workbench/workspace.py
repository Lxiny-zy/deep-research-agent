"""运行工作区视图：步骤列表 + 按步骤归属的产物文件树 + 交付物，一次请求拿全。

详情页三栏布局（左：步骤 / 中：实时进展与正文 / 右：文件树）的全部结构数据都从
这里来。它是持久化状态的只读投影：

* **steps**：工作流运行时的每一步（状态、耗时、重试次数、错误），外加重规划插入的
  补救记录——用户能看到「第 2 步部分完成 → 插入补救 → 补救完成」的完整链条；
* **files**：运行产物清单（``.framework/manifests/<slug>.json``），区分 ``work``（中间
  产物，默认折叠）与 ``output``（成品），每个文件带所属步骤、大小、哈希；
* **deliverables**：交付登记摘要（只在运行结束后存在）。

文件读取只允许清单里登记过的路径，并按清单记录的大小与哈希复核后再返回，
不接受任何用户拼出来的文件系统路径。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..artifacts import ArtifactError, ArtifactStore
from ..persistence.repository import RunDetail
from .replan import REPLAN_SCRATCH_KEY

MAX_TEXT_PREVIEW = 400_000
_TEXT_MIMES = ("text/", "application/json")


def _scratch(detail: RunDetail) -> dict[str, Any]:
    checkpoint = detail.orchestration.checkpoint if detail.orchestration else None
    scratch = checkpoint.get("scratch") if isinstance(checkpoint, dict) else None
    return scratch if isinstance(scratch, dict) else {}


def artifact_store_for(detail: RunDetail, artifact_root: str) -> tuple[ArtifactStore, str] | None:
    """定位这次运行的产物仓库与 slug；运行没有产物目录时返回 None。"""
    scratch = _scratch(detail)
    slug = scratch.get("_artifact_slug")
    if not isinstance(slug, str) or not slug:
        return None
    root = Path(artifact_root)
    if scratch.get("_artifact_run_scoped"):
        run_dir = scratch.get("_artifact_run_id") or detail.id
        root = root / "runs" / str(run_dir)
    if not root.is_dir():
        return None
    return ArtifactStore(root, max_bytes=None), slug


def _steps(detail: RunDetail) -> list[dict[str, Any]]:
    execution = detail.orchestration
    steps: list[dict[str, Any]] = []
    for index, step in enumerate(execution.steps if execution else []):
        started, finished = step.started_at, step.finished_at
        steps.append(
            {
                "index": index,
                "node_id": step.node_id,
                "label": step.label,
                "kind": step.kind,
                "agent": step.agent,
                "status": getattr(step.status, "value", step.status),
                "attempt": step.attempt,
                "error": step.error,
                "started_at": started.isoformat() if started else None,
                "finished_at": finished.isoformat() if finished else None,
                "elapsed": (finished - started).total_seconds() if started and finished else None,
            }
        )
    return steps


def _files(store: ArtifactStore, slug: str) -> list[dict[str, Any]]:
    try:
        manifest = store.load_manifest(slug)
    except (ArtifactError, OSError, ValueError):
        return []
    files = []
    for record in manifest.files:
        files.append(
            {
                "path": record.path,
                "area": record.area,
                "stage": record.stage,
                "name": record.name,
                "size": record.size_bytes,
                "sha256": record.sha256,
                "mime_type": record.mime_type,
                "step": record.metadata.get("plan_step_id") if record.metadata else None,
                "attempt": record.attempt,
                "created_at": record.created_at.isoformat(),
            }
        )
    files.sort(key=lambda item: (item["area"] != "output", item["path"]))
    return files


def build_workspace(detail: RunDetail, artifact_root: str) -> dict[str, Any]:
    from .delivery_store import workspace_files

    scratch = _scratch(detail)
    located = artifact_store_for(detail, artifact_root)
    files = _files(*located) if located is not None else []
    files.extend(workspace_files(detail, artifact_root))
    replan = scratch.get(REPLAN_SCRATCH_KEY)
    return {
        "run_id": detail.id,
        "status": detail.status,
        "workflow": detail.orchestration.workflow_name if detail.orchestration else None,
        "attempt": detail.orchestration.attempt if detail.orchestration else None,
        "steps": _steps(detail),
        "replans": replan.get("log", []) if isinstance(replan, dict) else [],
        "files": files,
        "slug": located[1] if located is not None else None,
    }


def read_workspace_file(
    detail: RunDetail, artifact_root: str, path: str
) -> tuple[bytes, str, bool]:
    """读取一个清单登记过的产物；返回 (字节, MIME, 是否截断)。"""
    from .delivery_store import read_workspace_file as read_delivery

    delivery = read_delivery(detail, artifact_root, path)
    if delivery is not None:
        data, mime = delivery
        truncated = mime.startswith(_TEXT_MIMES) and len(data) > MAX_TEXT_PREVIEW
        return data[:MAX_TEXT_PREVIEW] if truncated else data, mime, truncated
    located = artifact_store_for(detail, artifact_root)
    if located is None:
        raise FileNotFoundError(path)
    store, slug = located
    record = store.load_manifest(slug).get(path)
    if record is None:
        raise FileNotFoundError(path)
    store.verify(
        path, expected_sha256=record.sha256, expected_size=record.size_bytes, raise_on_error=True
    )
    data = store.read_bytes(path)
    truncated = False
    if record.mime_type.startswith(_TEXT_MIMES) and len(data) > MAX_TEXT_PREVIEW:
        data, truncated = data[:MAX_TEXT_PREVIEW], True
    return data, record.mime_type, truncated


__all__ = ["artifact_store_for", "build_workspace", "read_workspace_file"]
