"""Public configuration changes reach real task execution through the catalog."""

from __future__ import annotations

import asyncio
import json

from deep_research import api
from deep_research.catalog import search as catalog_search
from deep_research.catalog.repository import CatalogRepository
from deep_research.execution import ExecutionContext, RunExecutor
from deep_research.llm import LLM
from deep_research.workbench.templates import AUTO_RESEARCH
from deep_research.worker import Worker
from tests.fakes import FakeSearch
from tests.queue_helpers import drain_inline
from tests.test_scenario_paths import scenario_app as scenario_app
from tests.test_workbench import WorkbenchLLM


async def test_model_role_search_and_global_configuration_reach_a_complete_task(
    scenario_app, monkeypatch
):
    client, repo = scenario_app
    catalog = CatalogRepository(repo._sm)
    monkeypatch.setattr(api.app.state, "catalog", catalog)
    body = "\n\n".join(f"## {s.title}\n发现X [1]。" for s in AUTO_RESEARCH.sections)
    profiles = []
    calls = []
    searches = []

    class ProfileLLM(WorkbenchLLM):
        def __init__(self, profile):
            super().__init__(body)
            self.model = profile["model"]

        async def parse(self, system, user, schema, **kwargs):
            calls.append((self.model, str(system)))
            return await super().parse(system, user, schema, **kwargs)

        async def stream(self, system, user, **kwargs):
            calls.append((self.model, str(system)))
            yield body

        async def aclose(self):
            pass

    def model_factory(tracer, **profile):
        profiles.append({k: v for k, v in profile.items() if k != "api_key"})
        return ProfileLLM(profile)

    async def search_factory(profile, catalog, settings, **kwargs):
        searches.append((profile.id, settings.max_concurrency, settings.request_timeout))
        return FakeSearch()

    monkeypatch.setattr(LLM, "from_params", staticmethod(model_factory))
    monkeypatch.setattr(catalog_search, "build_profile_tool", search_factory)
    default = await client.post(
        "/api/models",
        json={
            "name": "task-default",
            "model": "default-model",
            "is_default": True,
            "api_key": "test-default-credential",
            "context_window_tokens": 200000,
            "max_output_tokens": 16000,
        },
    )
    researcher = await client.post(
        "/api/models",
        json={
            "name": "task-researcher",
            "model": "research-model",
            "temperature": 0.25,
            "api_key": "test-research-credential",
            "context_window_tokens": 300000,
            "max_output_tokens": 24000,
        },
    )
    assert default.status_code == researcher.status_code == 201
    search = await client.post(
        "/api/search-profiles",
        json={
            "name": "task-search",
            "provider": "openalex",
        },
    )
    assert search.status_code == 201, search.text
    search_id = search.json()["id"]
    card = await client.post(
        "/api/agents",
        json={
            "name": "researcher",
            "behavior": "research",
            "system_prompt": "TASK_PROFILE_MARKER",
            "model_profile_id": researcher.json()["id"],
            "search_profile_ids": [search_id],
        },
    )
    assert card.status_code == 201, card.text
    configured = await client.put(
        "/api/config",
        json={
            "max_concurrency": 3,
            "request_timeout": 47,
            "search_profile_ids": [search_id],
        },
    )
    assert configured.status_code == 200, configured.text
    created = await client.post(
        "/api/runs",
        json={
            "query": "分析材料中的方法与结果",
            "template": "autoResearch",
            "strategy": "quick",
            "clarified": True,
            "params": {"max_concurrency": 3},
        },
    )
    assert created.status_code == 202, created.text
    run_id = created.json()["run_id"]
    if api.app.state.settings.execution_mode == "worker":
        # Later edits apply to new tasks; queued work retains its captured semantics.
        changed = await client.put(
            f"/api/models/{researcher.json()['id']}",
            json={
                "model": "later-model",
                "context_window_tokens": 500000,
            },
        )
        assert changed.status_code == 200
        changed = await client.put(
            f"/api/agents/{card.json()['id']}",
            json={
                "system_prompt": "LATER_PROFILE_MARKER",
            },
        )
        assert changed.status_code == 200
        changed = await client.put(
            "/api/config", json={"max_concurrency": 4, "request_timeout": 61}
        )
        assert changed.status_code == 200
        worker = Worker(
            repo, RunExecutor(ExecutionContext(repo=repo, catalog=catalog)), api.app.state.settings
        )
        await worker._tick()
        assert len(worker._running) == 1
        await asyncio.wait_for(worker._drain(), 45)
        await repo.remove_worker(worker.name)
    else:
        await drain_inline(api.app, seconds=45)
    detail = await repo.get_run(run_id)
    assert detail is not None
    assert any(
        model == "research-model" and "TASK_PROFILE_MARKER" in prompt for model, prompt in calls
    )
    assert not any("LATER_PROFILE_MARKER" in prompt for _, prompt in calls)
    assert {p["model"] for p in profiles} == {"default-model", "research-model"}
    role = next(p for p in profiles if p["model"] == "research-model")
    assert role["context_window_tokens"] == 300000 and role["max_output_tokens"] == 24000
    assert role["temperature"] == 0.25 and role["timeout"] == 47
    assert searches and all(item == (search_id, 3, 47) for item in searches)
    assert "test-default-credential" not in json.dumps(detail.orchestration.checkpoint)
    registry = await client.get(f"/api/runs/{run_id}/deliverables")
    assert registry.status_code == 200, registry.text
    record = registry.json()
    required = {"md", "pdf", "html", "docx"}
    assert required <= {item["format"] for item in record["items"]}
    public = await client.get(f"/api/runs/{run_id}")
    assert public.status_code == 200 and "test-research-credential" not in public.text
    detail = await repo.get_run(run_id)
    completion = detail.orchestration.checkpoint["scratch"].get("_completion")
    assert isinstance(completion, dict), detail.status
    nonpassing = [gate for gate in record["gates"] if gate["status"] != "pass"]
    requires_review = (
        bool(nonpassing or record["failures"])
        or record["status"] != "pass"
        or any(
            item["status"] != "pass" or item["size"] <= 0
            for item in record["items"]
            if item["format"] in required
        )
    )
    expected = "needs_review" if requires_review else "done"
    # The one available source satisfies the attainable citation minimum.
    # Successful routing still does not make the short prose pass its length gate.
    assert "length" in {gate["name"] for gate in nonpassing}
    citation = next(gate for gate in record["gates"] if gate["name"] == "citation")
    assert citation["status"] == "pass"
    assert citation["metrics"]["required"] == citation["metrics"]["available"] == 1
    assert citation["metrics"]["retrieval_shortfall"] > 0
    assert expected == "needs_review"
    assert public.json()["status"] == detail.status == completion["status"] == expected
    assert public.json()["completion"] == completion
    assert completion["required_formats"] == sorted(required)
    assert completion["content_version"] == record["content_version"]
    assert completion["input_version"] == record["input_version"]
    assert completion["gates"] == record["gates"]
    assert bool(completion["issues"]) is requires_review
    for gate in nonpassing:
        assert set(gate["issues"]) <= set(completion["issues"])
