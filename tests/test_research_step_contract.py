from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from deep_research.agents.base import Blackboard, RunContext
from deep_research.agents.plan_executor import PlanExecutor
from deep_research.artifacts import ArtifactIntegrityError, ArtifactStore, ArtifactValidationError
from deep_research.config import Settings
from deep_research.observability import Tracer
from deep_research.orchestration.compiler import compile_plan
from deep_research.orchestrator import DeepResearchAgent
from deep_research.plan_handoffs import dependency_context, read_journal
from deep_research.planner_runtime import coerce_execution_plan
from deep_research.skills import SkillResolver
from tests.fakes import FakeLLM, FakeSearch

SLUG = "contract-test"
NOTES = f"work/{SLUG}/research/notes.md"
DATA = f"work/{SLUG}/research/data.json"
REPORT = f"output/{SLUG}/final/report.md"
EXAMPLE_PLAN = Path(__file__).resolve().parents[1] / "framework" / "10_research_plan.json"


class SequenceLLM(FakeLLM):
    def __init__(self, *responses: str | BaseException) -> None:
        super().__init__()
        self.responses = list(responses)
        self.requests: list[tuple[str, str]] = []

    async def complete(self, system: str, user: str, *, temperature: float = 0.3) -> str:
        self.complete_calls += 1
        self.requests.append((system, user))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def envelope(
    *artifacts: tuple[str, str], status: str = "done", gaps: list[str] | None = None
) -> str:
    return json.dumps(
        {
            "contract_version": 1,
            "status": status,
            "summary": "Research summary",
            "artifacts": [{"path": path, "content": content} for path, content in artifacts],
            "gaps": gaps or [],
            "next_actions": ["Verify remaining claims"] if gaps else [],
        }
    )


def context(tmp_path: Path, llm: FakeLLM) -> RunContext:
    return RunContext(
        llm=llm,
        search_tool=FakeSearch(),
        tracer=Tracer(),
        settings=Settings(runner_enabled=False, artifact_root=str(tmp_path)),
        artifact_store=ArtifactStore(tmp_path),
        artifact_slug=SLUG,
    )


def board(outputs: list[str] | None = None, **metadata: object) -> Blackboard:
    return Blackboard(
        query="Compare published methods",
        scratch={
            "_active_step_metadata": {
                "plan_step_id": "research",
                "prompt": "Summarize only supplied evidence",
                "expected_outputs": outputs if outputs is not None else [NOTES],
                **metadata,
            }
        },
    )


