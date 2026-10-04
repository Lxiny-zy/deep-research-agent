"""Library HTTP imports reach actual task and Q&A execution, with fixed providers."""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager

import pytest

from deep_research import api
from deep_research.execution import ExecutionContext, RunExecutor
from deep_research.library.repository import SqlLibraryRepository
from deep_research.models import ExtractedFindingList, ResearchPlan, SubQuestion
from deep_research.orchestrator import DeepResearchAgent
from deep_research.workbench.templates import get_template
from deep_research.workbench.writers import Mindmap
from deep_research.worker import Worker
from tests.fakes import FakeSearch, verified_finding
from tests.queue_helpers import drain_inline
from tests.test_scenario_paths import scenario_app as scenario_app
from tests.test_workbench import WorkbenchLLM


async def finish_run(repo):
    if api.app.state.settings.execution_mode == "worker":
        worker = Worker(
            repo,
            RunExecutor(ExecutionContext(repo=repo, library=api.app.state.library)),
            api.app.state.settings,
        )
        await worker._tick()
        assert len(worker._running) == 1
        await asyncio.wait_for(worker._drain(), 45)
        await repo.remove_worker(worker.name)
    else:
        await drain_inline(api.app, seconds=45)


@pytest.mark.parametrize("key", ["autoResearch", "litReview", "slides", "mindmap"])
async def test_library_import_research_followup_and_qa_share_selected_corpus(
    scenario_app, monkeypatch, key
):
    client, repo = scenario_app
    monkeypatch.setattr(api.app.state, "library", SqlLibraryRepository(repo._sm))
    project = (await client.post("/api/projects", json={"name": "Calibration evidence"})).json()
    project_id = project["id"]
    corpus = (await client.get(f"/api/projects/{project_id}/corpora")).json()[0]
    quote = "The quartz calibration uses a reference."
    selected = await client.post(
        f"/api/projects/{project_id}/sources/import",
        json={
            "corpus_id": corpus["id"],
            "title": "Quartz calibration",
            "kind": "markdown",
            "text": f"# Quartz calibration\n\n{quote}\n",
        },
    )
    assert selected.status_code == 201, selected.text
    source_id = selected.json()["id"]
    excluded = (
        await client.post(
            f"/api/projects/{project_id}/sources/import",
            json={
                "corpus_id": corpus["id"],
                "title": "Rejected calibration",
                "kind": "text",
                "text": "quartz calibration EXCLUDED_MARKER",
            },
        )
    ).json()
    excluded_response = await client.patch(
        f"/api/projects/{project_id}/sources/{excluded['id']}", json={"status": "excluded"}
    )
    assert excluded_response.status_code == 200
    template = get_template(key)
    body = "\n\n".join(f"## {s.title}\n发现X [1]。" for s in template.sections)
    queries = []
    parsed = []
    qa_mode = False

    class LLM(WorkbenchLLM):
        async def parse(self, system, user, schema, **kwargs):
            parsed.append(str(user))
            if schema is ResearchPlan:
                return ResearchPlan(
                    interpretation="quartz calibration",
                    sub_questions=[
                        SubQuestion(
                            question="quartz calibration", rationale="Check supplied corpus"
                        )
                    ],
                )
            if schema is ExtractedFindingList:
                urls = list(
                    dict.fromkeys(
                        re.findall(
                            r"https://workspace\.invalid/sources/[a-zA-Z0-9-]+\?chunk=\d+",
                            str(user),
                        )
                    )
                )
                return ExtractedFindingList(
                    findings=[verified_finding("发现X", url, quote) for url in urls]
                )
            if schema is Mindmap:
                return Mindmap(
                    root="Calibration",
                    branches=[{"label": "发现X", "kind": "claim", "citations": [1]}],
                )
            return await super().parse(system, user, schema, **kwargs)

        async def stream(self, system, user, **kwargs):
            yield "发现X [1]。" if qa_mode else body

    class EmptySearch(FakeSearch):
        async def search(self, query, **kwargs):
            queries.append(query)
            return []

    async def build_agent(self, settings, **kwargs):
        search = EmptySearch()
        return DeepResearchAgent(settings, llm=LLM(body), search_tool=search, **kwargs), search

    @asynccontextmanager
    async def intent_llm(*args):
        yield LLM(body)

    monkeypatch.setattr(RunExecutor, "build_agent", build_agent)
    monkeypatch.setattr(api, "_intent_llm", intent_llm)
    base = {"template": key, "strategy": "quick", "project_id": project_id, "clarified": True}
    for question, history in [
        ("Analyze quartz calibration", []),
        ("进一步说明这个方法", [{"query": "Analyze quartz calibration", "intent": "unknown"}]),
    ]:
        created = await client.post(
            "/api/runs", json={**base, "query": question, "history": history}
        )
        assert created.status_code == 202, created.text
        run_id = created.json()["run_id"]
        await finish_run(repo)
        detail = await repo.get_run(run_id)
        assert detail is not None
        assert detail.project_id == project_id
        assert any(source_id in citation for citation in detail.report.citations)
        assert any(source_id in f.source_url for result in detail.results for f in result.findings)
        delivery = await client.get(f"/api/runs/{run_id}/deliverables")
        assert delivery.status_code == 200, delivery.text
        record = delivery.json()
        formats = {f["format"] for f in record["items"]}
        required = set(template.deliverables)
        if "mindmap" in required:
            required = required - {"mindmap"} | {"html", "png"}
        assert required <= formats, record["gates"]
        public = await client.get(f"/api/runs/{run_id}")
        assert public.status_code == 200 and public.json()["project_id"] == project_id
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
        if key in {"autoResearch", "litReview"}:
            # A single selected library source cannot meet these research
            # templates' citation/length promises, even when retrieval is correct.
            assert {"length", "citation"} <= {gate["name"] for gate in nonpassing}
            assert expected == "needs_review"
        else:
            assert expected == "done", record["gates"]
        assert public.json()["status"] == detail.status == completion["status"] == expected
        assert public.json()["completion"] == completion
        assert completion["required_formats"] == sorted(required)
        assert completion["content_version"] == record["content_version"]
        assert completion["input_version"] == record["input_version"]
        assert completion["gates"] == record["gates"]
        assert bool(completion["issues"]) is requires_review
        for gate in nonpassing:
            assert set(gate["issues"]) <= set(completion["issues"])
    assert not any("EXCLUDED_MARKER" in prompt for prompt in parsed)
    before_qa = len(queries)
    qa_mode = True
    cid = (await client.post("/api/qa/conversations", json={"title": "Calibration"})).json()["id"]
    response = await client.post(
        f"/api/qa/conversations/{cid}/messages",
        json={
            "query": "quartz calibration",
            "sources": ["library"],
            "project_id": project_id,
            "request_id": "library-only-question",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "done"
    assert any(source_id in citation for citation in response.json()["citations"])
    assert len(queries) == before_qa, "Library-only Q&A must not search the public web"
