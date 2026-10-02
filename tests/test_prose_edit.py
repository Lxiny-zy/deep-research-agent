from __future__ import annotations

import json

import pytest

from deep_research.models import ResearchResult
from deep_research.observability import Tracer
from deep_research.workbench.prose_edit import ProseEdits, repair_paragraphs
from deep_research.workbench.prose_review import ProseReviewer
from deep_research.workbench.support import SupportDecisions
from tests.fakes import FakeLLM, verified_finding


class Judge(FakeLLM):
    def __init__(self):
        super().__init__()
        self.checked = []
        self.mode = "valid"

    async def parse(self, system, user, schema, **kwargs):
        if schema is ProseEdits:
            data = json.loads(user.split("【只修订以下段落】\n", 1)[1])
            unit = data["paragraphs"][0]
            replacement = {
                "valid": "只有相关关系 [1]。",
                "unchanged": unit["text"],
                "heading": "## New section",
                "empty": "",
            }.get(self.mode, "固定")
            return ProseEdits(
                edits=[
                    {
                        "unit_id": "foreign" if self.mode == "foreign" else unit["unit_id"],
                        "replacement": replacement,
                    }
                ]
            )
        if schema is SupportDecisions:
            data = json.loads(user)
            self.checked.extend(u["text"] for u in data["units"])
            return SupportDecisions(
                decisions=[
                    {
                        "unit_id": u["id"],
                        "verdict": "unsupported" if "因果" in u["text"] else "supported",
                        "evidence_ids": [
                            e["id"] for e in data["evidence"] if e["citation"] in u["citations"]
                        ],
                        "reason": "仅有相关证据" if "因果" in u["text"] else "fixture",
                    }
                    for u in data["units"]
                ]
            )
        return await super().parse(system, user, schema, **kwargs)


def reviewer(llm):
    return ProseReviewer.research(
        llm,
        [ResearchResult(sub_question="q", findings=[verified_finding()])],
        {"https://a.com": 1},
        50000,
    )


BODY = "## 结果\n\n已核验的固定段落 [1]。\n\n已经证明因果 [1]。\n\n后续固定段落 [1]。\n"


async def test_resume_repairs_only_rejected_paragraph_and_reuses_bound_checks():
    llm = Judge()
    first = reviewer(llm)
    record = await first.review(BODY)
    assert record["status"] == "fail"
    resumed = reviewer(llm)
    assert resumed.prime(BODY, record)
    patched = await repair_paragraphs(llm, resumed, BODY, record)
    assert patched == BODY.replace("已经证明因果 [1]。", "只有相关关系 [1]。")
    llm.checked.clear()
    final = await resumed.review(patched)
    assert final["status"] == "pass"
    assert set(llm.checked) == {"只有相关关系 [1]。", "后续固定段落 [1]。"}


@pytest.mark.parametrize("mode", ["foreign", "unchanged", "heading", "empty"])
async def test_invalid_patch_does_not_modify_or_reroll_the_old_draft(mode):
    llm = Judge()
    check = reviewer(llm)
    record = await check.review(BODY)
    llm.mode = mode
    with pytest.raises(ValueError):
        await repair_paragraphs(llm, check, BODY, record)
    assert not check.prime(BODY + "changed", record)


def test_tracer_keeps_replace_between_accumulated_token_batches():
    tracer = Tracer()
    delivered = []
    tracer.add_sink(delivered.append)
    tracer.emit("SYNTHESIZER", "token", data={"delta": "old"})
    tracer.emit("SYNTHESIZER", "token", data={"delta": "new", "replace": True})
    tracer.emit("SYNTHESIZER", "token", data={"delta": " tail"})
    tracer.flush_tokens()
    assert [e.data for e in delivered] == [
        {"delta": "old"},
        {"delta": "new", "replace": True},
        {"delta": " tail"},
    ]


class StructuredJudge(Judge):
    def __init__(self):
        super().__init__()
        self.edits = []
        self.transform = lambda text: text.replace("已经证明因果", "只有相关关系")

    async def parse(self, system, user, schema, **kwargs):
        if schema is ProseEdits:
            data = json.loads(user.split("【只修订以下段落】\n", 1)[1])
            self.edits.extend(data["paragraphs"])
            return ProseEdits(
                edits=[
                    {"unit_id": u["unit_id"], "replacement": self.transform(u["text"])}
                    for u in data["paragraphs"]
                ]
            )
        result = await super().parse(system, user, schema, **kwargs)
        if schema is SupportDecisions:
            data = json.loads(user)
            by_id = {u["id"]: u for u in data["units"]}
            for decision in result.decisions:
                text = by_id[decision.unit_id]["text"]
                if text == "|项目|结论|":
                    decision.verdict = "non_factual"
                elif "无引用事实" in text:
                    decision.verdict, decision.reason = "unsupported", "没有引用角标"
        return result