@pytest.mark.asyncio
async def test_distinct_outputs_partial_handoff_and_plan_status(tmp_path: Path) -> None:
    llm = SequenceLLM(
        envelope(
            (NOTES, "Useful evidence"),
            (DATA, '{"sources": []}'),
            status="partial",
            gaps=["Full text unavailable"],
        ),
        "# Deliverable\nAvailable findings.",
    )
    plan = {
        "slug": SLUG,
        "title": "Contract test",
        "steps": [
            {
                "id": "research",
                "name": "Research",
                "prompt": "Analyze supplied evidence",
                "artifacts": [NOTES, DATA],
            },
            {
                "id": "deliver",
                "name": "Deliver",
                "prompt": "Report findings and gaps",
                "artifacts": [REPORT],
            },
        ],
    }
    agent = DeepResearchAgent(
        Settings(artifact_root=str(tmp_path), runner_enabled=False),
        llm=llm,
        search_tool=FakeSearch(),
        artifact_store=ArtifactStore(tmp_path),
        execution_plan=plan,
    )
    try:
        report = await agent.run("Compare published methods")
    finally:
        await agent.aclose()
    assert (tmp_path / NOTES).read_text(encoding="utf-8") == "Useful evidence"
    assert json.loads((tmp_path / DATA).read_text()) == {"sources": []}
    assert "Full text unavailable" in llm.requests[1][1]
    assert "Full text unavailable" in report.markdown
    saved = json.loads((tmp_path / f".framework/plans/{SLUG}.json").read_text())
    assert saved["status"] == "partial"
    assert [step["status"] for step in saved["steps"]] == ["partial", "done"]
    assert saved["steps"][0]["metadata"]["gap_note"] == "Full text unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        "not JSON",
        '{"a": NaN}',
        '{"a":1,"a":2}',
        '```json\n{"a":1}\n```',
    ],
)
async def test_invalid_json_is_failed_and_raw_response_is_preserved(tmp_path: Path, response: str):
    llm = SequenceLLM(response)
    ctx = context(tmp_path, llm)
    with pytest.raises(ArtifactValidationError, match="artifact result contract"):
        await PlanExecutor().step(board([DATA]), ctx)
    assert not (tmp_path / DATA).exists()
    journal = read_journal(ctx.artifact_store, SLUG, "research")
    assert journal["status"] == "failed"
    assert ctx.artifact_store.read_text(journal["response_path"]) == response


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        envelope((NOTES, "a")),
        envelope((NOTES, "a"), (NOTES, "b"), (DATA, "{}")),
        envelope((NOTES, "a"), (DATA, "invalid")),
        envelope((NOTES, "a"), (DATA, "{}"), (REPORT, "undeclared")),
        envelope((NOTES, "a"), (DATA, "{}"), status="partial"),
    ],
)
async def test_all_outputs_are_validated_before_any_publish(tmp_path: Path, response: str):
    ctx = context(tmp_path, SequenceLLM(response))
    with pytest.raises(ArtifactValidationError):
        await PlanExecutor().step(board([NOTES, DATA]), ctx)
    assert not (tmp_path / NOTES).exists()
    assert not (tmp_path / DATA).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        f"output/{SLUG}/final/report.pdf",
        "work/other/research/result.md",
        f"work/{SLUG}/executor-journal/fake.txt",
    ],
)
async def test_unsupported_or_wrong_scope_outputs_fail_before_model_call(tmp_path: Path, path: str):
    llm = SequenceLLM("unused")
    with pytest.raises(ArtifactValidationError):
        await PlanExecutor().step(board([path]), context(tmp_path, llm))
    assert llm.complete_calls == 0


@pytest.mark.asyncio
async def test_recover_committed_step_after_lost_workflow_checkpoint(tmp_path: Path) -> None:
    llm = SequenceLLM("# Preserved report")
    ctx = context(tmp_path, llm)
    await PlanExecutor().step(board([REPORT], is_terminal=True), ctx)
    # Simulates a new worker with only the disk artifacts and pre-step checkpoint.
    resumed = board([REPORT], is_terminal=True)
    await PlanExecutor().step(resumed, context(tmp_path, llm))
    assert llm.complete_calls == 1
    assert resumed.report.markdown == "# Preserved report"
    assert resumed.scratch["plan_step_results"]["research"]["status"] == "done"
    assert read_journal(ctx.artifact_store, SLUG, "research")["attempt"] == 1


@pytest.mark.asyncio
async def test_recovery_rejects_tampered_outputs(tmp_path: Path) -> None:
    llm = SequenceLLM("Original evidence")
    ctx = context(tmp_path, llm)
    await PlanExecutor().step(board(), ctx)
    (tmp_path / NOTES).write_text("Tampered", encoding="utf-8")
    with pytest.raises(ArtifactIntegrityError):
        await PlanExecutor().step(board(), ctx)
    assert llm.complete_calls == 1


@pytest.mark.asyncio
async def test_changed_prompt_invalidates_committed_receipt(tmp_path: Path) -> None:
    llm = SequenceLLM("First result", "Updated result")
    ctx = context(tmp_path, llm)
    await PlanExecutor().step(board(), ctx)
    await PlanExecutor().step(board(prompt="Reassess with new criteria"), ctx)
    assert llm.complete_calls == 2
    assert (tmp_path / NOTES).read_text() == "Updated result"


@pytest.mark.asyncio
async def test_interrupted_step_is_durable_and_retried(tmp_path: Path) -> None:
    llm = SequenceLLM(asyncio.CancelledError(), "Recovered result")
    ctx = context(tmp_path, llm)
    with pytest.raises(asyncio.CancelledError):
        await PlanExecutor().step(board(), ctx)
    assert read_journal(ctx.artifact_store, SLUG, "research")["status"] == "interrupted"
    await PlanExecutor().step(board(), ctx)
    assert read_journal(ctx.artifact_store, SLUG, "research")["attempt"] == 2
    assert "CancelledError" in llm.requests[1][1]


