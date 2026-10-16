"""Isolate generation/repair routing from semantic-model accuracy."""

import pytest

from deep_research.agents.base import Blackboard, RunContext
from deep_research.observability import Tracer
from deep_research.workbench import analysis, prose_edit, prose_review
from deep_research.workbench.contract import CONTRACT_SCRATCH_KEY, build_contract
from deep_research.workbench.templates import get_template
from tests.fakes import FakeLLM, FakeSearch


class PassingReviewer:
    def __init__(self):
        self.records = {}

    async def review(self, body):
        return self.records.setdefault(body, {
            "status": "pass", "issues": [], "can_revise": True,
            "requirements_review": {"status": "pass", "issues": [], "can_revise": True},
        })

    def cached_review(self, body):
        return self.records.get(body)


async def run_analysis(settings, monkeypatch, *, repeated=False, bad_number=False):
    csv = "subject,score\na,1\na,2\nb,3\nb,4\n" if repeated else "score\n1\n2\n3\n4\n"
    query = "只做逐行描述统计，不做推断检验。"
    scope = {"measures": ["score"]}
    if repeated:
        scope.update(background=["subject"], subject_columns=["subject"])
    stats = analysis.analyse(csv, query, scope=scope)
    template = get_template("dataAnalysis")
    sections = []
    for section in template.sections:
        text = "本段保留描述性分析说明。"
        if section.key == "statistics":
            text = "score 的均值为 " + ("999" if bad_number else "2.5") + "。"
        sections.append(f"## {section.title}\n\n{text}")
    body = "\n\n".join(sections)
    contract = build_contract(template, query, attachments_csv=csv, quality=settings.quality)
    bb = Blackboard(query=query, scratch={
        CONTRACT_SCRATCH_KEY: contract.model_dump(mode="json"),
        analysis.ANALYSIS_SCRATCH_KEY: stats.snapshot(),
    })

    class Model(FakeLLM):
        async def stream(self, *args, **kwargs):
            self.stream_calls += 1
            yield body

    model = Model()
    monkeypatch.setattr(prose_review, "reviewer_for_report", lambda *a, **k: PassingReviewer())
    ctx = RunContext(llm=model, search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    await analysis.DataAnalyst().step(bb, ctx)
    return bb, model


async def test_repeated_observations_keep_valid_prose_and_mandatory_inference_limits(
    settings, monkeypatch,
):
    settings.quality = {"max_revisions": 0, "register_check": False}
    bb, model = await run_analysis(settings, monkeypatch, repeated=True)
    assert model.stream_calls == 1
    assert "本段保留描述性分析说明" in bb.report.markdown
    assert "检测到重复测量" in bb.report.markdown
    assert "未改用独立样本检验" in bb.report.markdown
    assert not bb.scratch["_report_validation"]["fallback"]
    assert not bb.scratch["workbench"]["extras"]["revision"]["remaining"]


@pytest.mark.parametrize("fails", [False, True])
async def test_statistical_number_repair_is_local_and_never_starts_another_full_write(
    settings, monkeypatch, fails,
):
    settings.quality = {"max_revisions": 2, "register_check": False}
    edits = []

    async def edit(llm, reviewer, body, audit, *, local_problems):
        edits.append(local_problems)
        assert any("999" in paragraph for paragraph, _ in local_problems)
        if fails:
            raise ValueError("controlled edit failure")
        return body.replace("999", "2.5")

    monkeypatch.setattr(prose_edit, "repair_paragraphs", edit)
    bb, model = await run_analysis(settings, monkeypatch, bad_number=True)
    assert model.stream_calls == 1 and len(edits) == 1
    assert "999" not in bb.report.markdown
    revision = bb.scratch["workbench"]["extras"]["revision"]
    assert revision["stop_reason"] == ("revision_failed" if fails else "passed")
    assert bb.scratch["_report_validation"]["fallback"] is fails