@pytest.mark.parametrize(
    "body",
    [
        "## 结果\n\n- 固定 [1]。\n  - 已经证明因果 [1]。\n- 后续固定 [1]。\n",
        "## 结果\n\n7. 已经证明因果 [1]。\n8. 后续固定 [1]。\n",
        "## 结果\n\n|项目|结论|\n|---|---|\n|A|已经证明因果 [1]|\n|B|固定 [1]|\n",
    ],
)
async def test_list_items_and_table_rows_preserve_untouched_bytes_and_structure(body):
    llm = StructuredJudge()
    check = reviewer(llm)
    record = await check.review(body)
    patched = await repair_paragraphs(llm, check, body, record)
    assert patched == body.replace("已经证明因果", "只有相关关系")
    assert len(llm.edits) == 1
    assert (await check.review(patched))["status"] == "pass"


@pytest.mark.parametrize(
    ("body", "replacement"),
    [
        ("7. 已经证明因果 [1]。", "8. 只有相关关系 [1]。"),
        ("- 已经证明因果 [1]。", "- 只有相关关系 [1]。\n  - 新条目 [1]。"),
        ("- 已经证明因果 [1]。", "- ## 新标题"),
        (
            "|项目|结论|\n|---|---|\n|A|已经证明因果 [1]|",
            "|A|只有相关关系 [1]|新列|",
        ),
    ],
)
async def test_patch_cannot_change_list_numbering_or_table_columns(body, replacement):
    llm = StructuredJudge()
    llm.transform = lambda _: replacement
    check = reviewer(llm)
    record = await check.review(body)
    with pytest.raises(ValueError, match="结构"):
        await repair_paragraphs(llm, check, body, record)


async def test_missing_citation_can_use_retained_evidence_without_rewriting_other_paragraphs():
    body = "固定段落 [1]。\n\n无引用事实。\n\n后续段落 [1]。"
    llm = StructuredJudge()
    llm.transform = lambda _: "只有相关关系 [1]。"
    check = reviewer(llm)
    record = await check.review(body)
    patched = await repair_paragraphs(
        llm, check, body, record, local_problems=[("无引用事实。", "没有任何引用角标")]
    )
    assert patched == body.replace("无引用事实。", "只有相关关系 [1]。")
    assert (await check.review(patched))["status"] == "pass"


async def test_deterministic_style_issue_targets_a_previously_supported_list_item():
    body = "- 其实，只有相关关系 [1]。\n- 固定段落 [1]。"
    llm = StructuredJudge()
    llm.transform = lambda text: text.replace("其实，", "")
    check = reviewer(llm)
    record = await check.review(body)
    assert record["status"] == "pass"
    patched = await repair_paragraphs(
        llm, check, body, record, local_problems=[("其实，只有相关关系 [1]。", "口语化措辞")]
    )
    assert patched == body.replace("其实，", "")
    assert len(llm.edits) == 1


async def test_ambiguous_mechanical_excerpt_does_not_edit_the_wrong_unit():
    body = "## 第一处\n\n固定句子 [1]。\n\n## 第二处\n\n固定句子 [1]。"
    llm = StructuredJudge()
    check = reviewer(llm)
    record = await check.review(body)
    assert (
        await repair_paragraphs(llm, check, body, record, local_problems=[("固定句子", "需修订")])
        is None
    )
    assert not llm.edits


async def test_indented_code_is_not_rewritten_as_an_ordinary_paragraph():
    body = "## 代码\n\n    已经证明因果\n"
    llm = StructuredJudge()
    check = reviewer(llm)
    record = await check.review(body)
    assert await repair_paragraphs(llm, check, body, record) is None
    assert not llm.edits


async def test_writer_uses_local_edits_for_combined_style_and_support_issues(settings):
    from deep_research.agents.base import Blackboard, RunContext
    from deep_research.workbench.quality import QualityPolicy
    from deep_research.workbench.revision import assess_draft
    from deep_research.workbench.templates import AUTO_RESEARCH
    from deep_research.workbench.writers import ResearchWriter, eligible_material
    from tests.fakes import FakeSearch

    body = (
        "## 摘要\n\n变量相关。\n\n## 分析\n\n"
        "- 其实，已经证明因果 [1]。\n- 原有固定论述 [1]。\n\n"
        "## 结论\n\n局限需要进一步讨论 [1]。"
    )

    class Writer(StructuredJudge):
        async def stream(self, *args, **kwargs):
            self.stream_calls += 1
            yield body

    llm = Writer()
    llm.transform = lambda text: text.replace("其实，", "").replace("已经证明因果", "只有相关关系")
    evidence = [ResearchResult(sub_question="q", findings=[verified_finding()])]
    material, mapping = eligible_material(evidence)
    policy = QualityPolicy()
    bb = Blackboard(query="解释相关关系", results=evidence)
    ctx = RunContext(llm=llm, search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    patched, log = await ResearchWriter()._write_checked(
        bb,
        ctx,
        AUTO_RESEARCH,
        None,
        material,
        mapping,
        policy=policy,
        min_citations=1,
        require_corroboration=False,
    )
    assert patched == body.replace("其实，", "").replace("已经证明因果", "只有相关关系")
    assert llm.stream_calls == 1 and len(llm.edits) == 1 and not log.remaining
    global_issue = assess_draft(
        "缺少所有章节 [1]",
        template=AUTO_RESEARCH,
        query=bb.query,
        results=evidence,
        url_to_idx=mapping,
        policy=policy,
        min_citations=1,
    )
    assert global_issue.hard and global_issue.local_problems is None
