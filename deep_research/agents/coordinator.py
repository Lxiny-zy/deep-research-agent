"""Coordinator（L3 运行时自组合）：按问题现场生成一份角色流程，交由引擎递归执行。

与「三选一预置流程」不同：Coordinator 从能力注册表里挑角色、定是否反思及轮数，当场拼出
一份 Workflow——这是声明式 workflow-as-data 引擎的变现（流程即数据，可被生成）。

可靠性：生成的流程经 validate_workflow 白名单校验（只允许已注册角色、禁止嵌套自组合、
受步数/轮数上限约束）；不合法则把错误回灌自修复一次，仍失败回退内置 deep 流程——
保证「自主编排」永不因 LLM 乱出而炸穿或不终止。
"""

from __future__ import annotations

from typing import cast

from ..config import Settings
from ..llm import LLM
from ..observability import Tracer
from ..registry import available, register
from ..workflow import GeneratedWorkflow, Step, validate_workflow
from .base import Blackboard, RunContext, direct_system_prompt

# 可被 Coordinator 编排的内置角色及其用途（仅白名单内角色会进 prompt；终端须能产出报告）。
_ROLE_HINTS = {
    "planner": "把问题拆成若干可独立检索的子问题",
    "researcher": "按子问题并行检索网络、抽取带出处的发现",
    "reflector": "评估证据是否充分、提出补洞子问题（配合 reflect_loop）",
    "synthesizer": "把发现综合成带引用的报告（终端，必须有）",
    "critic": "对报告做批判性复核",
}

SYSTEM = (
    "你是多智能体研究系统的总协调者（Coordinator）。针对给定研究问题，从【可用角色】中"
    "挑选并排列出一条最合适的研究流程（即一个步骤列表 steps）。\n"
    "步骤类型只有两种：\n"
    '- {"kind":"agent","agent":"<角色名>"}：执行某角色一次\n'
    '- {"kind":"reflect_loop","reflector":"reflector","researcher":"researcher","max_rounds":N}'
    "：反思补洞循环（评估不足则补检索，最多 N 轮）\n"
    "编排原则：简单/单点问题可省略规划与反思以省 token（如 planner→researcher→synthesizer）；"
    "复杂、争议或需多侧面对比的问题应包含规划与反思补洞。"
    "流程必须以 synthesizer 收尾产出报告；不要使用未在【可用角色】中列出的角色。"
)


@register("coordinator")
class Coordinator:
    name: str  # 由 @register 注入

    def __init__(
        self, llm: LLM | None = None, tracer: Tracer | None = None, settings: Settings | None = None
    ) -> None:
        self.llm = cast(LLM, llm)
        self.tracer = cast(Tracer, tracer)
        self.settings = cast(Settings, settings)
        self.system = SYSTEM  # 可被角色卡片的 system_prompt 覆盖（数据驱动角色）

    async def step(self, bb: Blackboard, ctx: RunContext) -> Blackboard:
        self.llm, self.tracer, self.settings = ctx.llm_for(self.name), ctx.tracer, ctx.settings
        self.tracer.emit("COORDINATOR", "start", "按问题自组合研究流程…")
        avail = {n for n in available() if n in _ROLE_HINTS}  # 只暴露可编排的内置角色
        hint = bb.scratch.pop("replan_hint", "")  # 引擎重规划时回灌的修正提示（一次性）
        steps = await self._compose_steps(
            bb.query,
            avail,
            hint,
            system=ctx.system_prompt(self.system),
        )
        bb.scratch["composed_workflow"] = GeneratedWorkflow(steps=steps).model_dump()
        self.tracer.emit(
            "COORDINATOR",
            "info",
            f"生成流程：{' → '.join(self._label(s) for s in steps)}",
            data={"steps": [s.model_dump() for s in steps]},
        )
        return bb

    async def _compose_steps(
        self,
        query: str,
        avail: set[str],
        hint: str = "",
        *,
        system: str | None = None,
    ) -> list[Step]:
        roles = "\n".join(f"- {n}：{_ROLE_HINTS[n]}" for n in sorted(avail))
        user = f"研究问题：{query}\n\n【可用角色】\n{roles}\n\n请输出步骤列表。"
        if hint:  # 引擎重规划：把上次「零产出」的修正提示并入
            user = f"{user}\n\n（重规划提示：{hint}）"
        cap = self.settings.max_rounds
        for attempt in range(2):  # 一次生成 + 一次自修复
            try:
                gen = await self.llm.parse(
                    direct_system_prompt(system or self.system), user, GeneratedWorkflow
                )
            except Exception as e:  # 网络/解析彻底失败：回退默认深度流程，保证仍出报告
                self.tracer.emit("COORDINATOR", "info", f"生成流程失败（{e}），回退默认深度流程")
                return self._fallback()
            errors = validate_workflow(gen.steps, avail, max_rounds_cap=cap)
            if not errors:
                return gen.steps
            if attempt == 0:  # 首次不合法：把错误回灌让模型自修复
                self.tracer.emit("COORDINATOR", "info", f"流程不合法，自修复：{'；'.join(errors)}")
                user = (
                    f"{user}\n\n（上次流程不合法：{'；'.join(errors)}；请修正后只输出合法步骤列表）"
                )
        self.tracer.emit("COORDINATOR", "info", "自修复后仍不合法，回退默认深度流程")
        return self._fallback()

    @staticmethod
    def _fallback() -> list[Step]:
        from ..workflows import DEEP

        return [s.model_copy() for s in DEEP.steps]

    @staticmethod
    def _label(step: Step) -> str:
        return "reflect_loop" if step.kind == "reflect_loop" else (step.agent or step.kind)
