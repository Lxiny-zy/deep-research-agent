"""Exercise public task routes through the real executor, SQLite and downloads.

Only external model/search responses are fixtures. This proves application
wiring, not model accuracy or live search quality.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import zipfile

import httpx
import pytest

from deep_research import api
from deep_research.config import Settings
from deep_research.execution import ExecutionContext, RunExecutor
from deep_research.models import ExtractedFindingList
from deep_research.orchestrator import DeepResearchAgent
from deep_research.persistence.db import create_all, make_engine, make_sessionmaker
from deep_research.persistence.sql_repository import SqlRepository
from deep_research.workbench.attachments import Attachment
from deep_research.workbench.qa_store import SqlQaStore
from deep_research.workbench.support import SupportDecisions
from deep_research.workbench.templates import TASK_TEMPLATES, get_template
from deep_research.workbench.writers import Mindmap
from deep_research.worker import Worker
from tests.fakes import FakeSearch, verified_finding
from tests.queue_helpers import drain_inline
from tests.test_workbench import WorkbenchLLM


@pytest.fixture(params=["inline", "worker"])
async def scenario_app(monkeypatch, tmp_path, request):
    settings = Settings(
        artifact_root=str(tmp_path / "artifacts"),
        intent_enabled=False,
        orchestration_mode="legacy",
        execution_mode=request.param,
        max_rounds=0,
        quality={"max_revisions": 0, "register_check": False},
    )
    engine = make_engine(f"sqlite+aiosqlite:///{(tmp_path / 'runs.db').as_posix()}")
    await create_all(engine)
    repo = SqlRepository(make_sessionmaker(engine))
    for name, value in {
        "settings": settings,
        "repo": repo,
        "catalog": None,
        "library": None,
        "executor": None,
        "inline_worker": None,
        "live": {},
        "tasks": set(),
        "run_tasks": {},
        "cancellation_requested": set(),
        "config_lock": asyncio.Lock(),
        "delivery_cache": {},
        "delivery_pending": {},
        "qa_store": SqlQaStore(make_sessionmaker(engine)),
        "qa_requests": None,
        "qa_requests_store": None,
        "qa_live_turns": {},
        "qa_tasks": set(),
        "paper_evidence_cache": None,
        "run_admission": api.RunAdmission(2, 2),
    }.items():
        monkeypatch.setattr(api.app.state, name, value, raising=False)
    monkeypatch.setattr(api, "_run_limiter", api._RateLimiter(1000, 60))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        yield client, repo
    await asyncio.gather(*list(api.app.state.tasks))
    await asyncio.gather(*list(api.app.state.qa_tasks))
    await engine.dispose()


CASES = [(t.key, strategy) for t in TASK_TEMPLATES.values() for strategy in t.strategies]


@pytest.mark.parametrize(("key", "strategy"), CASES)
async def test_task_creation_execution_result_and_all_promised_downloads(
    scenario_app, monkeypatch, key, strategy
):
    client, repo = scenario_app
    template = get_template(key)
    body = "\n\n".join(
        f"## {s.title}\n"
        + (
            "内容A提供了可核验的原文证据。"
            if s.key == "translation"
            else "观察各变量的分布与关系。"
            if key == "dataAnalysis"
            else "评审总分：7/10。\n发现X [1]。"
            if s.key == "score"
            else "发现X [1]。"
        )
        for s in template.sections
    )
    sources = []
    searches = []
    qa_mode = False

    class ScenarioLLM(WorkbenchLLM):
        async def stream(self, system, user, **kwargs):
            if qa_mode:
                yield "发现X [1]。"
            else:
                async for delta in super().stream(system, user, **kwargs):
                    yield delta

        async def parse(self, system, user, schema, **kwargs):
            if schema is Mindmap:
                return Mindmap(
                    root="材料结论",
                    branches=[{"label": "发现X", "kind": "claim", "citations": [1]}],
                )
            if schema is ExtractedFindingList and sources:
                selected = [s for s in sources if s.url in user]
                return ExtractedFindingList(
                    findings=[
                        verified_finding("发现X", s.url, "内容A提供了可核验的原文证据")
                        for s in selected
                    ]
                )
            result = await super().parse(system, user, schema, **kwargs)
            if schema is SupportDecisions:
                units = {u["id"]: u for u in json.loads(user)["units"]}
                for decision in result.decisions:
                    if units[decision.unit_id]["kind"] == "translation":
                        decision.verdict = "supported"
            return result

    class Search(FakeSearch):
        async def search(self, query, **kwargs):
            searches.append(query)
            assert strategy != "none", "Provided inputs must not trigger open retrieval"
            return await super().search(query, **kwargs)

    async def build_agent(self, settings, **kwargs):
        search = Search()
        return DeepResearchAgent(
            settings, llm=ScenarioLLM(body), search_tool=search, **kwargs
        ), search

    monkeypatch.setattr(RunExecutor, "build_agent", build_agent)
    payload = {
        "template": key,
        "strategy": strategy,
        "query": "分析材料中的方法与结果",
        "clarified": True,
    }
    if key == "dataAnalysis":
        csv = b"group,x,y\nA,1,2\nA,2,4\nA,3,5\nB,4,7\nB,5,9\nB,6,10\n"
        parsed = await client.post(
            "/api/datasets",
            json={
                "filename": "measurements.csv",
                "data_base64": base64.b64encode(csv).decode(),
            },
        )
        assert parsed.status_code == 200, parsed.text
        payload.update(
            dataset=parsed.json()["sheets"][0]["csv"],
            dataset_source={"filename": "measurements.csv"},
        )
    elif strategy == "none":
        uploaded = await client.post(
            "/api/attachments/file?filename=paper.md",
            content=(
                "# 流程样本\n\n## Abstract\n\n内容A提供了可核验的原文证据。\n\n"
                "## Method\n\n内容A提供了可核验的原文证据。\n"
            ).encode(),
            headers={"Content-Type": "application/octet-stream"},
        )
        assert uploaded.status_code == 201, uploaded.text
        attachment = Attachment.model_validate(uploaded.json()["attachment"])
        sources.extend(attachment.sources())
        payload["attachments"] = [attachment.model_dump(mode="json")]
    created = await client.post("/api/runs", json=payload)
    assert created.status_code == 202, created.text
    run_id = created.json()["run_id"]
    if api.app.state.settings.execution_mode == "worker":
        assert await repo.get_run_status(run_id) == "pending"
        worker = Worker(repo, RunExecutor(ExecutionContext(repo=repo)), api.app.state.settings)
        await worker._tick()
        assert len(worker._running) == 1
        await asyncio.wait_for(worker._drain(), timeout=45)
        await repo.remove_worker(worker.name)
    else:
        await drain_inline(api.app, timeout=45)
    detail = await repo.get_run(run_id)
    assert detail is not None
    completion = detail.orchestration.checkpoint.get("scratch", {}).get("_completion")
    assert isinstance(completion, dict), {
        "status": detail.status,
        "events": [
            (event.type, event.message)
            for event in await repo.get_events(run_id)
            if event.stage == "ORCHESTRATOR"
        ],
    }
    assert detail.status == completion["status"]
    assert detail.report is not None and detail.report.markdown.strip()
    assert detail.orchestration.workflow_name == template.workflow_for(strategy)
    assert bool(searches) is (strategy != "none")
    events = await client.get(f"/api/runs/{run_id}/events")
    assert events.status_code == 200 and events.json()
    workspace = await client.get(f"/api/runs/{run_id}/workspace")
    assert workspace.status_code == 200
    registry = await client.get(f"/api/runs/{run_id}/deliverables")
    assert registry.status_code == 200, registry.text
    record = registry.json()
    formats = {item["format"] for item in record["items"]}
    promised = set(template.deliverables)
    if "mindmap" in promised:
        promised = promised - {"mindmap"} | {"html", "png"}
    assert promised <= formats, {
        "missing": promised - formats,
        "failed": [(g["name"], g["issues"]) for g in record["gates"] if g["status"] == "fail"],
    }
    nonpassing_gates = [gate for gate in record["gates"] if gate["status"] != "pass"]
    required_files = [item for item in record["items"] if item["format"] in promised]
    requires_review = (
        bool(nonpassing_gates or record["failures"])
        or record["status"] != "pass"
        or any(item["status"] != "pass" or item["size"] <= 0 for item in required_files)
    )
    expected_status = "needs_review" if requires_review else "done"
    # The fixed external fixture intentionally supplies a short draft and one
    # cited finding. Its quality warnings must stay visible, even though every
    # promised file can be downloaded and opened successfully.
    if key == "autoResearch":
        assert {"length", "citation"} <= {gate["name"] for gate in nonpassing_gates}
        assert expected_status == "needs_review"
    response = await client.get(f"/api/runs/{run_id}")
    assert response.status_code == 200, response.text
    public = response.json()
    assert public["report"]["markdown"]
    current = await repo.get_run(run_id)
    current_completion = current.orchestration.checkpoint["scratch"]["_completion"]
    assert public["completion"] == current_completion
    assert public["status"] == current.status == current_completion["status"] == expected_status
    assert current_completion["required_formats"] == sorted(promised)
    assert current_completion["content_version"] == record["content_version"]
    assert current_completion["input_version"] == record["input_version"]
    assert current_completion["gates"] == record["gates"]
    assert bool(current_completion["issues"]) is requires_review
    for gate in nonpassing_gates:
        assert set(gate["issues"]) <= set(current_completion["issues"])
    for item in record["items"]:
        download = await client.get(f"/api/runs/{run_id}/deliverables/{item['name']}")
        assert download.status_code == 200, item
        assert len(download.content) == item["size"] > 0
        assert hashlib.sha256(download.content).hexdigest() == item["sha256"]
        if item["format"] in {"xlsx", "pptx", "docx"}:
            with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
                assert archive.testzip() is None
        elif item["format"] == "pdf":
            assert download.content.startswith(b"%PDF")
    if key == "paperRead":
        reader = await client.get(f"/api/runs/{run_id}/reader")
        assert reader.status_code == 200 and reader.json()["can_ask"]
        qa_mode = True
        conversation = await client.post("/api/qa/conversations", json={"run_id": run_id})
        assert conversation.status_code == 201, conversation.text
        cid = conversation.json()["id"]
        for i, question in enumerate(["论文的主要发现是什么？", "这个结论有什么依据？"]):
            streamed = await client.post(
                f"/api/qa/conversations/{cid}/messages/stream",
                json={"query": question, "request_id": f"paper-question-{i}"},
            )
            assert streamed.status_code == 200 and "event: complete" in streamed.text, streamed.text
        history = (await client.get(f"/api/qa/conversations/{cid}")).json()["messages"]
        assert len(history) == 2 and all(
            m["citations"] and m["status"] == "done" and "发现X" in m["answer"] for m in history
        )
        assert not searches


async def test_academic_qa_stream_history_and_optional_search(scenario_app, monkeypatch):
    from tests.test_workbench import QaLLM

    client, _ = scenario_app
    searches = []

    class Search(FakeSearch):
        async def search(self, query, **kwargs):
            searches.append(query)
            return await super().search(query, **kwargs)

    async def build_agent(self, settings, **kwargs):
        search = Search()
        return DeepResearchAgent(settings, llm=QaLLM(), search_tool=search, **kwargs), search

    monkeypatch.setattr(RunExecutor, "build_agent", build_agent)
    created = await client.post("/api/qa/conversations", json={"title": "研究问答"})
    assert created.status_code == 201
    cid = created.json()["id"]
    first = await client.post(
        f"/api/qa/conversations/{cid}/messages/stream",
        json={"query": "什么是光谱成像？", "request_id": "qa-knowledge-turn"},
    )
    assert first.status_code == 200 and "event: complete" in first.text
    assert not searches
    for i, question in enumerate(["CASSI 的研究结论是什么？", "这个结论有什么依据？"]):
        result = await client.post(
            f"/api/qa/conversations/{cid}/messages/stream",
            json={"query": question, "sources": ["web"], "request_id": f"qa-search-turn-{i}"},
        )
        assert result.status_code == 200 and "event: complete" in result.text
    detail = (await client.get(f"/api/qa/conversations/{cid}")).json()
    messages = detail["messages"]
    assert len(messages) == 3 and all(
        m["status"] == "done" and "发现X" in m["answer"] for m in messages
    )
    assert messages[0]["citations"] == []
    assert messages[1]["citations"] == messages[2]["citations"] == ["https://a.com"]
    assert searches and "CASSI" in searches[-1]
