"""Call-count and delivery-boundary regressions for non-QA task optimization."""

import asyncio
import json

import pytest

from deep_research.artifacts import ArtifactStore
from deep_research.models import Report, ResearchResult, Source
from deep_research.report.validation import finalize_report, validate_body
from deep_research.workbench.contract import CONTRACT_SCRATCH_KEY, build_contract
from deep_research.workbench.fulltext_review import FullTextReviewer, FullTextTargets
from deep_research.workbench.prose_review import prose_units
from deep_research.workbench.quality import writer_policy
from deep_research.workbench.revision import Assessment, write_with_revisions
from deep_research.workbench.slide_content import Slide, SlideDeck, deck_to_markdown
from deep_research.workbench.slide_edit import SlideTextEdit, SlideTextEdits, repair_slides
from deep_research.workbench.support import SupportReviewer, SupportUnit
from deep_research.workbench.templates import get_template
from deep_research.workbench.writing_progress import WritingProgress
from tests.fakes import verified_finding
from tests.test_fulltext_absence import FullTextJudge, make_corpus


async def test_unchanged_issues_stop_revisions_and_remain_stopped_after_recovery(tmp_path):
    calls = 0
    store = ArtifactStore(tmp_path)

    async def write(revision):
        nonlocal calls
        calls += 1
        return f"draft version {calls}"

    def assess(body):
        return Assessment(hard=["same unsupported claim"])

    _, log = await write_with_revisions(
        write, assess, max_revisions=4, progress=WritingProgress(store, {"task": "same"})
    )
    assert calls == 2 and log.stop_reason == "no_progress" and log.revisions_used == 1
    _, recovered = await write_with_revisions(
        write, assess, max_revisions=4, progress=WritingProgress(store, {"task": "same"})
    )
    assert calls == 2 and recovered.stop_reason == "no_progress"


async def test_failed_revision_uses_a_slot_without_discarding_the_saved_draft(tmp_path):
    calls = 0

    async def write(revision):
        nonlocal calls
        calls += 1
        if revision:
            raise ValueError("controlled edit failure")
        return "original"

    progress = WritingProgress(ArtifactStore(tmp_path), {"task": "failed"})
    body, log = await write_with_revisions(
        write, lambda _: Assessment(hard=["unsupported"]), max_revisions=2, progress=progress
    )
    assert body == "original" and log.revisions_used == 1 and log.stop_reason == "revision_failed"
    await write_with_revisions(
        write, lambda _: Assessment(hard=["unsupported"]), max_revisions=2,
        progress=WritingProgress(ArtifactStore(tmp_path), {"task": "failed"}),
    )
    assert calls == 2


async def test_zero_automatic_revisions_also_disables_completion_repair(settings):
    from tests.test_completion_auto_repair import run_fixture

    repo, run_id, model, _ = await run_fixture(settings, max_revisions=0)
    detail = await repo.get_run(run_id)
    content = detail.orchestration.checkpoint["scratch"]["_completion_repair"]["content"]
    assert model.stream_calls == 1 and detail.status == "needs_review"
    assert content["attempts"] == 0 and content["status"] == "skipped"


async def test_six_pure_suggestions_use_two_calls_then_reuse_the_same_records():
    _, corpus = make_corpus(complete=True)
    model = FullTextJudge(kind="not_applicable")
    checker = SupportReviewer(model, [], 20000, fulltext_corpus=corpus)
    units = [
        SupportUnit(str(index), f"建议进一步评估{topic}。", kind="prose", context="不足")
        for index, topic in enumerate([
            "效率", "鲁棒性", "跨域能力", "解释性", "部署成本", "用户体验",
        ])
    ]
    decisions = await checker.review(units)
    assert all(item.verdict == "non_factual" for item in decisions)
    assert model.calls == 2 and not model.parts
    await checker.review(units)
    assert model.calls == 2


async def test_invalid_target_batch_does_not_fan_out_or_exempt_assertions():
    class Broken(FullTextJudge):
        async def parse(self, system, user, schema, **kwargs):
            response = await super().parse(system, user, schema, **kwargs)
            if schema is FullTextTargets:
                response.targets[1].unit_id = response.targets[0].unit_id
            return response

    _, corpus = make_corpus(complete=True)
    model = Broken(kind="absence")
    checker = SupportReviewer(model, [], 20000, fulltext_corpus=corpus)
    decisions = await checker.review([
        SupportUnit("a", "全文未报告优化设置。", context="不足"),
        SupportUnit("b", "全文未报告实验条件。", context="不足"),
    ])
    assert model.calls == 1 and not model.parts
    assert all(item.verdict == "uncertain" for item in decisions)


