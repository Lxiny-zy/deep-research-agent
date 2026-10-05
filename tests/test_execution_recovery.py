from __future__ import annotations

import asyncio
import time
from dataclasses import replace

import httpx
import pytest
from openai import AuthenticationError, RateLimitError

from deep_research.agents.base import Blackboard, RunContext
from deep_research.config import Settings
from deep_research.execution import ExecutionContext, RunExecutor
from deep_research.execution_policy import (
    COMMITTED_RESEARCH_PROGRESS_KEY,
    attempt_seconds,
    recovery_checkpoint,
    start_window,
    transient_failure,
)
from deep_research.models import Report
from deep_research.orchestrator import DeepResearchAgent, create_initial_execution
from deep_research.resume_window import remaining_seconds, renewed_checkpoint
from deep_research.workflow import Step, Workflow, WorkflowEngine
from tests.fakes import FakeLLM, FakeSearch
from tests.test_run_queue import repo as repo


def test_deadlines_follow_task_tier_and_explicit_override(monkeypatch):
    monkeypatch.delenv("MAX_RUN_SECONDS", raising=False)
    settings = Settings(run_timeout_profiles={})
    assert attempt_seconds(settings, "research_quick") == 7200
    assert attempt_seconds(settings, "paper_read") == 14400
    assert attempt_seconds(settings, "research") == 43200
    assert attempt_seconds(settings, "qa") == 600
    assert attempt_seconds(replace(settings, run_timeout_profiles={"qa": 120}), "qa") == 120
    assert attempt_seconds(replace(settings, research_tier="deep"), "paper_read") == 43200
    custom = replace(
        settings, run_timeout_profiles={"paper_read:deep": 60000}, research_tier="deep"
    )
    assert attempt_seconds(custom, "paper_read") == 60000
    assert attempt_seconds(replace(custom, max_run_seconds=120), "paper_read") == 120


