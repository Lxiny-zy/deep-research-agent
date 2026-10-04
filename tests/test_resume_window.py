from __future__ import annotations

import asyncio
import time

from deep_research import api
from deep_research.execution import ExecutionContext, RunExecutor
from deep_research.orchestrator import DeepResearchAgent, create_initial_execution
from deep_research.resume_window import remaining_seconds, renewed_checkpoint
from deep_research.workbench.templates import AUTO_RESEARCH
from deep_research.worker import Worker
from tests.fakes import FakeSearch
from tests.queue_helpers import drain_inline
from tests.test_scenario_paths import scenario_app as scenario_app
from tests.test_workbench import WorkbenchLLM


def test_explicit_window_renews_time_without_erasing_cumulative_usage(monkeypatch):
    monkeypatch.setattr("deep_research.resume_window.time.time", lambda: 1000)
    old = {
        "scratch": {
            "_deadline_at": 900,
            "_runtime_metrics": {"elapsed": 90, "total_tokens": 1234, "estimated_tokens": 50},
        }
    }
    assert remaining_seconds(30, 90, old["scratch"]) == 0
    updated = renewed_checkpoint(old, 30)
    assert old["scratch"]["_deadline_at"] == 900
    assert "_deadline_at" not in updated["scratch"]
    assert updated["scratch"]["_runtime_metrics"] == old["scratch"]["_runtime_metrics"]
    assert remaining_seconds(30, 92, updated["scratch"]) == 28
    assert remaining_seconds(30, 121, updated["scratch"]) == 0


async def test_http_resume_restarts_expired_window_and_legacy_execution_saves_progress(
    scenario_app, monkeypatch
):
    client, repo = scenario_app
    settings = api.app.state.settings
    settings.max_run_seconds = 30
    execution = create_initial_execution("研究问题", "research_quick", settings)
    scratch = execution.checkpoint["scratch"]
    scratch["_deadline_at"] = time.time() - 5
    scratch["_runtime_metrics"] = {"elapsed": 35, "total_tokens": 1234, "estimated_tokens": 0}
    run_id = await repo.create_run("研究问题", execution=execution)
    await repo.set_status(run_id, "error")
    body = "\n\n".join(f"## {section.title}\n发现X [1]。" for section in AUTO_RESEARCH.sections)

    async def build_agent(self, settings, **kwargs):
        search = FakeSearch()
        return DeepResearchAgent(
            settings, llm=WorkbenchLLM(body), search_tool=search, **kwargs
        ), search

    monkeypatch.setattr(RunExecutor, "build_agent", build_agent)
    resumed = await client.post(f"/api/runs/{run_id}/resume")
    assert resumed.status_code == 202, resumed.text
    if settings.execution_mode == "worker":
        worker = Worker(repo, RunExecutor(ExecutionContext(repo=repo)), settings)
        await worker._tick()
        await asyncio.wait_for(worker._drain(), 15)
        await repo.remove_worker(worker.name)
    else:
        await drain_inline(api.app, timeout=15)
    detail = await repo.get_run(run_id)
    # Execution resumed successfully; this intentionally tiny report does not
    # satisfy the frozen task's length/source requirements and must stay reviewable.
    assert detail.status == "needs_review"
    assert detail.orchestration.checkpoint["scratch"]["_completion"]["issues"]
    assert detail.report is not None and detail.report.markdown
    assert detail.elapsed >= 35 and detail.total_tokens >= 1234
    assert detail.orchestration.checkpoint["scratch"]["_attempt_elapsed_origin"] == 35
    from pathlib import Path

    assert await asyncio.to_thread(
        lambda: list(Path(settings.artifact_root).rglob("research/*.json"))
    )


async def test_automatic_recovery_does_not_renew_an_expired_window(scenario_app, monkeypatch):
    _, repo = scenario_app
    settings = api.app.state.settings
    execution = create_initial_execution("研究问题", "research_quick", settings)
    execution.checkpoint["scratch"]["_deadline_at"] = time.time() - 5
    run_id = await repo.create_run("研究问题", execution=execution)
    calls = []

    class NeverCalled(WorkbenchLLM):
        async def parse(self, *args, **kwargs):
            calls.append("model")
            return await super().parse(*args, **kwargs)

    async def build_agent(self, settings, **kwargs):
        search = FakeSearch()
        return DeepResearchAgent(
            settings, llm=NeverCalled("unused"), search_tool=search, **kwargs
        ), search

    monkeypatch.setattr(RunExecutor, "build_agent", build_agent)
    await RunExecutor(ExecutionContext(repo=repo)).execute(
        run_id, "研究问题", settings, workflow="research_quick", resume_execution=execution
    )
    assert await repo.get_run_status(run_id) == "error" and not calls