def test_dag_handoffs_exclude_unrelated_branches_and_bound_each_excerpt(tmp_path: Path) -> None:
    plan = coerce_execution_plan(
        {
            "slug": SLUG,
            "title": "Scoped handoffs",
            "steps": [
                {"id": "a", "name": "A", "prompt": "A", "artifacts": [NOTES]},
                {
                    "id": "b",
                    "name": "B",
                    "prompt": "B",
                    "artifacts": [f"work/{SLUG}/other/secret.md"],
                },
                {"id": "c", "name": "C", "prompt": "C", "depends_on": ["a"]},
            ],
        },
        query="Q",
    )
    compiled = compile_plan(plan, available_agents={"plan_executor"}, default_agent="plan_executor")
    metadata = compiled.workflow.steps[2].metadata
    assert [item["id"] for item in metadata["handoff_steps"]] == ["a"]
    store = ArtifactStore(tmp_path)
    store.write_text(SLUG, "research", "notes.md", "E" * 30_000)
    store.write_text(SLUG, "other", "secret.md", "unrelated branch data")
    payload = dependency_context(store, SLUG, metadata, {})
    assert "unrelated branch data" not in payload
    item = json.loads(payload)["artifacts"][0]
    assert item["truncated"] is True
    assert 0 < len(item["content"]) < 24_000
    assert len(payload) <= 24_000
    (tmp_path / NOTES).unlink()
    with pytest.raises(ArtifactIntegrityError):
        dependency_context(store, SLUG, metadata, {})


