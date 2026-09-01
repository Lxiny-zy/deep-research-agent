"""``execution_mode=worker`` 下 API 的行为：只入队，不执行。

这些断言守的是 W1 的核心不变量——两种拓扑绝不能同时执行同一个 run。
inline 模式的既有行为由 tests/test_api.py 覆盖，此处只验证差异。
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from httpx import ASGITransport

from deep_research import api
from deep_research.config import Settings
from deep_research.orchestration import OrchestrationRuntime
from deep_research.persistence.memory_repository import InMemoryRepository


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=api.app), base_url="http://test")


@pytest.fixture
def worker_repo(monkeypatch) -> InMemoryRepository:
    repo = InMemoryRepository()
    api.app.state.settings = Settings(execution_mode="worker")
    api.app.state.repo = repo
    api.app.state.live = {}
    api.app.state.tasks = set()
    api.app.state.run_tasks = {}
    api.app.state.cancellation_requested = set()
    api.app.state.run_admission = api.RunAdmission(8, 32)
    api.app.state.config_lock = asyncio.Lock()

    async def _must_not_execute(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("worker 模式下 API 进程不得执行研究任务")

    monkeypatch.setattr(api, "_execute", _must_not_execute)
    return repo


@pytest.mark.asyncio
async def test_create_run_enqueues_instead_of_executing(worker_repo) -> None:
    async with _client() as client:
        response = await client.post("/api/runs", json={"query": "worker mode"})

    assert response.status_code == 202
    run_id = response.json()["run_id"]
    # 没有本地执行痕迹：没有 EventHub，也没有派发出去的任务。
    assert run_id not in api.app.state.live
    assert api.app.state.run_tasks == {}
    # 但队列里确实有一条可领取的任务，且带完整的工作流定义。
    claimed = await worker_repo.claim_next_run("worker-1")
    assert claimed is not None
    assert claimed.run_id == run_id
    assert claimed.execution.definition


@pytest.mark.asyncio
async def test_create_run_persists_explicit_workflow_for_worker(worker_repo) -> None:
    async with _client() as client:
        response = await client.post(
            "/api/runs",
            json={"query": "worker explicit workflow", "workflow": "quick"},
        )

    assert response.status_code == 202
    run_id = response.json()["run_id"]
    claimed = await worker_repo.claim_next_run("worker-1")
    assert claimed is not None
    assert claimed.run_id == run_id
    assert claimed.execution.checkpoint["scratch"]["requested_workflow"] == "quick"


@pytest.mark.asyncio
async def test_enqueued_run_holds_no_lease(worker_repo) -> None:
    """API 若提前占住租约，worker 要等到租约过期才能领走——等于凭空延迟。"""
    async with _client() as client:
        response = await client.post("/api/runs", json={"query": "no lease"})
    run_id = response.json()["run_id"]

    assert worker_repo._runs[run_id].lease_owner is None
    assert await worker_repo.claim_next_run("worker-1") is not None


@pytest.mark.asyncio
async def test_enqueue_does_not_consume_admission_capacity(worker_repo) -> None:
    """准入名额限制的是本进程的执行并发；入队不执行，就不该占名额。"""
    api.app.state.run_admission = api.RunAdmission(1, 0)

    async with _client() as client:
        first = await client.post("/api/runs", json={"query": "first"})
        second = await client.post("/api/runs", json={"query": "second"})
        third = await client.post("/api/runs", json={"query": "third"})

    assert [first.status_code, second.status_code, third.status_code] == [202, 202, 202]
    assert api.app.state.run_admission.active == 0


@pytest.mark.asyncio
async def test_idempotency_still_applies_when_enqueuing(worker_repo) -> None:
    headers = {"Idempotency-Key": "worker-key"}
    async with _client() as client:
        first = await client.post("/api/runs", json={"query": "Q"}, headers=headers)
        replay = await client.post("/api/runs", json={"query": "Q"}, headers=headers)
        conflict = await client.post("/api/runs", json={"query": "other"}, headers=headers)

    assert first.json()["run_id"] == replay.json()["run_id"]
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert conflict.status_code == 409
    # 重放不得把同一个 run 入队两次。
    assert await worker_repo.claim_next_run("worker-1") is not None
    assert await worker_repo.claim_next_run("worker-2") is None


@pytest.mark.asyncio
async def test_resume_hands_the_run_back_to_the_queue(worker_repo) -> None:
    runtime = OrchestrationRuntime()
    execution = runtime.start("deep", {"query": "resume me"})
    runtime.save_checkpoint({"query": "resume me", "scratch": {}}, {"name": "deep", "steps": []})
    run_id = await worker_repo.create_run("resume me", execution=execution)
    await worker_repo.set_status(run_id, "running")

    async with _client() as client:
        response = await client.post(f"/api/runs/{run_id}/resume")

    assert response.status_code == 202
    assert run_id not in api.app.state.live
    claimed = await worker_repo.claim_next_run("worker-1")
    assert claimed is not None
    assert claimed.run_id == run_id
    assert claimed.resumed is True


@pytest.mark.asyncio
async def test_resume_requeues_a_failed_checkpointed_run(worker_repo) -> None:
    runtime = OrchestrationRuntime()
    execution = runtime.start("deep", {"query": "resume failed"})
    runtime.save_checkpoint(
        {"query": "resume failed", "scratch": {}},
        {"name": "deep", "steps": []},
    )
    run_id = await worker_repo.create_run("resume failed", execution=execution)
    await worker_repo.set_status(run_id, "error")

    async with _client() as client:
        first, concurrent = await asyncio.gather(
            client.post(f"/api/runs/{run_id}/resume"),
            client.post(f"/api/runs/{run_id}/resume"),
        )

    assert sorted([first.status_code, concurrent.status_code]) == [202, 409]
    assert await worker_repo.get_run_status(run_id) == "running"
    claimed = await worker_repo.claim_next_run("worker-1")
    assert claimed is not None
    assert claimed.run_id == run_id
    assert claimed.resumed is True


@pytest.mark.asyncio
async def test_resume_rejects_a_failed_run_with_an_active_worker_lease(worker_repo) -> None:
    runtime = OrchestrationRuntime()
    execution = runtime.start("deep", {"query": "still owned"})
    runtime.save_checkpoint(
        {"query": "still owned", "scratch": {}},
        {"name": "deep", "steps": []},
    )
    run_id = await worker_repo.create_run("still owned", execution=execution)
    await worker_repo.set_status(run_id, "error")
    assert await worker_repo.acquire_lease(run_id, "active-worker", seconds=120)

    async with _client() as client:
        response = await client.post(f"/api/runs/{run_id}/resume")

    assert response.status_code == 409
    assert await worker_repo.get_run_status(run_id) == "error"


@pytest.mark.asyncio
async def test_cancel_is_durable_without_a_local_task(worker_repo) -> None:
    """取消跨进程生效：API 只写状态，执行中的 worker 轮询到后自行收尾。"""
    runtime = OrchestrationRuntime()
    execution = runtime.start("deep", {"query": "cancel"})
    run_id = await worker_repo.create_run("cancel", execution=execution)
    await worker_repo.set_status(run_id, "running")

    async with _client() as client:
        response = await client.post(f"/api/runs/{run_id}/cancel")

    assert response.status_code == 202
    assert response.json()["status"] == "cancelling"
    assert await worker_repo.get_run_status(run_id) == "cancelling"


@pytest.mark.asyncio
async def test_recovery_scan_leaves_orphans_to_workers(worker_repo) -> None:
    """两种拓扑不能同时执行：worker 模式下 API 的恢复扫描不得接管孤儿任务。"""
    runtime = OrchestrationRuntime()
    execution = runtime.start("deep", {"query": "orphan"})
    runtime.save_checkpoint({"query": "orphan", "scratch": {}}, {"name": "deep", "steps": []})
    run_id = await worker_repo.create_run("orphan", execution=execution)
    await worker_repo.set_status(run_id, "running")

    await api._recover_orphaned_runs(api.app, api.app.state.settings)

    assert api.app.state.run_tasks == {}
    assert run_id not in api.app.state.live
    assert await worker_repo.get_run_status(run_id) == "running"


@pytest.mark.asyncio
async def test_recovery_scan_still_settles_cancelling_runs(worker_repo) -> None:
    """取消结算不需要执行能力，因此在 worker 模式下仍归 API。"""
    runtime = OrchestrationRuntime()
    execution = runtime.start("deep", {"query": "abandoned cancel"})
    runtime.save_checkpoint({"query": "abandoned cancel", "scratch": {}}, {"name": "deep"})
    run_id = await worker_repo.create_run("abandoned cancel", execution=execution)
    await worker_repo.set_status(run_id, "running")
    assert await worker_repo.request_cancel(run_id) == "cancelling"

    await api._recover_orphaned_runs(api.app, api.app.state.settings)

    assert await worker_repo.get_run_status(run_id) == "cancelled"
