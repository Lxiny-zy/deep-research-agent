import json

import pytest

from deep_research.workbench.paper_abstract import prepare_abstracts
from deep_research.workbench.prose_edit import ProseEdits, repair_paragraphs
from deep_research.workbench.prose_review import ProseReviewer
from deep_research.workbench.support import SupportDecisions
from tests.fakes import FakeLLM
from tests.test_paper_abstract import results, scratch

GOOD = "## 摘要翻译\n\n该方法使用 17 个样本。\n\n指标提高 0.8 dB，但仅适用于仿真。"
BAD = "## 摘要翻译\n\n错误译文使用了 17 个样本。"
REST = "\n\n## 方法\n\n正文方法使用 900 个样本 [1]。\n\n## 局限\n\n后续仍需独立验证 [1]。\n"


class Editor(FakeLLM):
    def __init__(self):
        super().__init__()
        self.prompts = []
        self.replacement = GOOD

    async def parse(self, system, user, schema, **kwargs):
        if schema is ProseEdits:
            evidence = json.loads(
                user.split("【已核验证据】\n", 1)[1].split("\n\n【只修订以下段落】", 1)[0]
            )
            payload = json.loads(user.split("【只修订以下段落】\n", 1)[1])
            self.prompts.append((evidence, payload))
            return ProseEdits(
                edits=[
                    {
                        "unit_id": part["unit_id"],
                        "replacement": self.replacement
                        if part["structure"] == "translation"
                        else "正文方法使用 900 个样本 [1]。",
                    }
                    for part in payload["paragraphs"]
                ]
            )
        if schema is SupportDecisions:
            payload = json.loads(user)
            return SupportDecisions(
                decisions=[
                    {
                        "unit_id": unit["id"],
                        "verdict": "unsupported" if "错误" in unit["text"] else "supported",
                        "evidence_ids": [
                            item["id"]
                            for item in payload["evidence"]
                            if item["citation"] in unit["citations"]
                        ],
                        "reason": "完整译文缺少原文条件"
                        if "错误" in unit["text"]
                        else "fixed judgement",
                    }
                    for unit in payload["units"]
                ]
            )
        return await super().parse(system, user, schema, **kwargs)


async def reviewer(model):
    state = scratch()
    abstracts = await prepare_abstracts(state, screen_intent=False)
    return ProseReviewer.research(
        model, results(), {"https://example.org/paper": 1}, 50000, abstracts=abstracts
    )


async def test_complete_abstract_is_locally_replaced_and_other_sections_stay_identical():
    model = Editor()
    checker = await reviewer(model)
    body = BAD + REST
    record = await checker.review(body)
    assert record["status"] == "fail"
    edited = await repair_paragraphs(model, checker, body, record)
    assert edited == GOOD + REST
    assert (await checker.review(edited))["status"] == "pass"
    assert all(item["citation"] < 0 for item in model.prompts[0][0])


async def test_abstract_and_body_edits_have_separate_evidence_inputs():
    model = Editor()
    checker = await reviewer(model)
    body = BAD + REST.replace("正文方法使用", "错误正文使用")
    record = await checker.review(body)
    edited = await repair_paragraphs(model, checker, body, record)
    assert edited == GOOD + REST
    assert len(model.prompts) == 2
    for evidence, payload in model.prompts:
        translation = payload["paragraphs"][0]["structure"] == "translation"
        assert all((item["citation"] < 0) == translation for item in evidence)


@pytest.mark.parametrize(
    "replacement",
    [
        GOOD.replace("摘要翻译", "新增章节"),
        GOOD + "\n\n## 新增结论\n补充内容",
        GOOD + " [1]",
        GOOD + " [-1]",
    ],
)
async def test_translation_patch_cannot_change_headings_or_print_internal_citations(replacement):
    model = Editor()
    model.replacement = replacement
    checker = await reviewer(model)
    record = await checker.review(BAD + REST)
    with pytest.raises(ValueError):
        await repair_paragraphs(model, checker, BAD + REST, record)


async def test_disjoint_abstract_sections_never_delete_intervening_body():
    model = Editor()
    checker = await reviewer(model)
    body = BAD + REST + "\n## 摘要译文\n另一个错误译文。"
    record = await checker.review(body)
    assert await repair_paragraphs(model, checker, body, record) is None
    assert not model.prompts


async def test_paper_reader_uses_local_abstract_edit_instead_of_rewriting_body(settings):
    from dataclasses import replace

    from deep_research.agents.base import Blackboard, RunContext
    from deep_research.observability import Tracer
    from deep_research.workbench.quality import QualityPolicy
    from deep_research.workbench.templates import get_template
    from deep_research.workbench.writers import PaperReader
    from tests.fakes import FakeSearch

    class Writer(Editor):
        async def stream(self, system, user, **kwargs):
            self.stream_calls += 1
            yield BAD + REST

    model = Writer()
    state = scratch()
    await prepare_abstracts(state, screen_intent=False)
    checker = await reviewer(model)
    bb = Blackboard(query="q", results=results(), scratch=state)
    ctx = RunContext(llm=model, search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    body, log = await PaperReader()._write_checked(
        bb,
        ctx,
        replace(get_template("paperRead"), sections=()),
        None,
        "body evidence",
        {"https://example.org/paper": 1},
        policy=QualityPolicy(max_revisions=1, register_check=False, require_limitations=False),
        min_citations=1,
        require_corroboration=False,
        reviewer=checker,
    )
    assert body == GOOD + REST and not log.remaining
    assert model.stream_calls == 1 and len(model.prompts) == 1
