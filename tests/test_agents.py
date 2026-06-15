"""各 Agent 的单元测试：用 FakeLLM / FakeSearch 注入，无需密钥与网络。"""

from __future__ import annotations

import pytest

from deep_research.agents import Planner, Reflector, Researcher, Synthesizer
from deep_research.models import Finding, Reflection, Report, ResearchPlan, ResearchResult
from deep_research.observability import Tracer
from tests.fakes import FakeLLM, FakeSearch


@pytest.mark.asyncio
async def test_planner_builds_plan(settings):
    tracer = Tracer()
    plan = await Planner(FakeLLM(), tracer, settings).run("测试问题")
    assert isinstance(plan, ResearchPlan)
    assert len(plan.sub_questions) >= 1
    assert any(e.stage == "PLANNER" for e in tracer.events)


@pytest.mark.asyncio
async def test_planner_truncates_to_limit(settings):
    settings.max_sub_questions = 1  # FakeLLM 返回 2 个，应被截断到 1
    plan = await Planner(FakeLLM(), Tracer(), settings).run("测试问题")
    assert len(plan.sub_questions) == 1


@pytest.mark.asyncio
async def test_researcher_keeps_only_grounded_findings(settings):
    researcher = Researcher(FakeLLM(), FakeSearch(), Tracer(), settings)
    result = await researcher.run("子问题A")
    assert result is not None
    assert result.findings  # FakeLLM 的发现引用 a.com，在 FakeSearch 来源内，应保留
    assert all(f.source_url in {"https://a.com", "https://b.com"} for f in result.findings)


@pytest.mark.asyncio
async def test_reflector_reports_sufficiency(settings):
    results = [
        ResearchResult(
            sub_question="A",
            findings=[Finding(statement="s", source_url="https://a.com", confidence=0.9)],
        )
    ]
    reflection = await Reflector(FakeLLM(), Tracer(), settings).run("测试问题", results)
    assert isinstance(reflection, Reflection)
    assert reflection.is_sufficient is True  # FakeLLM 预设充分


@pytest.mark.asyncio
async def test_synthesizer_appends_citation_list(settings):
    results = [
        ResearchResult(
            sub_question="A",
            findings=[Finding(statement="发现X", source_url="https://a.com", confidence=0.9)],
        )
    ]
    report = await Synthesizer(FakeLLM(), Tracer(), settings).run("测试问题", results)
    assert isinstance(report, Report)
    assert "## 参考来源" in report.markdown
    assert report.citations == ["https://a.com"]
