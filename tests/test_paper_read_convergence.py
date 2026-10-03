from __future__ import annotations

import pytest

from deep_research.workbench.paper_evidence import PaperEvidenceSelection
from deep_research.workbench.qa import answer_question
from deep_research.workbench.qa_cache import PaperEvidenceCache
from tests.test_paper_evidence_reuse import scoped_setup


def read_figure():
    return PaperEvidenceSelection(
        sufficient=False,
        finding_ids=["e1"],
        missing_topics=["模拟噪声条件"],
        source_urls=["https://paper.test/figure"],
    )


def retain_gap():
    return PaperEvidenceSelection(
        sufficient=False,
        finding_ids=["e1", "e2"],
        missing_topics=["模拟噪声条件"],
        next_step="answer_with_gaps",
        reading_reason="已经查阅相关实验章节，目录中没有必要的新目标",
    )


async def test_repeated_target_replans_without_expanding_and_cache_preserves_gaps(settings):
    sources, finding, llm, ctx = await scoped_setup(
        settings, [read_figure(), read_figure(), retain_gap()]
    )
    users = []

    async def stream(system, user, **kwargs):
        users.append(user)
        yield "发现X [1]。"

    llm.stream = stream
    cache = PaperEvidenceCache()
    kwargs = dict(
        history=[], ctx=ctx, paper_sources=sources, paper_evidence=[finding], paper_cache=cache
    )
    first = await answer_question("模拟和真实的条件", **kwargs)
    assert llm.reads == [[sources[1].url]]
    assert len(llm.selection_prompts) == 3 and "补读计划需纠正" in llm.selection_prompts[-1]
    assert first.unresolved_topics == ["模拟噪声条件"]
    first.unresolved_topics.append("caller mutation")
    second = await answer_question("模拟和真实的条件", **kwargs)
    assert llm.reads == [[sources[1].url]] and len(llm.selection_prompts) == 3
    assert second.unresolved_topics == ["模拟噪声条件"]
    assert all("读取后仍待确认的方面" in user and "模拟噪声条件" in user for user in users)
    assert all("caller mutation" not in user for user in users)
    assert any(
        t["tool"] == "evidence_coverage" and t["status"] == "partial" for t in second.thoughts
    )


async def test_repeated_invalid_plan_never_reads_unrelated_remaining_material(settings):
    sources, finding, llm, ctx = await scoped_setup(
        settings, [read_figure(), read_figure(), read_figure()]
    )
    with pytest.raises(ValueError, match="未自动扩大读取"):
        await answer_question(
            "模拟条件", history=[], ctx=ctx, paper_sources=sources, paper_evidence=[finding]
        )
    assert llm.reads == [[sources[1].url]]


async def test_explicitly_justified_wider_read_preserves_the_complete_scope(settings):
    plans = [
        read_figure(),
        PaperEvidenceSelection(
            sufficient=False,
            finding_ids=["e2"],
            next_step="scan_remaining",
            reading_reason="相关实验参数还可能在方法与附录中，需要查阅",
        ),
        PaperEvidenceSelection(sufficient=True, finding_ids=["e1", "e2", "e3"]),
    ]
    sources, finding, llm, ctx = await scoped_setup(settings, plans)
    answer = await answer_question(
        "完整实验设置", history=[], ctx=ctx, paper_sources=sources, paper_evidence=[finding]
    )
    assert llm.reads == [[sources[1].url], [sources[0].url, sources[2].url]]
    assert not answer.unresolved_topics and len(answer.findings) == 3


async def test_gap_answer_cannot_skip_initial_targeted_read(settings):
    early = retain_gap().model_copy(update={"finding_ids": ["e1"]})
    sources, finding, llm, ctx = await scoped_setup(settings, [early, read_figure(), retain_gap()])
    result = await answer_question(
        "模拟条件", history=[], ctx=ctx, paper_sources=sources, paper_evidence=[finding]
    )
    assert llm.reads == [[sources[1].url]] and result.unresolved_topics
    assert "保留待确认项前" in llm.selection_prompts[1]


@pytest.mark.parametrize("enforced", [None, 1000])
async def test_small_planning_heuristic_never_silently_triggers_full_read(settings, enforced):
    from deep_research.agents.researcher import Researcher
    from deep_research.workbench.paper_evidence import plan_findings
    from tests.test_paper_evidence_reuse import setup

    sources, finding, llm, _ = await setup(settings)
    finding.evidence_quote = "full evidence " * 1000
    llm.input_capacity_chars = 1000
    llm.enforced_input_capacity_chars = enforced
    researcher = Researcher(llm=llm, settings=settings)
    if enforced is None:
        plan = await plan_findings([finding], "论文方法", [], researcher, sources)
        assert plan.sufficient and len(llm.selection_prompts[0]) > 1000
    else:
        with pytest.raises(ValueError, match="未自动扩大为全文补读"):
            await plan_findings([finding], "论文方法", [], researcher, sources)
        assert not llm.selection_prompts
