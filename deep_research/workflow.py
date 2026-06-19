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

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from .models import SubQuestion
from .registry import available, create

if TYPE_CHECKING:
    # 仅类型注解需要（本模块所有用法都在 `from __future__ import annotations` 的注解里）。
    # 放到 TYPE_CHECKING 下可切断 workflow → agents 包 → coordinator → workflow 的运行期循环导入。
    from .agents.base import Agent, Blackboard, RunContext
    from .token_budget import TokenBudget


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
    aggregator: str = "aggregator"  # kind="team_fanout" 时：归并各团队结果的角色名
    max_teams: int | None = None  # kind="team_fanout" 时：最多并行的子团队数（None＝不限）


class Workflow(BaseModel):
    name: str
    description: str = ""
    steps: list[Step] = Field(default_factory=list)


# 自组合流程的硬上限：防止 LLM 生成超长流程烧 token，并与「可证终止」挂钩。
MAX_GENERATED_STEPS = 8
# 能产出报告的终端角色：预算耗尽时仍会执行这些步骤，保证尽力而为的报告。
_TERMINAL_ROLES = {"synthesizer", "aggregator"}
# 子团队默认内部流程：在隔离子黑板上对其 focus 做一次检索（可被 SubTask.steps 覆盖）。
_DEFAULT_TEAM_STEPS = [Step(kind="agent", agent="researcher")]


class GeneratedWorkflow(BaseModel):
    """Coordinator 运行时生成的流程（Workflow 的可生成子集，无需 name）。

    经 model_dump() 落到 Blackboard.scratch，再由引擎 model_validate 回来递归执行。
    """

    description: str = ""
    steps: list[Step] = Field(default_factory=list)


class SubTask(BaseModel):
    """L4 一个子团队的任务：聚焦点 focus + 该团队自己的内部流程（空＝默认仅检索）。"""

    focus: str
    steps: list[Step] = Field(default_factory=list)


def validate_workflow(
    steps: list[Step],
    available_roles: set[str],
    *,
    max_rounds_cap: int,
    max_steps: int = MAX_GENERATED_STEPS,
    terminal_roles: set[str] | None = None,
) -> list[str]:
    """校验「运行时生成 / 用户自建的流程」是否安全可执行，返回错误列表（空＝通过）。

    规则（缺一不可，保证自主编排不炸穿 / 可终止 / 能产出）：
      - 非空且步数 ≤ max_steps；
      - 只引用已注册角色（白名单 available_roles）；
      - 禁止 compose / team_fanout（顶层编排原语，不允许出现在生成/自建流程里，杜绝无限递归）；
      - reflect_loop 轮数 ≤ max_rounds_cap；
      - 必须含终端角色收尾（默认 synthesizer/aggregator；自定义可经 terminal_roles 扩展，
        如把 behavior=synthesize 的自定义卡片计为终端），否则产不出报告。
    """
    terminals = terminal_roles if terminal_roles is not None else _TERMINAL_ROLES
    errors: list[str] = []
    if not steps:
        return ["流程为空"]
    if len(steps) > max_steps:
        errors.append(f"步骤数 {len(steps)} 超过上限 {max_steps}")
    has_terminal = False
    for i, step in enumerate(steps):
        if step.kind in ("compose", "team_fanout"):
            errors.append(f"第 {i} 步禁止在生成流程中使用顶层编排原语 {step.kind}")
        elif step.kind == "reflect_loop":
            for role in (step.reflector, step.researcher):
                if role not in available_roles:
                    errors.append(f"第 {i} 步引用未注册角色：{role}")
            if step.max_rounds is not None and step.max_rounds > max_rounds_cap:
                errors.append(f"第 {i} 步反思轮数 {step.max_rounds} 超上限 {max_rounds_cap}")
        elif step.kind == "agent":
            if step.agent not in available_roles:
                errors.append(f"第 {i} 步引用未注册角色：{step.agent}")
            if step.agent in terminals:
                has_terminal = True
        else:
            errors.append(f"第 {i} 步未知步骤类型：{step.kind}")
    if not has_terminal:
        errors.append("流程缺少终端角色（如 synthesizer），无法产出报告")
    return errors