@pytest.mark.asyncio
async def test_declared_skill_reaches_executor_prompt(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "research-method"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("Always report alternative explanations.", encoding="utf-8")
    resolver = SkillResolver((tmp_path / "skills",))
    plan = coerce_execution_plan(
        {
            "slug": SLUG,
            "title": "Skill contract",
            "steps": [
                {
                    "id": "research",
                    "name": "Research",
                    "skills": ["research-method"],
                    "prompt": "Read skills/research-method/SKILL.md and assess the evidence.",
                    "artifacts": [NOTES],
                }
            ],
        },
        query="Q",
    )
    compiled = compile_plan(
        plan,
        available_agents={"plan_executor"},
        default_agent="plan_executor",
        skill_resolver=resolver,
    )
    llm = SequenceLLM("# Assessment")
    ctx = context(tmp_path, llm)
    ctx.skill_resolver = resolver
    bb = Blackboard(
        query="Q", scratch={"_active_step_metadata": compiled.workflow.steps[0].metadata}
    )
    await PlanExecutor().step(bb, ctx)
    assert "Always report alternative explanations." in llm.requests[0][1]


@pytest.mark.asyncio
async def test_storage_failure_keeps_attempt_files_out_of_handoffs(tmp_path: Path, monkeypatch):
    response = envelope((NOTES, "Not committed yet"), (DATA, "{}"))
    llm = SequenceLLM(response, response)
    ctx = context(tmp_path, llm)
    store = ctx.artifact_store
    write = store.write_text

    def fail_second_file(slug, stage, name, content, **kwargs):
        if name == "data.json":
            raise OSError("storage unavailable")
        return write(slug, stage, name, content, **kwargs)

    monkeypatch.setattr(store, "write_text", fail_second_file)
    with pytest.raises(OSError, match="storage unavailable"):
        await PlanExecutor().step(board([NOTES, DATA]), ctx)
    assert (tmp_path / NOTES).exists()
    handoff = dependency_context(
        store,
        SLUG,
        {"handoff_steps": [{"id": "research", "outputs": [{"path": NOTES}, {"path": DATA}]}]},
        {},
    )
    assert json.loads(handoff)["artifacts"] == []
    assert "storage unavailable" in handoff
    monkeypatch.setattr(store, "write_text", write)
    await PlanExecutor().step(board([NOTES, DATA]), ctx)
    assert read_journal(store, SLUG, "research")["status"] == "done"
    assert llm.complete_calls == 2


@pytest.mark.asyncio
async def test_runtime_retry_receives_format_failure_and_recovers(tmp_path: Path) -> None:
    llm = SequenceLLM("invalid json", '{"ok":true}')
    agent = DeepResearchAgent(
        Settings(artifact_root=str(tmp_path), runner_enabled=False),
        llm=llm,
        search_tool=FakeSearch(),
        artifact_store=ArtifactStore(tmp_path),
        execution_plan={
            "slug": SLUG,
            "title": "Retry",
            "steps": [
                {
                    "id": "research",
                    "name": "Research",
                    "prompt": "Return structured evidence",
                    "artifacts": [DATA],
                    "resource": {"max_attempts": 2},
                }
            ],
        },
    )
    try:
        await agent.run("Q")
    finally:
        await agent.aclose()
    assert llm.complete_calls == 2
    assert "artifact result contract failed" in llm.requests[1][1]
    assert json.loads((tmp_path / DATA).read_text()) == {"ok": True}
    assert read_journal(ArtifactStore(tmp_path), SLUG, "research")["status"] == "done"


@pytest.mark.asyncio
async def test_shipped_plan_uses_research_tools_then_custom_text_steps(tmp_path: Path) -> None:
    llm = SequenceLLM(
        envelope(
            ("work/long-research/assessment/assessment.md", "证据比较：发现X [1]。"),
            ("work/long-research/assessment/gaps.json", '{"gaps":[]}'),
        ),
        envelope(("output/long-research/final/report.md", "# 研究报告\n\n发现X [1]。")),
    )
    store = ArtifactStore(tmp_path)
    agent = DeepResearchAgent(
        Settings(artifact_root=str(tmp_path), runner_enabled=False),
        llm=llm,
        search_tool=FakeSearch(),
        artifact_store=store,
        execution_plan=EXAMPLE_PLAN.read_text(encoding="utf-8"),
    )
    try:
        report = await agent.run("给出已发表研究方法的资料概览")
    finally:
        await agent.aclose()
    assert llm.parse_calls >= 3  # Planner, evidence extraction and reflection.
    assert llm.complete_calls == 2  # Assessment and delivery only.
    assert "researcher/results.json" in llm.requests[0][1]
    assert "assessment/assessment.md" in llm.requests[1][1]
    assert "发现X [1]" in report.markdown
    assert "https://a.com" in report.markdown
    assert store.verify_manifest("long-research", raise_on_error=True)
    saved = store.read_control_json("plans/long-research.json")
    assert all(step["status"] in {"done", "partial"} for step in saved["steps"])


@pytest.mark.asyncio
async def test_cli_plan_uses_production_agent(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    from deep_research import cli
    from deep_research.models import Report

    observed = {}

    class Agent:
        def __init__(self, settings, **kwargs):
            observed.update(kwargs)
            self.tracer = SimpleNamespace(subscribe=lambda callback: None)

        async def run(self, query):
            observed["query"] = query
            return Report(query=query, markdown="# Report")

        async def aclose(self):
            observed["closed"] = True

    output = tmp_path / "report.md"
    monkeypatch.setattr(cli, "DeepResearchAgent", Agent)
    monkeypatch.setattr(
        "sys.argv",
        ["deep-research", "Research Q", "--plan", str(EXAMPLE_PLAN), "-o", str(output)],
    )
    await cli._amain()
    assert observed["execution_plan"].slug == "long-research"
    assert observed["query"] == "Research Q"
    assert observed["closed"] is True
    assert output.read_text(encoding="utf-8").endswith("# Report")


@pytest.mark.asyncio
async def test_cli_rejects_invalid_plan_before_client_creation(tmp_path: Path, monkeypatch):
    from deep_research import cli

    invalid = tmp_path / "invalid.json"
    invalid.write_text("not JSON", encoding="utf-8")

    def unexpected_client(*args, **kwargs):
        pytest.fail("Client must not be created for an invalid plan")

    monkeypatch.setattr(cli, "DeepResearchAgent", unexpected_client)
    monkeypatch.setattr("sys.argv", ["deep-research", "--plan", str(invalid)])
    with pytest.raises(SystemExit) as error:
        await cli._amain()
    assert error.value.code == 2