def test_queue_does_not_consume_window_and_crash_cannot_reset_final_deadline(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("deep_research.execution_policy.time.time", lambda: clock[0])
    settings = Settings(max_run_seconds=30, max_task_seconds=100)
    execution = create_initial_execution("Q", "quick", settings)
    assert "_deadline_at" not in execution.checkpoint["scratch"]
    clock[0] += 600
    start_window(execution, 30, 100)
    scratch = execution.checkpoint["scratch"]
    assert scratch["_deadline_at"] == 1630
    assert scratch["_task_deadline_at"] == 1700
    clock[0] += 10
    start_window(execution, 30, 100)
    assert remaining_seconds(30, 10, scratch) == 20
    updated, reason = recovery_checkpoint(execution, settings, reason="timeout", elapsed=10)
    assert reason == "scheduled" and updated is not None
    clock[0] = 1690
    start_window(updated, 30, 100)
    assert remaining_seconds(30, 10, updated.checkpoint["scratch"]) == 10
    clock[0] = 1701
    assert (
        recovery_checkpoint(updated, settings, reason="timeout", elapsed=20)[1]
        == "task_deadline_exceeded"
    )
    # An explicit user action authorizes a fresh window, beginning at admission.
    renewed = renewed_checkpoint(updated.checkpoint, 30)
    assert "_deadline_at" not in renewed["scratch"]
    assert "_task_deadline_at" not in renewed["scratch"]


def test_no_progress_ignores_tokens_but_counts_saved_subquestions():
    settings = Settings(max_run_seconds=10, max_no_progress_attempts=1)
    execution = create_initial_execution("Q", "quick", settings)
    updated, _ = recovery_checkpoint(execution, settings, reason="timeout", elapsed=10)
    assert updated is not None
    updated.checkpoint["scratch"]["_runtime_metrics"]["total_tokens"] = 1000
    assert recovery_checkpoint(updated, settings, reason="timeout", elapsed=20)[1] == "no_progress"
    updated.checkpoint["scratch"]["attempted_sub_questions"] = {"new evidence": 2}
    assert recovery_checkpoint(updated, settings, reason="timeout", elapsed=20)[1] == "no_progress"
    updated.checkpoint["scratch"][COMMITTED_RESEARCH_PROGRESS_KEY] = {"a" * 64: "b" * 64}
    progressed, reason = recovery_checkpoint(updated, settings, reason="timeout", elapsed=20)
    assert reason == "scheduled" and progressed is not None
    assert progressed.checkpoint["scratch"]["_runtime_metrics"]["total_tokens"] == 1000


def test_transient_classification_does_not_retry_quality_or_credentials():
    request = httpx.Request("GET", "https://example.com")
    assert transient_failure(httpx.ConnectError("temporary"))
    assert transient_failure(
        RateLimitError("wait", response=httpx.Response(429, request=request), body=None)
    )
    assert not transient_failure(
        AuthenticationError("invalid", response=httpx.Response(401, request=request), body=None)
    )
    assert not transient_failure(ValueError("unsupported claim"))


@pytest.mark.parametrize("failure", ["transport", "timeout", "quality", "cancel"])
async def test_executor_recovers_checkpoint_without_rereading_and_preserves_terminal_semantics(
    repo, settings, monkeypatch, failure
):
    settings = replace(settings, orchestration_mode="legacy", max_run_seconds=30)
    calls = []
    definition = Workflow(
        name="recovery-test",
        steps=[Step(agent="read"), Step(agent="write", retry={"max_attempts": 1})],
    )

    class Role:
        def __init__(self, name):
            self.name = name

        async def step(self, bb, ctx):
            calls.append(self.name)
            if self.name == "read":
                bb.scratch["verified_material"] = "original evidence"
                ctx.tracer.add_tokens(10)
            elif calls.count("write") == 1:
                if failure == "quality":
                    raise ValueError("claim unsupported by source")
                if failure == "cancel":
                    await repo.request_cancel(ctx.run_id)
                    raise asyncio.CancelledError
                if failure == "timeout":
                    raise TimeoutError
                raise httpx.ConnectError("temporary network interruption")
            else:
                assert bb.scratch["verified_material"] == "original evidence"
                bb.report = Report(query=bb.query, markdown="Verified report")
            return bb

    class Agent(DeepResearchAgent):
        async def _run_workflow(self, query, run_id):
            source = self._resume_execution or self._initial_execution
            bb = Blackboard.model_validate(source.checkpoint)
            ctx = RunContext(
                llm=FakeLLM(),
                search_tool=FakeSearch(),
                tracer=self.tracer,
                settings=self.settings,
                run_id=run_id,
            )

            async def save(execution):
                await repo.save_orchestration(run_id, execution, lease_owner=self._lease_owner)

            engine = WorkflowEngine(
                ctx,
                resolver=Role,
                checkpoint_sink=save,
                resume_run=self._resume_execution,
                initial_run=self._initial_execution,
                recover_transient=self.managed_recovery,
            )
            await engine.run(definition, bb)
            assert bb.report is not None
            return bb.report

    async def build_agent(self, settings, **kwargs):
        search = FakeSearch()
        return Agent(settings, llm=FakeLLM(), search_tool=search, **kwargs), search

    monkeypatch.setattr(RunExecutor, "build_agent", build_agent)
    execution = create_initial_execution("original input", "quick", settings)
    run_id, _ = await repo.create_run_once(
        "original input", request_hash="", execution=execution, claimable=True
    )
    first = await repo.claim_next_run("first")
    executor = RunExecutor(ExecutionContext(repo=repo))
    await executor.execute(
        run_id, "original input", settings, initial_execution=first.execution, lease_owner="first"
    )
    detail = await repo.get_run(run_id)
    if failure in {"quality", "cancel"}:
        assert detail.status == ("cancelled" if failure == "cancel" else "error")
        assert "_recovery" not in detail.orchestration.checkpoint["scratch"]
        return
    assert detail.status == "running"
    assert calls == ["read", "write"]
    assert not [
        event
        for event in await repo.get_events(run_id)
        if event.stage == "ORCHESTRATOR" and event.type in {"done", "error"}
    ]
    assert await repo.claim_next_run("early") is None
    # Move the already-persisted cooldown into the past; no new execution is created.
    assert await repo.acquire_lease(run_id, "cooldown-test")
    await repo.defer_run(
        run_id, detail.orchestration, lease_owner="cooldown-test", not_before=time.time() - 1
    )
    await repo.release_lease(run_id, "cooldown-test")
    second = await repo.claim_next_run("second")
    assert second.resumed
    await executor.execute(
        run_id, "original input", settings, resume_execution=second.execution, lease_owner="second"
    )
    completed = await repo.get_run(run_id)
    assert completed.status == "done"
    assert calls == ["read", "write", "write"]
    assert completed.total_tokens == 10
    assert completed.orchestration.checkpoint["scratch"]["_recovery"]["count"] == 1