class WorkflowEngine:
    """解释执行一份 Workflow。无状态，可复用；所有运行态都在传入的 Blackboard 上。"""

    def __init__(
        self,
        ctx: RunContext,
        resolver: Callable[[str], Agent] | None = None,
        *,
        budget: TokenBudget | None = None,
    ) -> None:
        self.ctx = ctx
        # 角色解析器：默认从代码注册表取；编排器可注入「先查 DB 角色卡片，再回退注册表」
        # 的解析器，实现数据驱动角色与内置角色统一调度。
        self._resolve = resolver or create
        # token 预算（None＝不限）；以 ctx.tracer.total_tokens 为唯一真相源，避免双重计数。
        self.budget = budget

    def _exhausted(self) -> bool:
        if self.budget is None:
            return False
        self.budget.update(self.ctx.tracer.total_tokens)
        return self.budget.exhausted

    @staticmethod
    def _is_terminal(step: Step) -> bool:
        """能产出报告的终端步骤：预算耗尽时仍执行，保证尽力而为的报告。"""
        return step.kind == "agent" and step.agent in _TERMINAL_ROLES

    @staticmethod
    def _step_label(step: Step) -> str:
        if step.kind == "reflect_loop":
            return "反思补洞"
        if step.kind == "compose":
            return "自组合"
        if step.kind == "team_fanout":
            return "多团队并行"
        return step.agent or step.kind

    @staticmethod
    def _stage_for(step: Step) -> str:
        """步骤失败时归属的事件 Stage（刻意避开 ORCHESTRATOR——它的 error 是运行终态，
        会让 run_stream 提前断流；单步失败是被隔离的，应挂在各自的非终态 Stage 上）。"""
        if step.kind == "agent" and step.agent:
            return step.agent.upper()
        if step.kind == "compose":
            return "COORDINATOR"
        if step.kind == "team_fanout":
            return "AGGREGATOR"
        if step.kind == "reflect_loop":
            return "REFLECTOR"
        return "ENGINE"

    async def run(self, wf: Workflow, bb: Blackboard) -> Blackboard:
        for step in wf.steps:
            # 预算耗尽：跳过研究/反思/自组合等非终端步骤，但仍执行 synthesizer 产出部分报告
            if self._exhausted() and not self._is_terminal(step):
                self.ctx.tracer.emit(
                    "ORCHESTRATOR", "info", f"token 预算耗尽，跳过 {self._step_label(step)}"
                )
                continue
            # 失败隔离：单步异常不炸穿整条工作流（与 researcher 内部按子问题隔离同一思路），
            # 记一条该步自有 Stage 的 error 事件后继续，让流程仍能走到终端综合产出报告。
            try:
                if step.kind == "reflect_loop":
                    await self._reflect_loop(step, bb)
                elif step.kind == "compose":
                    await self._compose(step, bb)
                elif step.kind == "team_fanout":
                    await self._team_fanout(step, bb)
                else:
                    bb = await self._resolve(step.agent).step(bb, self.ctx)
            except Exception as e:  # 不含 CancelledError（继承 BaseException）：断连仍能正常取消
                self.ctx.tracer.emit(
                    self._stage_for(step), "error", f"{self._step_label(step)} 失败，已隔离：{e}"
                )
        return bb

    async def _compose(self, step: Step, bb: Blackboard) -> None:
        """L3 运行时自组合：Coordinator 生成一份流程，校验后在同一黑板上递归执行。

        复用同一个 bb（与 _reflect_loop 一致）：生成流程产出的 plan/results/report 都落在
        编排器持有的那个黑板上，落库逻辑无需改动。深度守卫 + 禁止嵌套 compose 保证终止。

        自纠错：生成流程「零产出」时，Coordinator 至多重规划 settings.max_replans 次，
        每次受 token 预算约束。终止性 = 重规划上限 ∨ 预算耗尽 ∨ 有产出（缺一不可）。
        """
        depth = bb.scratch.get("_compose_depth", 0)
        if depth >= 1:
            self.ctx.tracer.emit("COORDINATOR", "info", "已达自组合深度上限，跳过嵌套 compose")
            return
        bb.scratch["_compose_depth"] = depth + 1

        attempts = max(0, self.ctx.settings.max_replans) + 1
        for attempt in range(attempts):
            if attempt > 0:
                self.ctx.tracer.emit("COORDINATOR", "round", f"第 {attempt} 次重规划（上次零产出）")
                bb.scratch["replan_hint"] = (
                    "上次生成的流程没有产出任何研究结果；请改用更充分的流程"
                    "（至少包含 planner 与 researcher，必要时加 reflect_loop 补洞）。"
                )
            generated = await self._generate(step.agent, bb)
            if generated is None:  # 校验失败已回退执行 deep
                return
            await self.run(generated, bb)  # run 内部已逐步隔离失败
            if bb.results:  # 有研究产出即视为成功
                return
            if self._exhausted():  # 预算耗尽：不再重规划
                break
        # 重规划预算用尽仍零产出：保留已生成的（可能为空的）报告，不再强行

    async def _generate(self, coordinator: str, bb: Blackboard) -> Workflow | None:
        """运行 Coordinator 产出并校验一份流程；不合法则回退执行内置 deep 并返回 None。"""
        await self._resolve(coordinator).step(bb, self.ctx)  # 写 bb.scratch["composed_workflow"]
        raw = bb.scratch.get("composed_workflow") or {}
        gen = GeneratedWorkflow.model_validate(raw) if raw else GeneratedWorkflow()
        errors = validate_workflow(
            gen.steps, set(available()), max_rounds_cap=self.ctx.settings.max_rounds
        )
        if errors:
            from .workflows import DEEP

            self.ctx.tracer.emit(
                "COORDINATOR", "info", f"生成流程未通过校验，回退 deep：{'；'.join(errors)}"
            )
            await self.run(DEEP, bb)
            return None
        return Workflow(name="generated", description=gen.description, steps=gen.steps)

    def _collect_subtasks(self, bb: Blackboard, max_teams: int | None) -> list[SubTask]:
        """子任务来源：优先 bb.scratch["subtasks"]（Coordinator/Planner 显式产出），
        否则把已有 plan 的每个子问题各作为一个团队的聚焦点。受 max_teams 上限约束。"""
        raw = bb.scratch.get("subtasks")
        tasks: list[SubTask] = []
        if raw:
            tasks = [SubTask.model_validate(item) for item in raw]
        elif bb.plan is not None:
            tasks = [SubTask(focus=sq.question) for sq in bb.plan.sub_questions]
        cap = max_teams if max_teams is not None else len(tasks)
        return tasks[: max(0, cap)]

    async def _team_fanout(self, step: Step, bb: Blackboard) -> None:
        """L4 多团队并行（map-reduce）：把子任务分给隔离子团队并行研究，再由 aggregator 归并。

        每个团队在自己的子黑板上跑（避免并发写父黑板竞争）；asyncio.gather + Semaphore 限流，
        单团队失败被隔离为跳过；gather 后串行把各团队结果并入父黑板，最后 aggregator 综合。
        """
        from .agents.base import Blackboard as BB  # 运行期才导入，避免顶层循环导入

        subtasks = self._collect_subtasks(bb, step.max_teams)
        if not subtasks:
            self.ctx.tracer.emit("ORCHESTRATOR", "info", "无子任务可分派，跳过多团队")
            return
        self.ctx.tracer.emit(
            "ORCHESTRATOR",
            "info",
            f"分派 {len(subtasks)} 个子团队并行研究",
            data={"teams": [t.focus for t in subtasks]},
        )
        sem = asyncio.Semaphore(self.ctx.settings.max_concurrency)

        async def _run_team(idx: int, task: SubTask) -> Blackboard | None:
            async with sem:
                child = BB(query=task.focus)
                child.scratch["pending_sub_questions"] = [SubQuestion(question=task.focus)]
                team_steps = task.steps or _DEFAULT_TEAM_STEPS
                try:
                    await self.run(Workflow(name=f"team-{idx}", steps=list(team_steps)), child)
                    return child
                except Exception as e:  # 单团队失败隔离：记一条 error，返回 None 不拖垮其余团队
                    self.ctx.tracer.emit("AGGREGATOR", "error", f"子团队 {idx} 失败，已隔离：{e}")
                    return None

        children = await asyncio.gather(*[_run_team(i, t) for i, t in enumerate(subtasks)])
        for child in children:  # 串行合并，避免并发写父黑板的竞态
            if child is not None:
                bb.results += child.results
        await self._resolve(step.aggregator).step(bb, self.ctx)

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
