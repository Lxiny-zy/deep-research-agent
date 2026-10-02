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
