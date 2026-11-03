"""Offline scheduling review probes for commit ed97c27 (2026-10-08).

Run from the repository with a Python 3.11+ environment containing dev dependencies:
    python docs/validation/resource_scheduling_probe_20261008.py

Prints eight JSON records. Uses temporary SQLite data, in-memory repositories,
fake model/search clients and a recording renderer. No live services or real
rendering are invoked. Timing and SQL polling counts are machine-dependent;
the records describe current behavior and are not pass/fail regression tests.
"""

import asyncio
import json
import logging
import tempfile
import time
from collections import Counter
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import event
from deep_research.config import Settings
from deep_research.execution import ExecutionContext, RunExecutor
from deep_research.execution_policy import transient_failure
from deep_research.llm import _retryable
from deep_research.orchestration import OrchestrationRuntime
from deep_research.persistence.db import create_all, make_engine, make_sessionmaker
from deep_research.persistence.sql_repository import SqlRepository
from deep_research.persistence.memory_repository import InMemoryRepository
from deep_research.persistence.repository import RunQueueFullError
from deep_research.persistence.orm import WorkflowRunRow
from deep_research.provider_limits import ProviderBusyError, ProviderCoordinator, fingerprint
from deep_research.worker import Worker
from deep_research.agents.base import Blackboard, RunContext
from deep_research.observability import Tracer
from deep_research.workflow import Workflow, WorkflowEngine
from tests.fakes import FakeLLM, FakeSearch

logging.disable(logging.CRITICAL)

async def enqueue(repo, owner, *, workflow="deep", limit=None, payload=0):
    execution = OrchestrationRuntime().start(workflow, {"query": "synthetic audit"})
    execution.checkpoint = {"query": "synthetic audit", "scratch": {"audit_payload": "x" * payload}}
    run_id, _ = await repo.create_run_once(
        "synthetic audit", request_hash="", owner_id=owner, execution=execution,
        claimable=True, max_inflight=limit,
    )
    return run_id

async def admission_and_hydration(repo):
    for _ in range(40):
        await enqueue(repo, "owner-a", limit=40, payload=128 * 1024)
    claims = []
    for i in range(8):
        claim = await repo.claim_next_run(f"audit-{i}", max_active_runs=8)
        if claim is None:
            break
        claims.append(claim)
    print(json.dumps({"probe": "single_owner_capacity", "global_limit": 8,
                      "claimed": len(claims), "queued": 40-len(claims),
                      "idle_slots": 8-len(claims)}))
    refused = False
    try:
        await enqueue(repo, "owner-b", workflow="quick", limit=40)
    except RunQueueFullError:
        refused = True
    print(json.dumps({"probe": "new_owner_light_admission", "queue_limit_including_active": 40,
                      "new_owner_refused": refused, "idle_slots": 8-len(claims)}))
    loaded = []
    def on_load(obj, _):
        loaded.append(len(obj.checkpoint.get("scratch", {}).get("audit_payload", "")))
    event.listen(WorkflowRunRow, "load", on_load)
    try:
        started = time.perf_counter()
        rejected = await repo.claim_next_run("audit-hydration", max_active_runs=8)
        elapsed = time.perf_counter()-started
    finally:
        event.remove(WorkflowRunRow, "load", on_load)
    print(json.dumps({"probe": "blocked_claim_hydration", "claimed": rejected is not None,
                      "workflow_rows_loaded": len(loaded),
                      "payload_bytes_loaded": sum(loaded), "elapsed_ms": round(elapsed*1000, 2)}))

async def worker_distribution():
    repo = InMemoryRepository()
    for i in range(8):
        await enqueue(repo, f"identity-{i}", workflow="quick")
    release = asyncio.Event()
    async def hold(run_id, query, settings, **kwargs):
        await release.wait()
        await repo.set_status(run_id, "done", lease_owner=kwargs["lease_owner"])
        await repo.release_lease(run_id, kwargs["lease_owner"])
    executor = RunExecutor(ExecutionContext(repo=repo))
    settings = Settings(max_active_runs=8)
    first = Worker(repo, executor, settings, name="warm-worker", execute=hold)
    second = Worker(repo, executor, settings, name="late-worker", execute=hold)
    try:
        for _ in range(8):
            await first._tick()
        await second._tick()
        print(json.dumps({"probe": "worker_distribution", "global_limit": 8,
                          "warm_worker_active": len(first._running),
                          "late_worker_active": len(second._running),
                          "late_worker_local_free": second.capacity}))
    finally:
        tasks = set(first._running) | set(second._running)
        release.set()
        await asyncio.gather(*tasks)

