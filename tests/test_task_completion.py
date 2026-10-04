from __future__ import annotations

import asyncio
import threading
from dataclasses import replace

import pytest

from deep_research.execution import ExecutionContext, RunExecutor
from deep_research.models import Report
from deep_research.observability import EventHub
from deep_research.orchestrator import DeepResearchAgent, create_initial_execution
from deep_research.persistence.db import create_all, make_engine, make_sessionmaker
from deep_research.persistence.memory_repository import InMemoryRepository
from deep_research.persistence.repository import RunDetail
from deep_research.persistence.sql_repository import SqlRepository
from deep_research.workbench import completion
from deep_research.workbench.contract import TaskContract
from deep_research.workbench.delivery_store import build_or_load, current_version, load_version
from deep_research.workbench.gates import GateResult
from deep_research.workbench.publish import DeliveryBundle, DeliveryFile, delivery_fingerprint
from tests.fakes import FakeLLM, FakeSearch


@pytest.fixture(params=["memory", "sqlite"])
async def repo(request, tmp_path):
    if request.param == "memory":
        yield InMemoryRepository()
        return
    # Cancelling an aiosqlite operation can invalidate its pooled connection.
    # A file DB models deployment correctly: reconnecting must retain the schema.
    engine = make_engine(f"sqlite+aiosqlite:///{(tmp_path / 'completion.db').as_posix()}")
    await create_all(engine)
    try:
        yield SqlRepository(make_sessionmaker(engine))
    finally:
        await engine.dispose()


def execution(settings, formats=None):
    result = create_initial_execution("Q", "quick", settings)
    result.checkpoint["scratch"]["task_contract"] = TaskContract(
        title="Q", template="autoResearch", original_request="Q", deliverables=formats or ["md"]
    ).model_dump(mode="json")
    return result


def bundle(detail, *, status="pass", files=None, gates=None):
    return DeliveryBundle(
        "autoResearch",
        "Q",
        files
        if files is not None
        else [DeliveryFile("q.md", "md", "Q", "source", b"Verified body")],
        gates
        or [
            *[
                GateResult(name, "pass")
                for name in ("markdown", "structure", "length", "consistency")
            ],
            GateResult(
                "prose_evidence", status, [] if status == "pass" else ["Evidence needs review"]
            ),
        ],
        status,
        "2026-10-04T00:00:00Z",
        input_version=delivery_fingerprint(detail),
        content_version=delivery_fingerprint(detail),
    )


@pytest.mark.parametrize(
    "kind", ["warning", "missing", "corrupt_png", "corrupt_xlsx", "wrong_snapshot", "missing_proof"]
)
def test_completion_cannot_trust_pass_label_without_required_evidence(settings, kind):
    formats = (
        ["md", "png" if kind == "corrupt_png" else "xlsx"] if kind.startswith("corrupt") else ["md"]
    )
    detail = RunDetail(
        id="unit",
        query="Q",
        status="running",
        orchestration=execution(settings, formats),
        report=Report(query="Q", markdown="Body"),
    )
    value = bundle(detail, status="warn" if kind == "warning" else "pass")
    completion.validate_bundle_files(value)
    if kind == "warning":
        value.status = "pass"  # Aggregate labels cannot override a gate's warning.
    if kind == "missing":
        value.files = []
    elif kind.startswith("corrupt"):
        fmt = formats[-1]
        value.files.append(
            DeliveryFile("bad." + fmt, fmt, "broken", "figure", b"not a real document")
        )
    elif kind == "wrong_snapshot":
        value.input_version = "0" * 64
    elif kind == "missing_proof":
        value.gates = [gate for gate in value.gates if gate.name != "prose_evidence"]
    record = completion.assess_completion(detail, value)
    assert record["status"] == "needs_review" and record["issues"]
    expected = {
        "warning": "Evidence needs review",
        "missing": "缺少承诺",
        "corrupt_png": "bad.png",
        "corrupt_xlsx": "bad.xlsx",
        "wrong_snapshot": "版本不一致",
        "missing_proof": "prose_evidence",
    }[kind]
    assert any(expected in issue for issue in record["issues"])


