from __future__ import annotations

import json

from deep_research.workbench import prose_review
from deep_research.workbench.prose_review import prose_units
from deep_research.workbench.support import SupportDecisions
from tests.test_prose_review import Judge, reviewer

BODY = (
    "## 结果\n\n表 1 机制对比\n\n|方法|结果|\n|---|---|\n|A|相关 [1]|\n\n"
    "表 2 阶段对比\n\n|阶段|结果|\n|---|---|\n|最后阶段|相关 [1]|\n\n"
    "表 1 的 A 行与表 2 的最后阶段行相符 [1]。\n\n"
    "## 其他结论\n\n固定说明 [1]。"
)


def test_internal_table_references_include_the_named_tables_and_preserve_source_scope():
    units, _ = prose_units(BODY, [1, 2])
    note = next(u for u in units if u.text.startswith("表 1 的 A"))
    assert "|A|相关 [1]|" in note.context and "|最后阶段|相关 [1]|" in note.context
    assert "不是来源论文中的表号或新增事实证据" in note.context
    assert note.citations == [1]  # Context never expands allowed original evidence.
    other = next(u for u in units if u.text == "固定说明 [1]。")
    assert "本报告内的表格" not in other.context


def test_duplicate_table_numbers_are_retained_as_ambiguous_not_silently_overwritten():
    body = BODY.replace("表 2 阶段对比", "表 1 阶段对比")
    tables = prose_review._report_tables(body)
    assert len(tables["1"]) == 2 and "2" not in tables


def test_caption_must_be_adjacent_to_an_actual_table_and_not_quoted_or_code():
    invalid = (
        "表 1 只是一句文字\n\n隔着另一个段落\n\n|X|\n|---|\n|A|\n\n"
        "```text\n表 2 代码\n|X|\n|---|\n|A|\n```\n\n"
        "> 表 3 引用材料\n>\n> |X|\n> |---|\n> |A|\n"
    )
    assert prose_review._report_tables(invalid) == {}
    titled = "**Table 4. Results**\n\n|X|\n|---|\n|A|\n\nTable 4 lists A."
    assert "4" in prose_review._report_tables(titled)
    assert "本报告内的表格" in prose_units(titled, [1])[0][-1].context


def test_formula_and_code_mentions_do_not_claim_internal_table_references():
    body = BODY + "\n\n`Table 1` is code.\n\n$$\\text{Table 1}$$\n\n```txt\nTable 1\n```"
    units, _ = prose_units(body, [1])
    assert all("本报告内的表格" not in u.context for u in units[-3:])


async def test_changing_a_table_invalidates_a_distant_reference_but_not_unrelated_content():
    judge = Judge()
    check = reviewer(judge)
    first = await check.review(BODY)
    judge.judged.clear()
    changed = BODY.replace("|A|相关 [1]|", "|A|另一种相关 [1]|")
    second = await check.review(changed)
    assert any(u["text"].startswith("表 1 的 A") for u in judge.judged)
    assert not any(u["text"] == "固定说明 [1]。" for u in judge.judged)
    assert first["input_hash"] != second["input_hash"]


async def test_old_record_without_table_scope_cannot_back_the_current_reference(monkeypatch):
    original = prose_review._report_tables
    monkeypatch.setattr(prose_review, "_report_tables", lambda _: {})
    check = reviewer()
    record = await check.review(BODY)
    monkeypatch.setattr(prose_review, "_report_tables", original)
    assert not check.check(BODY, record)[0]
    assert not check.prime(BODY, record)


async def test_own_table_context_cannot_supply_original_fact_evidence():
    class SourceCheckingJudge(Judge):
        async def parse(self, system, user, schema, **kwargs):
            if schema is not SupportDecisions:
                return await super().parse(system, user, schema, **kwargs)
            data = json.loads(user)
            decisions = []
            for unit in data["units"]:
                bad = "已经证明因果" in unit["text"]
                if bad and unit["text"].startswith("表 1"):
                    assert "本报告内的表格" in unit["context"]
                    assert "原文/作者" in unit["context"]
                decisions.append(
                    {
                        "unit_id": unit["id"],
                        "verdict": "unsupported" if bad else "non_factual",
                        "reason": "表格编排不能证明来源没有的因果关系" if bad else "fixture",
                    }
                )
            return SupportDecisions(decisions=decisions)

    check = reviewer(SourceCheckingJudge())
    record = await check.review(BODY + "\n\n表 1 已经证明因果 [1]。")
    assert record["status"] == "fail" and any("因果关系" in issue for issue in record["issues"])
