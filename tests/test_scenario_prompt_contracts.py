"""Exercise the effective output contracts used by different writing scenarios."""

from deep_research.agents.base import Blackboard, RunContext
from deep_research.observability import Tracer
from deep_research.workbench.templates import LIT_REVIEW, SLIDES
from deep_research.workbench.writers import Slide, SlideDeck, SlideWriter, SurveyWriter
from tests.fakes import FakeLLM, FakeSearch


async def test_slide_generation_uses_one_structured_output_contract(settings):
    class Capture(FakeLLM):
        prompt = ""

        async def parse(self, system, user, schema, **kwargs):
            assert schema is SlideDeck
            self.prompt = system
            return SlideDeck(
                title="研究进展",
                slides=[
                    Slide(title="发现", bullets=["结论 [1]"], notes="适用条件 [1]", citations=[1])
                ],
            )

    llm = Capture()
    ctx = RunContext(llm=llm, search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    body = await SlideWriter().write(
        Blackboard(query="汇报研究进展"), ctx, SLIDES, None, "[1] 已核验结论与条件。"
    )
    assert "结论 [1]" in body
    assert "只依据【已核验素材】" in llm.prompt
    assert "结构化 JSON" in llm.prompt
    assert "evidence-table" not in llm.prompt
    assert "用 Markdown 输出，章节用二级标题" not in llm.prompt
    assert llm.prompt.count("每页 3–5 条") == 1


def test_literature_comparison_uses_the_supported_table_contract():
    prompt = SurveyWriter().system_prompt(LIT_REVIEW, None)
    assert "evidence-table" in prompt
    assert "方法对比优先用 Markdown 表格" not in prompt