@pytest.mark.parametrize("outcome", ["pass", "warn", "cancel"])
async def test_terminal_state_waits_for_durable_delivery_and_survives_restart(
    repo, settings, monkeypatch, outcome
):
    settings = replace(settings, orchestration_mode="legacy", max_run_seconds=30)
    initial = execution(settings)
    run_id = await repo.create_run("Q", execution=initial, lease_owner="owner")
    entered, release = threading.Event(), threading.Event()
    builds = []

    def render(detail):
        builds.append(detail.id)
        entered.set()
        assert release.wait(5)
        return bundle(detail, status="warn" if outcome == "warn" else "pass")

    class Agent(DeepResearchAgent):
        async def _run_workflow(self, query, run_id):
            result = Report(query=query, markdown="Verified body")
            await repo.replace_artifacts(
                run_id,
                plan=None,
                reflection_rounds=[],
                results=[],
                report=result,
                lease_owner=self._lease_owner,
            )
            return result

    async def build_agent(self, settings, **kwargs):
        search = FakeSearch()
        return Agent(settings, llm=FakeLLM(), search_tool=search, **kwargs), search

    monkeypatch.setattr(completion, "build_bundle", render)
    monkeypatch.setattr(RunExecutor, "build_agent", build_agent)
    hub = EventHub()
    executor = RunExecutor(ExecutionContext(repo=repo, live={run_id: hub}))
    task = asyncio.create_task(
        executor.execute(run_id, "Q", settings, initial_execution=initial, lease_owner="owner")
    )
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        assert await repo.get_run_status(run_id) == "running"
        assert not any(
            event.type in {"done", "needs_review"} for event in await repo.get_events(run_id)
        )
        if outcome == "cancel":
            assert await repo.request_cancel(run_id) == "cancelling"
    finally:
        release.set()
    await asyncio.wait_for(task, 10)
    detail = await repo.get_run(run_id)
    expected = {"pass": "done", "warn": "needs_review", "cancel": "cancelled"}[outcome]
    assert detail.status == expected
    events = [event async for event in hub.stream()]
    assert events[-1].type == expected
    if outcome == "cancel":
        assert not any(event.type in {"done", "needs_review"} for event in events)
        return
    record = detail.orchestration.checkpoint["scratch"]["_completion"]
    assert record["status"] == expected
    assert current_version(detail, settings.artifact_root) == record["content_version"]
    # A fresh request/process gets the same immutable bytes, not a second render.
    loaded = build_or_load(detail, settings.artifact_root, settings.artifact_total_bytes, render)
    assert loaded.files[0].data == b"Verified body" and len(builds) == 1
    assert (
        load_version(detail, settings.artifact_root, record["content_version"]).input_version
        == record["input_version"]
    )


async def test_file_commit_before_database_failure_is_reconciled_without_rendering(
    repo, settings, monkeypatch
):
    from deep_research.workbench.delivery_store import _commit, _index, _lock, delivery_store

    initial = execution(settings)
    run_id = await repo.create_run("Q", execution=initial)
    await repo.save_report(run_id, Report(query="Q", markdown="Verified body"))
    detail = await repo.get_run(run_id)
    first = build_or_load(
        detail,
        settings.artifact_root,
        settings.artifact_total_bytes,
        lambda d: bundle(d, status="warn"),
    )
    await repo.finalize(
        run_id, elapsed=12, total_tokens=20, completion=completion.assess_completion(detail, first)
    )
    detail = await repo.get_run(run_id)
    repaired = bundle(detail)
    repaired.content_version = "f" * 64
    repaired.parent_version = first.content_version
    store, slug = delivery_store(detail, settings.artifact_root)
    with _lock(store):
        _commit(store, slug, _index(store), repaired)
    # Files committed, DB still points at the failed version (the crash boundary).
    assert await repo.get_run_status(run_id) == "needs_review"
    assert await completion.synchronize_completion(repo, run_id, settings)
    final = await repo.get_run(run_id)
    assert final.status == "done"
    assert (
        final.orchestration.checkpoint["scratch"]["_completion"]["content_version"]
        == repaired.content_version
    )
    assert final.total_tokens == 20 and final.elapsed == 12
    assert await completion.synchronize_completion(repo, run_id, settings)