async def test_bad_target_rows_do_not_discard_valid_independent_batch_items():
    class Partial(FullTextJudge):
        async def parse(self, system, user, schema, **kwargs):
            response = await super().parse(system, user, schema, **kwargs)
            if schema is FullTextTargets:
                response.targets[-1].unit_id = response.targets[-2].unit_id
            return response

    _, corpus = make_corpus(complete=True)
    model = Partial(kind="not_applicable")
    checker = SupportReviewer(model, [], 20000, fulltext_corpus=corpus)
    units = [
        SupportUnit(str(i), "建议增加跨域实验。", context="不足", kind="prose")
        for i in range(3)
    ]
    decisions = await checker.review(units)
    assert decisions[0].verdict == "non_factual"
    assert all(item.verdict == "uncertain" for item in decisions[1:])
    assert model.calls == 2 and not model.parts


async def test_batched_targets_cannot_borrow_another_units_document():
    from deep_research.document_corpus import FullTextCorpus

    sources = [Source(url=f"https://example.org/{name}.pdf", title=name, content="Original text.")
               for name in ["alpha", "beta"]]
    corpus = FullTextCorpus(sources, {source.url: i + 1 for i, source in enumerate(sources)})

    class WrongDocument:
        calls = 0

        async def parse(self, system, user, schema, **kwargs):
            self.calls += 1
            assert schema is FullTextTargets
            items = json.loads(user)["units"]
            return schema.model_validate({"targets": [
                {"unit_id": item["unit"]["id"], "target": {
                    "kind": "absence", "document_ids": [items[1 - index]["documents"][0]["id"]],
                    "reason": "wrong scope",
                }} for index, item in enumerate(items)
            ]})

    model = WrongDocument()
    checker = FullTextReviewer(model, corpus, 20000)
    units = [SupportUnit(str(i), "全文未报告测试条件。", citations=[i + 1]) for i in range(2)]
    await checker.prepare(units)
    records = await asyncio.gather(*(checker.review(unit) for unit in units))
    assert model.calls == 1 and all(record["status"] == "uncertain" for record in records)


def test_isolated_bad_citation_preserves_valid_report_sections():
    results = [ResearchResult(sub_question="q", findings=[verified_finding()])]
    body = "## 分析\n\n发现X [1]。\n\n坏段落 [99]。\n\n## 结论\n\n发现X [1]。"
    final, check = finalize_report(
        Report(query="q", markdown=body, citations=["https://a.com"]), results, preserve_valid=True
    )
    assert check.partial and check.issues == ("invalid_citation",)
    assert "## 分析" in final.markdown and "## 结论" in final.markdown
    assert "坏段落" not in final.markdown
    assert not validate_body(final.markdown, results, {"https://a.com": 1}, fallback=False).issues


def test_unlocalizable_citation_failure_still_uses_verified_material_only():
    results = [ResearchResult(sub_question="q", findings=[verified_finding()])]
    final, check = finalize_report(
        Report(query="q", markdown="## 错误标题 [99]\n\n发现X [1]。", citations=["https://a.com"]),
        results, preserve_valid=True,
    )
    assert not check.partial and "已验证素材摘要" in final.markdown


