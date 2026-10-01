"""交付质量体系回归：质量策略配置、学术写作检查、写作返工循环、证据覆盖缺口。"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from httpx import ASGITransport

from deep_research.models import ResearchResult
from deep_research.workbench.contract import CONTRACT_SCRATCH_KEY, build_contract
from deep_research.workbench.coverage import coverage_gaps, gap_prompt
from deep_research.workbench.quality import (
    QUALITY_FIELDS,
    QualityPolicy,
    coerce_policy,
    quality_schema,
)
from deep_research.workbench.revision import Assessment, write_with_revisions
from deep_research.workbench.scholarly import (
    check_abstract,
    check_clusters,
    check_limitations,
    check_recency,
    check_register,
    check_sources,
    requested_year,
)
from deep_research.workbench.templates import get_template
from tests.fakes import verified_finding

POLICY = QualityPolicy()

# --------------------------------------------------------------------------- 策略


def test_policy_defaults_and_survey_minimum_is_twenty() -> None:
    assert POLICY.survey_min_citations == 20
    assert POLICY.min_citations_for("litReview", 6) == 20
    assert POLICY.min_citations_for("autoResearch", 3) == POLICY.research_min_citations
    # 其它任务沿用模板自己的下限
    assert POLICY.min_citations_for("peerReview", 1) == 1


def test_every_field_has_help_text_and_valid_bounds() -> None:
    schema = {item["key"]: item for item in quality_schema()}
    assert set(schema) == set(QualityPolicy.model_fields)
    for field in QUALITY_FIELDS:
        entry = schema[field.key]
        # 悬浮说明必须足够具体：至少讲清「是什么」与「不满足时会怎样」
        assert len(entry["help"]) >= 40, field.key
        if field.kind == "int":
            assert entry["min"] <= entry["default"] <= entry["max"], field.key
        else:
            assert isinstance(entry["default"], bool), field.key


def test_coerce_policy_tolerates_bad_values() -> None:
    assert coerce_policy({"survey_min_citations": 9999}) == QualityPolicy()
    assert coerce_policy(None) == QualityPolicy()
    assert coerce_policy({"survey_min_citations": 12}).survey_min_citations == 12


def test_contract_freezes_policy_and_minimum() -> None:
    template = get_template("litReview")
    assert template is not None
    contract = build_contract(template, "高光谱重建综述", quality={"survey_min_citations": 25})
    assert contract.min_citations == 25
    assert contract.quality["survey_min_citations"] == 25
    assert any("25" in item for item in contract.constraints)
    assert any("摘要" in item for item in contract.constraints)


# --------------------------------------------------------------------------- 学术检查


def test_register_flags_paired_frames_colloquialisms_and_production_narration() -> None:
    body = (
        "## 分析\n\n"
        "该方法不是简单堆叠模块，而是重新设计了展开结构 [1]。\n\n"
        "说白了，这是一个颠覆性的工作 [1]。\n\n"
        "作为 AI，我无法确认该结论 [1]。\n\n"
        "重建精度较高,但计算开销大 [1]。\n"
    )
    codes = {finding.code for finding in check_register(body, POLICY)}
    assert {"paired-frame", "colloquial", "production-narration", "cjk-ascii-punctuation"} <= codes


def test_register_ignores_clean_academic_prose_and_code() -> None:
    body = (
        "## 分析\n\n深度展开网络将迭代优化映射为可学习的网络层 [1]。"
        "该设计在 CASSI 数据集上取得了更高的 PSNR [2]。\n\n"
        "```python\nx = a if b else c  # 不是……而是……\n```\n"
    )
    assert check_register(body, POLICY) == []


def test_overlong_sentence_is_only_a_warning() -> None:
    body = "## 分析\n\n" + "该方法" * 80 + "取得了提升 [1]。\n"
    findings = check_register(body, POLICY)
    assert findings and all(f.severity == "warning" for f in findings)


def test_abstract_citation_cluster_and_limitations() -> None:
    body = "## 摘要\n\n本文综述了方法 [1]。\n\n## 分析\n\n方法 A 有效 [1, 2, 3, 4, 5, 6, 7, 8]。\n"
    assert check_abstract(body)[0].code == "abstract-citation"
    assert check_clusters(body, POLICY)[0].code == "citation-cluster"
    assert check_limitations(body)[0].code == "missing-limitations"
    assert check_limitations(body + "\n## 局限\n\n样本量有限 [1]。\n") == []


def test_duplicate_sources_and_shortfall() -> None:
    findings = check_sources(
        ["https://arxiv.org/abs/2205.10102", "https://arxiv.org/pdf/2205.10102v2.pdf"],
        used=2,
        minimum=20,
    )
    codes = [finding.code for finding in findings]
    assert codes == ["duplicate-source", "citation-shortfall"]
    assert "1 个" in findings[1].message  # 去重后只剩 1 个不同来源


def test_fragment_anchors_count_one_document_without_forcing_writer_to_delete_citations():
    from deep_research.workbench.gates import citation_gate

    citations = [f"https://workspace.invalid/attachments/paper?chunk={i}" for i in range(1, 5)]
    assert not check_sources(citations, 4, 1)
    assert [f.code for f in check_sources(citations, 4, 2)] == ["citation-shortfall"]
    template = get_template("paperRead")
    assert template is not None
    gate = citation_gate("结论 [1][2][3][4]。", citations, template, min_citations=1)
    assert gate.status == "pass" and gate.metrics["used"] == 1
    assert gate.metrics["anchors_used"] == 4


def test_recency_requires_sources_from_requested_year() -> None:
    assert requested_year("2025 年以来扩散模型用于光谱重建的进展") == 2025
    assert requested_year("progress since 2024") == 2024
    assert requested_year("快照光谱成像综述") is None
    assert check_recency("2025 年以来的进展", ["Smith et al. 2021", "Wang 2023"])[0].code == (
        "recency-gap"
    )
    assert check_recency("2025 年以来的进展", ["Li et al., CVPR 2025"]) == []


# --------------------------------------------------------------------------- 返工循环


def test_revision_loop_rewrites_until_clean_and_passes_problems_back() -> None:
    drafts = iter(["坏稿", "仍有一处问题", "好稿"])
    prompts: list[str | None] = []

    async def write(revision: str | None) -> str:
        prompts.append(revision)
        return next(drafts)

    def assess(body: str) -> Assessment:
        return {
            "坏稿": Assessment(hard=["缺少章节：结论", "数字 99 在所引素材中找不到"]),
            "仍有一处问题": Assessment(hard=["缺少章节：结论"]),
            "好稿": Assessment(),
        }[body]

    body, log = asyncio.run(write_with_revisions(write, assess, max_revisions=3))
    assert body == "好稿"
    assert log.attempts == 3 and log.remaining == [] and log.chosen == 3
    assert prompts[0] is None
    assert prompts[1] is not None and "缺少章节：结论" in prompts[1] and "坏稿" in prompts[1]


def test_revision_loop_keeps_the_best_draft_when_revisions_run_out() -> None:
    drafts = iter(["一处问题", "三处问题"])

    async def write(revision: str | None) -> str:
        return next(drafts)

    def assess(body: str) -> Assessment:
        return Assessment(hard=["x"] if body == "一处问题" else ["x", "y", "z"])

    body, log = asyncio.run(write_with_revisions(write, assess, max_revisions=1))
    assert body == "一处问题" and log.chosen == 1 and log.remaining == ["x"]


def test_revision_loop_returns_best_draft_when_a_later_write_fails() -> None:
    calls = 0

    async def write(revision: str | None) -> str:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("provider down")
        return "首稿"

    body, log = asyncio.run(
        write_with_revisions(write, lambda _: Assessment(hard=["x"]), max_revisions=2)
    )
    assert body == "首稿" and log.attempts == 1


# --------------------------------------------------------------------------- 覆盖缺口


def _scratch(template_key: str, query: str, **quality) -> dict:  # type: ignore[no-untyped-def]
    template = get_template(template_key)
    assert template is not None
    contract = build_contract(template, query, quality=quality or None)
    return {CONTRACT_SCRATCH_KEY: contract.model_dump(mode="json")}


def _results(count: int) -> list[ResearchResult]:
    return [
        ResearchResult(
            sub_question=f"q{i}",
            findings=[
                verified_finding(
                    f"发现{i}", f"https://s{i}.example/paper", evidence_quote=f"原文证据{i}"
                )
            ],
        )
        for i in range(count)
    ]


def test_coverage_gaps_track_the_survey_minimum() -> None:
    gaps = coverage_gaps(_scratch("litReview", "高光谱重建综述"), _results(5), "q")
    assert gaps.open and "至少 20 个" in gaps.gaps[0]
    assert "is_sufficient=true" in gap_prompt(gaps)
    closed = coverage_gaps(
        _scratch("litReview", "高光谱重建综述", survey_min_citations=5), _results(5), "q"
    )
    assert not closed.open


def test_coverage_gaps_flag_missing_recent_sources() -> None:
    gaps = coverage_gaps(
        _scratch("autoResearch", "2025 年以来扩散模型用于光谱重建的进展"), _results(4), "q"
    )
    assert any("2025" in gap for gap in gaps.gaps)


@pytest.mark.asyncio
async def test_reflector_keeps_searching_while_delivery_requirements_are_unmet(settings) -> None:
    from deep_research.agents.base import Blackboard, RunContext
    from deep_research.agents.reflector import Reflector
    from deep_research.models import Reflection
    from deep_research.observability import Tracer
    from tests.fakes import FakeLLM, FakeSearch

    class OptimisticLLM(FakeLLM):
        prompts: list[str] = []

        async def parse(self, system, user, schema, *, temperature=0.2, retries=2):  # type: ignore[no-untyped-def]
            self.prompts.append(user)
            return Reflection(is_sufficient=True, new_sub_questions=["近期代表方法有哪些"])

    llm = OptimisticLLM()
    ctx = RunContext(llm=llm, search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    bb = Blackboard(query="高光谱重建综述", results=_results(3))
    bb.scratch.update(_scratch("litReview", "高光谱重建综述"))
    bb = await Reflector().step(bb, ctx)
    assert bb.reflections[-1].is_sufficient is False
    assert "交付要求检查" in llm.prompts[-1]


# --------------------------------------------------------------------------- 配置接口


def _client(app) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_quality_policy_is_editable_through_config_api(monkeypatch, tmp_path) -> None:
    from deep_research import api
    from deep_research.config import Settings

    monkeypatch.setenv("RUNTIME_CONFIG_PATH", str(tmp_path / "runtime.json"))
    monkeypatch.setattr(api.app.state, "catalog", None, raising=False)
    api.app.state.settings = Settings()
    api.app.state.config_lock = asyncio.Lock()
    async with _client(api.app) as client:
        schema = await client.get("/api/config/quality-schema")
        before = await client.get("/api/config")
        updated = await client.put("/api/config", json={"quality": {"survey_min_citations": 30}})
        rejected = await client.put("/api/config", json={"quality": {"max_revisions": 99}})
        after = await client.get("/api/config")
    assert schema.status_code == 200 and any(
        item["key"] == "survey_min_citations" and item["default"] == 20 for item in schema.json()
    )
    assert before.json()["quality"]["survey_min_citations"] == 20
    assert updated.status_code == 200, updated.text
    assert updated.json()["quality"]["survey_min_citations"] == 30
    # 部分更新不会清掉其它字段
    assert updated.json()["quality"]["max_revisions"] == POLICY.max_revisions
    assert rejected.status_code == 422
    assert after.json()["quality"]["survey_min_citations"] == 30
    assert (tmp_path / "runtime.json").exists()


# --------------------------------------------------------------------------- 失败路径


def _llm(monkeypatch, outcomes):  # type: ignore[no-untyped-def]
    """构造一个 LLM，``_stream_once`` 依次按 outcomes 抛错或产出增量。"""
    from deep_research import llm as llm_module
    from deep_research.config import Settings
    from deep_research.observability import Tracer

    monkeypatch.setattr(llm_module.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(llm_module, "provider_request", _null_provider)
    client = llm_module.LLM(Settings(llm_api_key="k"), Tracer())
    calls = iter(outcomes)

    async def fake_stream_once(system, user, *, temperature=0.4):  # type: ignore[no-untyped-def]
        outcome = next(calls)
        for piece in outcome.get("yield", []):
            yield piece
        if "raise" in outcome:
            raise outcome["raise"]

    client._stream_once = fake_stream_once  # type: ignore[method-assign]
    return client


async def _no_sleep(_seconds: float) -> None:
    return None


class _null_provider:  # noqa: N801 - 作为 async context manager 工厂替身
    def __init__(self, *_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
        pass

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_exc) -> None:  # type: ignore[no-untyped-def]
        return None


def _collect(client) -> list[str]:  # type: ignore[no-untyped-def]
    async def run() -> list[str]:
        return [piece async for piece in client.stream("s", "u")]

    return asyncio.run(run())


def test_stream_retries_transient_failure_before_any_output(monkeypatch) -> None:
    client = _llm(monkeypatch, [{"raise": TimeoutError()}, {"yield": ["正文"]}])
    assert _collect(client) == ["正文"]


def test_stream_does_not_retry_after_partial_output(monkeypatch) -> None:
    # 已经产出过增量时重试会把半截正文重复拼接：必须把异常交给调用方
    client = _llm(monkeypatch, [{"yield": ["半截"], "raise": TimeoutError()}, {"yield": ["x"]}])
    with pytest.raises(TimeoutError):
        _collect(client)


def test_stream_does_not_retry_non_transient_errors(monkeypatch) -> None:
    client = _llm(monkeypatch, [{"raise": ValueError("bad request")}, {"yield": ["x"]}])
    with pytest.raises(ValueError):
        _collect(client)


def test_a_crashing_renderer_does_not_sink_the_whole_bundle(monkeypatch) -> None:
    """单个格式渲染崩溃：其余格式照常交付，失败格式记入验收门。"""
    from deep_research.config import Settings
    from deep_research.models import Report
    from deep_research.orchestrator import create_initial_execution
    from deep_research.persistence.repository import RunDetail
    from deep_research.workbench import publish

    def boom(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("docx engine crashed")

    monkeypatch.setattr("deep_research.workbench.delivery.docx.render_docx", boom)
    template = get_template("autoResearch")
    assert template is not None
    execution = create_initial_execution("q", template.workflow, Settings())
    contract = build_contract(template, "快照光谱成像调研")
    execution.checkpoint.setdefault("scratch", {})[CONTRACT_SCRATCH_KEY] = contract.model_dump(
        mode="json"
    )
    detail = RunDetail(
        id="r1",
        query="快照光谱成像调研",
        status="done",
        report=Report(
            query="q",
            markdown=(
                "## 摘要\n\n概述。\n\n## 分析\n\n发现X [1]。\n\n## 结论\n\n局限在于样本 [1]。\n"
            ),
            citations=["https://a.com"],
        ),
        results=_results(1),
        orchestration=execution,
    )
    bundle = publish.build_bundle(detail)
    formats = {file.format for file in bundle.files}
    assert "docx" not in formats and {"md", "html", "pdf"} <= formats
    render = next(g for g in bundle.gates if g.name == "render")
    assert render.status == "fail" and "docx engine crashed" in render.issues[0]
    assert bundle.status == "fail"
