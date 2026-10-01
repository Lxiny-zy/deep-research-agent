"""Stable evidence must precede changing questions, dialogue and revision instructions."""

from __future__ import annotations

from deep_research.agents.base import Blackboard
from deep_research.agents.researcher import Researcher
from deep_research.models import FindingList
from deep_research.observability import Tracer
from deep_research.workbench.templates import get_template
from deep_research.workbench.writers import TemplateWriter
from tests.fakes import FakeLLM, FakeSearch


async def test_research_questions_share_the_unchanged_source_prefix(settings) -> None:
    prompts: list[str] = []

    class Capture(FakeLLM):
        async def parse(self, system, user, schema, **kwargs):  # type: ignore[no-untyped-def]
            if schema is FindingList:
                prompts.append(user)
            return await super().parse(system, user, schema, **kwargs)

    researcher = Researcher(Capture(), FakeSearch(), Tracer(), settings)
    await researcher.run("问题一：方法是什么？")
    await researcher.run("问题二：有什么局限？")
    prefixes = [prompt.split("\n子问题：", 1)[0] for prompt in prompts]
    assert len(prefixes) == 2 and prefixes[0] == prefixes[1]
    assert "内容A提供了可核验的原文证据" in prefixes[0]
    assert "问题一" not in prefixes[0] and "问题二" not in prefixes[1]


def test_report_material_precedes_changing_task_and_revision() -> None:
    writer = TemplateWriter()
    template = get_template("paperRead")
    assert template is not None
    material = "[1] 原文支持的结论与条件。"
    first = writer.user_prompt(Blackboard(query="聚焦方法"), template, None, material)
    second = writer.user_prompt(Blackboard(query="聚焦局限"), template, None, material)
    assert first.split("# 任务", 1)[0] == second.split("# 任务", 1)[0]
    assert first.index(material) < first.index("聚焦方法")


def test_global_rules_are_an_identical_prefix_across_roles() -> None:
    from deep_research.prompting import compose_system_prompt

    rules = "固定的研究规则"
    first = compose_system_prompt("角色甲", rules)
    second = compose_system_prompt("角色乙", rules)
    assert first.split("角色甲")[0] == second.split("角色乙")[0]
    assert first.index(rules) < first.index("角色甲")
    assert compose_system_prompt(first, rules) == first