@pytest.mark.parametrize("page", [1, 2])
async def test_slide_edit_is_confined_to_the_failed_page(page):
    deck = SlideDeck(title="演示", slides=[
        Slide(
            title="方法", bullets=["BAD [1]。", "发现X [1]。"],
            notes="方法说明 [1]。", citations=[1],
        ),
        Slide(title="结果", bullets=["发现X [1]。"], notes="独立的结果说明 [1]。", citations=[1]),
    ])
    original = deck.model_dump(mode="json")
    markdown = deck_to_markdown(deck)
    units, locations = prose_units(markdown, [1])
    audit = {"units": locations, "decisions": [
        {"unit_id": unit.id, "verdict": "unsupported" if "BAD" in unit.text else "supported",
         "reason": "incorrect claim"} for unit in units
    ]}

    class Model:
        calls = 0

        async def parse(self, system, user, schema, **kwargs):
            self.calls += 1
            assert schema is SlideTextEdits
            return SlideTextEdits(edits=[SlideTextEdit(
                page=page, title="方法", bullets=["发现X [1]。", "发现X [1]。"],
                notes="修订后的方法说明 [1]。", citations=[1],
            )])

    model = Model()
    if page == 2:
        with pytest.raises(ValueError, match="未授权页面"):
            await repair_slides(model, deck, markdown, audit, [], [], 20000)
    else:
        repaired = await repair_slides(model, deck, markdown, audit, [], [], 20000)
        assert repaired.slides[1].model_dump(mode="json") == original["slides"][1]
        assert "BAD" not in deck_to_markdown(repaired)
    assert deck.model_dump(mode="json") == original and model.calls == 1


def test_light_tasks_adjust_revision_allowance_without_changing_old_checkpoints():
    contract = build_contract(
        get_template("mindmap"), "概念图", tier="light", quality={"max_revisions": 2}
    )
    scratch = {CONTRACT_SCRATCH_KEY: contract.model_dump(mode="json")}
    assert writer_policy(contract.quality, scratch).max_revisions == 1
    old_quality = {
        key: value for key, value in contract.quality.items() if key != "adaptive_review"
    }
    assert writer_policy(old_quality, scratch).max_revisions == 2
    assert writer_policy({**contract.quality, "max_revisions": 0}, scratch).max_revisions == 0
    strict = build_contract(
        get_template("peerReview"), "论文", tier="light", quality={"max_revisions": 2}
    )
    policy = writer_policy(strict.quality, {CONTRACT_SCRATCH_KEY: strict.model_dump(mode="json")})
    assert policy.max_revisions == 2


async def test_new_quality_defaults_do_not_replay_frozen_writing_progress(settings, tmp_path):
    from dataclasses import replace

    from deep_research.agents.base import Blackboard, RunContext
    from deep_research.checkpoints import RUN_SETTINGS_KEY
    from deep_research.observability import Tracer
    from deep_research.workbench.quality import QualityPolicy
    from deep_research.workbench.writing_progress import for_writer
    from tests.fakes import FakeLLM, FakeSearch

    legacy = QualityPolicy().model_dump(exclude={"adaptive_review", "task_max_model_calls"})
    bb = Blackboard(query="q", scratch={RUN_SETTINGS_KEY: {"quality": legacy}})
    settings.quality = legacy
    ctx = RunContext(
        llm=FakeLLM(), search_tool=FakeSearch(), tracer=Tracer(), settings=settings,
        artifact_store=ArtifactStore(tmp_path), run_id="legacy",
    )
    calls = 0

    async def write(revision):
        nonlocal calls
        calls += 1
        return "saved draft"

    first = for_writer(bb, ctx, "research_writer", "system")
    await write_with_revisions(write, lambda _: Assessment(), max_revisions=2, progress=first)
    ctx.settings = replace(settings, quality=legacy)
    assert "adaptive_review" in ctx.settings.quality  # Settings normalization adds new defaults.
    restored = for_writer(bb, ctx, "research_writer", "system")
    await write_with_revisions(write, lambda _: Assessment(), max_revisions=2, progress=restored)
    assert first.path(2) == restored.path(2) and calls == 1


async def test_partial_report_remains_marked_for_review_through_task_delivery(
    settings, monkeypatch,
):
    from tests import test_completion_auto_repair as completion

    class BadCitation(completion.RepairModel):
        async def stream(self, *args, **kwargs):
            self.stream_calls += 1
            yield self.bodies[0] + "\n\n单独的错误段落 [99]。"

    monkeypatch.setattr(completion, "RepairModel", BadCitation)
    repo, run_id, model, _ = await completion.run_fixture(settings, broken=False)
    detail = await repo.get_run(run_id)
    scratch = detail.orchestration.checkpoint["scratch"]
    assert model.stream_calls == 1 and detail.status == "needs_review"
    assert "## 分析" in detail.report.markdown and "[99]" not in detail.report.markdown
    assert scratch["_report_validation"]["partial"]
    assert "[99]" in scratch["workbench"]["extras"]["unapproved_draft"]