async def provider_contention(engine, sessions):
    coord = ProviderCoordinator(sessions, concurrency=1, wait_seconds=0.25)
    key = fingerprint("https://audit.invalid", "synthetic-not-a-real-key")
    held = await coord.acquire(key)
    statements = Counter()
    def before(conn, cursor, statement, parameters, context, executemany):
        statements[statement.split()[0].upper()] += 1
    event.listen(engine.sync_engine, "before_cursor_execute", before)
    try:
        started = time.perf_counter()
        results = await asyncio.gather(*(coord.acquire(key) for _ in range(8)), return_exceptions=True)
        elapsed = time.perf_counter()-started
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", before)
        await coord.release(held)
    busy = [r for r in results if isinstance(r, ProviderBusyError)]
    print(json.dumps({"probe": "provider_contention", "waiting_calls": 8,
                      "busy_errors": len(busy), "sql_statements": sum(statements.values()),
                      "statement_types": dict(statements), "elapsed_ms": round(elapsed*1000, 2),
                      "classified_transient": [transient_failure(e) for e in busy],
                      "llm_retryable": [bool(_retryable(e)) for e in busy]}))

async def graph_barrier():
    fast_done = asyncio.Event()
    slow_started = asyncio.Event()
    release_slow = asyncio.Event()
    successor_started = asyncio.Event()
    observed = []
    class Role:
        def __init__(self, name):
            self.name = name
        async def step(self, bb, ctx):
            observed.append(self.name + ":start")
            if self.name == "fast":
                fast_done.set()
            elif self.name == "slow":
                slow_started.set()
                await release_slow.wait()
            elif self.name == "successor":
                successor_started.set()
            observed.append(self.name + ":end")
            return bb
    nodes = [{"id": n, "step": {"kind": "agent", "agent": n}}
             for n in ("root", "fast", "slow", "successor")]
    edges = [{"id": a+"-"+b, "source": a, "target": b}
             for a,b in (("root","fast"),("root","slow"),("fast","successor"))]
    wf = Workflow(name="audit-graph", nodes=nodes, edges=edges)
    ctx = RunContext(llm=FakeLLM(), search_tool=FakeSearch(), tracer=Tracer(),
                     settings=Settings(intent_enabled=False))
    engine = WorkflowEngine(ctx, resolver=Role)
    task = asyncio.create_task(engine.run(wf, Blackboard(query="synthetic audit")))
    try:
        await asyncio.wait_for(asyncio.gather(fast_done.wait(), slow_started.wait()), timeout=2)
        await asyncio.sleep(0.03)
        before_release = successor_started.is_set()
    finally:
        release_slow.set()
        await asyncio.wait_for(task, timeout=2)
    print(json.dumps({"probe": "graph_dependency_ready", "successor_depends_only_on": "fast",
                      "fast_finished": fast_done.is_set(),
                      "successor_started_before_unrelated_slow_finished": before_release,
                      "events": observed}))

async def executor_busy_terminal(root):
    repo = InMemoryRepository()
    settings = Settings(artifact_root=str(root / "artifacts"), orchestration_mode="legacy",
                        max_run_seconds=30, intent_enabled=False)
    from deep_research.orchestrator import create_initial_execution
    execution = create_initial_execution("synthetic audit", "quick", settings)
    run_id, _ = await repo.create_run_once("synthetic audit", request_hash="",
                                           execution=execution, claimable=True)
    claim = await repo.claim_next_run("busy-probe")
    class BusyAgent:
        def __init__(self):
            self.tracer = Tracer()
        async def run(self, query):
            raise ProviderBusyError(30)
        async def aclose(self):
            pass
    class Executor(RunExecutor):
        async def build_agent(self, settings, **kwargs):
            return BusyAgent(), FakeSearch()
    executor = Executor(ExecutionContext(repo=repo))
    await executor.execute(run_id, "synthetic audit", settings,
                           initial_execution=claim.execution, lease_owner=claim.lease_owner)
    detail = await repo.get_run(run_id)
    print(json.dumps({"probe": "busy_error_executor_result", "run_status": detail.status,
                      "auto_recovery_present": "_recovery" in detail.orchestration.checkpoint.get("scratch", {})}))


async def worker_mode_render_consumer(root):
    from deep_research.render_service import RenderService
    from deep_research.render_queue import MemoryRenderQueue

    repo = InMemoryRepository()
    run_id = await repo.create_run("synthetic render audit")
    started = asyncio.Event()

    class RecordingService(RenderService):
        async def _execute(self, job):
            started.set()
            await self.queue.finish(job.id, job.lease_owner, {"audit": True})
            self._notify()

    settings = Settings(execution_mode="worker", artifact_root=str(root / "render-artifacts"))
    service = RecordingService(repo, settings, queue=MemoryRenderQueue())
    try:
        await service.reserve("export", run_id, "synthetic-render-probe", {})
        await asyncio.wait_for(started.wait(), timeout=2)
        print(json.dumps({"probe": "worker_mode_render_consumer",
                          "configured_mode": settings.execution_mode,
                          "local_consumer_started": started.is_set(),
                          "real_render_executed": False}))
    finally:
        await service.close()

async def main():
    with tempfile.TemporaryDirectory(prefix="deep-research-audit-") as temporary:
        root = Path(temporary)
        engine = make_engine("sqlite+aiosqlite:///" + (root / "audit.sqlite").as_posix())
        await create_all(engine)
        sessions = make_sessionmaker(engine)
        try:
            await admission_and_hydration(SqlRepository(sessions))
            await provider_contention(engine, sessions)
            await worker_distribution()
            await graph_barrier()
            await executor_busy_terminal(root)
            await worker_mode_render_consumer(root)
        finally:
            await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())

