"""声明式工作流：把「控制流」从编排器代码变成可声明、可路由、可运行时生成的数据。

一个 Workflow 是一串 Step；引擎顺序解释每个 Step，对同一块 Blackboard 逐步加工。
两类控制原语覆盖了原编排器写死的全部逻辑：
  - 普通 step：调用一个角色的 step(bb, ctx)
  - loop step：带 reflect/research 的「评估→补洞」循环（原 max_rounds 循环）

新增角色无需改引擎：只要它已注册，在 steps 里写上名字即可被调度。
不同任务类型走不同流程：在 workflows/ 里声明不同的 Workflow 即可（见 L2 路由）。
运行时动态组队：Coordinator 角色可生成一个 Workflow 交给引擎执行（见 L3）。
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, Field

from .agents.base import Agent, Blackboard, RunContext
from .models import SubQuestion
from .registry import create


class Step(BaseModel):
    """单个工作流步骤。

    kind="agent"：执行 `agent` 命名的角色一次。
    kind="reflect_loop"：用 `reflector` 评估，不充分则把新子问题交给 `researcher`
        再研究，最多 `max_rounds` 轮（复刻原编排器的反思补洞循环）。
    """

    kind: str = "agent"
    agent: str = ""  # kind="agent" 时：角色名
    reflector: str = "reflector"  # kind="reflect_loop" 时：评估角色名
    researcher: str = "researcher"  # kind="reflect_loop" 时：补洞研究角色名
    max_rounds: int | None = None  # None＝用 settings.max_rounds


class Workflow(BaseModel):
    name: str
    description: str = ""
    steps: list[Step] = Field(default_factory=list)


class WorkflowEngine:
    """解释执行一份 Workflow。无状态，可复用；所有运行态都在传入的 Blackboard 上。"""

    def __init__(self, ctx: RunContext, resolver: Callable[[str], Agent] | None = None) -> None:
        self.ctx = ctx
        # 角色解析器：默认从代码注册表取；编排器可注入「先查 DB 角色卡片，再回退注册表」
        # 的解析器，实现数据驱动角色与内置角色统一调度。
        self._resolve = resolver or create

    async def run(self, wf: Workflow, bb: Blackboard) -> Blackboard:
        for step in wf.steps:
            if step.kind == "reflect_loop":
                await self._reflect_loop(step, bb)
            else:
                bb = await self._resolve(step.agent).step(bb, self.ctx)
        return bb

    async def _reflect_loop(self, step: Step, bb: Blackboard) -> None:
        reflector = self._resolve(step.reflector)
        researcher = self._resolve(step.researcher)
        rounds = step.max_rounds if step.max_rounds is not None else self.ctx.settings.max_rounds
        for rnd in range(rounds):
            bb = await reflector.step(bb, self.ctx)
            reflection = bb.reflections[-1] if bb.reflections else None
            if reflection is None or reflection.is_sufficient or not reflection.new_sub_questions:
                break
            self.ctx.tracer.emit("ORCHESTRATOR", "round", f"第 {rnd + 1} 轮补洞")
            new_subs = [SubQuestion(question=q) for q in reflection.new_sub_questions]
            # 记录补洞轮次，供编排器落库（origin="reflection"）；不影响无 repo 运行
            bb.scratch.setdefault("reflection_rounds", []).append(
                {"round": rnd + 1, "sub_questions": new_subs}
            )
            # 把补洞子问题交给 researcher 增量研究（经 scratch 传递，不重研已做过的）
            bb.scratch["pending_sub_questions"] = new_subs
            bb = await researcher.step(bb, self.ctx)
