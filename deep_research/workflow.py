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
from collections.abc import Awaitable, Callable, Sequence
from copy import deepcopy
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import uuid4

from pydantic import BaseModel, Field

from .guardrails import ClaimConsistencyVerifier, verify_claim_consistency
from .models import SubQuestion
from .orchestration import (
    OrchestrationRuntime,
    RunStatus,
    StepRun,
    StepStatus,
    WorkflowEdge,
    WorkflowNode,
    WorkflowRun,
)
from .persistence.repository import LeaseLostError
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
    timeout_seconds: float | None = Field(None, gt=0, le=3600)
    max_attempts: int = Field(1, ge=1, le=10)
    retry_backoff: float = Field(0.5, ge=0, le=60)
    failure_policy: Literal["continue", "fail_fast"] = "continue"
    fallback_agent: str | None = None


class Workflow(BaseModel):
    name: str
    description: str = ""
    steps: list[Step] = Field(default_factory=list)
    nodes: list[dict] = Field(default_factory=list)
    edges: list[dict] = Field(default_factory=list)


# 自组合流程的硬上限：防止 LLM 生成超长流程烧 token，并与「可证终止」挂钩。
MAX_GENERATED_STEPS = 8
# 重试预算的硬上限：Step 字段本身允许 max_attempts≤10、retry_backoff≤60，
# 若不在校验期约束，生成/自建流程可合法产出单步小时级的最坏指数退避。
MAX_STEP_ATTEMPTS = 5
MAX_TOTAL_BACKOFF_SECONDS = 120.0
# 能产出报告的终端角色：预算耗尽时仍会执行这些步骤，保证尽力而为的报告。
_TERMINAL_ROLES = {"synthesizer", "aggregator"}
# 角色请求提前终止的黑板标记：任一角色把它设为真值，引擎即跳过后续非终端步骤。
# 通用原语而非「意图专用」——引擎不需要知道谁因为什么要求停下（当前使用方是
# IntentRouter 的风险拒识；将来的合规/配额检查可复用同一机制）。角色在设置它
# 之前必须自行产出 bb.report，否则 require_report 会判定运行失败。
HALT_SCRATCH_KEY = "_halt"
HALT_REASON_KEY = "_halt_reason"
# 子团队默认内部流程：在隔离子黑板上对其 focus 做一次检索（可被 SubTask.steps 覆盖）。
_DEFAULT_TEAM_STEPS = [Step(kind="agent", agent="researcher")]
_INCOMING_CONDITIONS_SKIP_REASON = "incoming conditions not matched"
_MISSING = object()


def _merge_parallel_value(base: Any, current: Any, branch: Any) -> Any:
    """Apply one branch's change relative to a shared layer snapshot."""
    if branch is _MISSING:
        return _MISSING
    if base is not _MISSING and branch == base:
        return current

    if (
        isinstance(branch, dict)
        and (base is _MISSING or isinstance(base, dict))
        and (current is _MISSING or isinstance(current, dict))
    ):
        base_dict: dict[Any, Any] = {} if base is _MISSING else cast("dict[Any, Any]", base)
        merged: dict[Any, Any] = (
            {} if current is _MISSING else deepcopy(cast("dict[Any, Any]", current))
        )
        keys = dict.fromkeys([*base_dict.keys(), *branch.keys()])
        for key in keys:
            merged_value = _merge_parallel_value(
                base_dict.get(key, _MISSING),
                merged.get(key, _MISSING),
                branch.get(key, _MISSING),
            )
            if merged_value is _MISSING:
                merged.pop(key, None)
            else:
                merged[key] = merged_value
        return merged

    if isinstance(branch, list) and (base is _MISSING or isinstance(base, list)):
        base_list: list[Any] = [] if base is _MISSING else cast("list[Any]", base)
        if len(branch) >= len(base_list) and branch[: len(base_list)] == base_list:
            additions = branch[len(base_list) :]
            if current is _MISSING:
                return deepcopy(branch)
            if isinstance(current, list):
                return [*deepcopy(current), *deepcopy(additions)]
        if (
            isinstance(current, list)
            and len(current) >= len(base_list)
            and current[: len(base_list)] == base_list
        ):
            # A destructive edit wins for the baseline portion, but it must
            # not erase another branch's independent append-only delta.
            return [*deepcopy(branch), *deepcopy(current[len(base_list) :])]

    return deepcopy(branch)


def _merge_parallel_sequence(base: list[Any], current: list[Any], branch: list[Any]) -> list[Any]:
    if branch == base:
        return current
    if len(branch) < len(base):
        additions = current[len(base) :] if current[: len(base)] == base else []
        return [*deepcopy(branch), *deepcopy(additions)]

    if branch[: len(base)] != base:
        if current[: len(base)] == base:
            # current 未动基线：branch 的改写整体生效，仅需保留 current 的追加段。
            return [*deepcopy(branch), *deepcopy(current[len(base) :])]
        # 双方都改写了基线前缀（如各分支的 claim consistency 均原地替换基线 Finding）：
        # 基线段以 branch 相对 base 的逐元素差异覆盖到 current 之上，再同时保留双方
        # 各自超出基线长度的追加段——破坏性改写不得抹掉兄弟分支的追加增量。
        merged = deepcopy(current[: len(base)])
        for index, base_item in enumerate(base):
            if branch[index] != base_item:
                if index < len(merged):
                    merged[index] = deepcopy(branch[index])
                else:
                    merged.append(deepcopy(branch[index]))
        merged.extend(deepcopy(current[len(base) :]))
        merged.extend(deepcopy(branch[len(base) :]))
        return merged

    merged = deepcopy(current)
    for index, base_item in enumerate(base):
        if branch[index] != base_item:
            if index < len(merged):
                merged[index] = deepcopy(branch[index])
            else:
                merged.append(deepcopy(branch[index]))
    merged.extend(deepcopy(branch[len(base) :]))
    return merged


def _merge_parallel_blackboard(base: Blackboard, current: Blackboard, branch: Blackboard) -> None:
    if branch.query != base.query:
        current.query = branch.query
    if branch.plan != base.plan:
        current.plan = deepcopy(branch.plan)
    if branch.report != base.report:
        current.report = deepcopy(branch.report)
    current.results = _merge_parallel_sequence(base.results, current.results, branch.results)
    current.reflections = _merge_parallel_sequence(
        base.reflections, current.reflections, branch.reflections
    )
    merged_scratch = _merge_parallel_value(base.scratch, current.scratch, branch.scratch)
    current.scratch = {} if merged_scratch is _MISSING else merged_scratch


def _commit_blackboard(target: Blackboard, source: Blackboard) -> Blackboard:
    """Commit a successful isolated attempt while preserving target identity."""
    for field_name in target.__class__.model_fields:
        setattr(target, field_name, getattr(source, field_name))
    return target


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


def validate_workflow_graph_terminal(
    nodes: list[dict],
    edges: list[dict],
    *,
    terminal_roles: set[str] | None = None,
) -> list[str]:
    """Require every graph branch to end at a report-producing node."""
    terminals = terminal_roles if terminal_roles is not None else _TERMINAL_ROLES
    parsed_nodes = [WorkflowNode.model_validate(node) for node in nodes]
    parsed_edges = [WorkflowEdge.model_validate(edge) for edge in edges]
    outgoing = {edge.source for edge in parsed_edges}
    terminal_steps: list[Step] = []
    report_steps: list[tuple[str, Step]] = []
    for node in parsed_nodes:
        step = Step.model_validate(node.step)
        if node.id not in outgoing:
            terminal_steps.append(step)
        if step.kind == "agent" and step.agent in terminals:
            report_steps.append((node.id, step))
    if len(terminal_steps) == 1 and len(report_steps) == 1 and report_steps[0][0] not in outgoing:
        return []
    return ["所有工作流分支都必须汇入唯一的终端报告角色（如 Synthesizer）"]


def validate_workflow_steps_terminal(
    steps: list[Step], *, terminal_roles: set[str] | None = None
) -> list[str]:
    terminals = terminal_roles if terminal_roles is not None else _TERMINAL_ROLES
    report_indexes = [
        index
        for index, step in enumerate(steps)
        if step.kind == "agent" and step.agent in terminals
    ]
    if report_indexes == [len(steps) - 1]:
        return []
    return ["流程必须由唯一的终端角色（如 synthesizer）收尾"]


def validate_workflow(
    steps: list[Step],
    available_roles: set[str],
    *,
    max_rounds_cap: int,
    max_steps: int = MAX_GENERATED_STEPS,
    terminal_roles: set[str] | None = None,
    require_terminal_last: bool = True,
) -> list[str]:
    """校验「运行时生成 / 用户自建的流程」是否安全可执行，返回错误列表（空＝通过）。

    规则（缺一不可，保证自主编排不炸穿 / 可终止 / 能产出）：
      - 非空且步数 ≤ max_steps；
      - 只引用已注册角色（白名单 available_roles）；
      - 禁止 compose / team_fanout（顶层编排原语，不允许出现在生成/自建流程里，杜绝无限递归）；
      - reflect_loop 轮数 ≤ max_rounds_cap；
      - 单步重试次数 ≤ MAX_STEP_ATTEMPTS，且全流程最坏重试退避总时长
        ≤ MAX_TOTAL_BACKOFF_SECONDS（防止合法字段组合出小时级的空转等待）；
      - 必须由终端角色收尾（默认 synthesizer/aggregator；自定义可经 terminal_roles 扩展，
        如把 behavior=synthesize 的自定义卡片计为终端），否则产不出报告。
    """
    terminals = terminal_roles if terminal_roles is not None else _TERMINAL_ROLES
    errors: list[str] = []
    if not steps:
        return ["流程为空"]
    if len(steps) > max_steps:
        errors.append(f"步骤数 {len(steps)} 超过上限 {max_steps}")
    has_terminal = False
    worst_backoff = 0.0
    for i, step in enumerate(steps):
        if step.fallback_agent and step.fallback_agent not in available_roles:
            errors.append(f"第 {i} 步引用未注册 fallback 角色：{step.fallback_agent}")
        if step.max_attempts > MAX_STEP_ATTEMPTS:
            errors.append(f"第 {i} 步重试次数 {step.max_attempts} 超上限 {MAX_STEP_ATTEMPTS}")
        # 单步最坏退避：retry_backoff * (2^0 + 2^1 + … + 2^(max_attempts-2))，
        # 与引擎的指数退避实现（retry_backoff * 2**attempt）一致。
        worst_backoff += step.retry_backoff * (2 ** (step.max_attempts - 1) - 1)
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
    if worst_backoff > MAX_TOTAL_BACKOFF_SECONDS:
        errors.append(
            f"全流程最坏重试退避总时长 {worst_backoff:.1f}s "
            f"超上限 {MAX_TOTAL_BACKOFF_SECONDS:.0f}s，请调低 max_attempts 或 retry_backoff"
        )
    if not has_terminal:
        errors.append("流程缺少终端角色（如 synthesizer），无法产出报告")
    elif require_terminal_last:
        errors.extend(validate_workflow_steps_terminal(steps, terminal_roles=terminals))
    return errors


class WorkflowEngine:
    """解释执行一份 Workflow。无状态，可复用；所有运行态都在传入的 Blackboard 上。"""

    def __init__(
        self,
        ctx: RunContext,
        resolver: Callable[[str], Agent] | None = None,
        *,
        budget: TokenBudget | None = None,
        checkpoint_sink: Callable[[WorkflowRun], Awaitable[None]] | None = None,
        resume_run: WorkflowRun | None = None,
        initial_run: WorkflowRun | None = None,
        require_report: bool = False,
        terminal_roles: set[str] | None = None,
    ) -> None:
        self.ctx = ctx
        # 角色解析器：默认从代码注册表取；编排器可注入「先查 DB 角色卡片，再回退注册表」
        # 的解析器，实现数据驱动角色与内置角色统一调度。
        self._resolve = resolver or create
        # token 预算（None＝不限）；以 ctx.tracer.total_tokens 为唯一真相源，避免双重计数。
        self.budget = budget
        self.runtime = OrchestrationRuntime(self._emit_runtime_event)
        self._run_depth = 0
        self._checkpoint_sink = checkpoint_sink
        if resume_run is not None and initial_run is not None:
            raise ValueError("resume_run and initial_run are mutually exclusive")
        self._resuming = resume_run is not None
        self._require_report = require_report
        self._terminal_roles = terminal_roles if terminal_roles is not None else _TERMINAL_ROLES
        if resume_run is not None:
            self.runtime.restore(resume_run)
        elif initial_run is not None:
            self.runtime.adopt(initial_run)

    async def _checkpoint(
        self, wf: Workflow, bb: Blackboard, *, publish_event: bool = True
    ) -> None:
        # Keep cumulative observability counters with the durable root state so
        # a resumed agent does not reset its budget or elapsed-time accounting.
        bb.scratch["_runtime_metrics"] = {
            "total_tokens": self.ctx.tracer.total_tokens,
            "estimated_tokens": self.ctx.tracer.estimated_tokens,
            "elapsed": self.ctx.tracer.elapsed,
        }
        definition = {
            "name": wf.name,
            "description": wf.description,
            "steps": [step.model_dump(mode="json") for step in wf.steps],
            "nodes": wf.nodes,
            "edges": wf.edges,
        }
        checkpoint = bb.model_dump(mode="json")
        if publish_event:
            run = self.runtime.save_checkpoint(checkpoint, definition)
        else:
            current_run = self.runtime.run
            assert current_run is not None
            current_run.checkpoint = checkpoint
            current_run.definition = definition
            run = current_run
        if self._checkpoint_sink is not None:
            await self._checkpoint_sink(run)

    def _emit_runtime_event(self, name: str, data: dict) -> None:
        """把统一运行时生命周期桥接到已有 SSE/持久化事件流。"""
        self.ctx.tracer.emit("ORCHESTRATOR", "info", name, data={"event_name": name, **data})

    def _emit_workflow_plan(self, wf: Workflow) -> None:
        """在首个步骤开始前公布总阶段数，供前端计算可信的总体阶段进度。"""
        if wf.nodes:
            planned = [Step.model_validate(node["step"]) for node in wf.nodes]
        else:
            planned = list(wf.steps)
        self.ctx.tracer.emit(
            "ORCHESTRATOR",
            "info",
            f"研究流程已就绪，共 {len(planned)} 个阶段",
            data={
                "event_name": "workflow.plan",
                "workflow": wf.name,
                "total_steps": len(planned),
                "steps": [self._step_label(step) for step in planned],
            },
        )

    async def _invoke_step(
        self, step: Step, bb: Blackboard, *, agent_override: str | None = None
    ) -> Blackboard:
        if agent_override is not None:
            return await self._resolve(agent_override).step(bb, self.ctx)
        if step.kind == "reflect_loop":
            await self._reflect_loop(step, bb)
        elif step.kind == "compose":
            await self._compose(step, bb)
        elif step.kind == "team_fanout":
            await self._team_fanout(step, bb)
        else:
            bb = await self._resolve(step.agent).step(bb, self.ctx)
        return bb

    async def _invoke_with_timeout(
        self, step: Step, bb: Blackboard, *, agent_override: str | None = None
    ) -> Blackboard:
        if step.timeout_seconds is None:
            return await self._invoke_step(step, bb, agent_override=agent_override)
        async with asyncio.timeout(step.timeout_seconds):
            return await self._invoke_step(step, bb, agent_override=agent_override)

    async def _execute_with_policy(
        self, step: Step, bb: Blackboard, step_run: StepRun
    ) -> Blackboard:
        try:
            last_error: Exception | None = None
            for attempt in range(step.max_attempts):
                self.runtime.start_step(step_run)
                candidate = bb.model_copy(deep=True)
                try:
                    candidate = await self._invoke_with_timeout(step, candidate)
                except Exception as exc:
                    last_error = exc
                    if attempt + 1 < step.max_attempts:
                        delay = step.retry_backoff * (2**attempt)
                        self.runtime.retry_step(step_run, exc, delay)
                        if delay:
                            await asyncio.sleep(delay)
                        continue
                    break
                else:
                    bb = _commit_blackboard(bb, candidate)
                    self.runtime.complete_step(step_run)
                    return bb

            if step.fallback_agent:
                candidate = bb.model_copy(deep=True)
                try:
                    candidate = await self._invoke_with_timeout(
                        step, candidate, agent_override=step.fallback_agent
                    )
                except Exception as exc:
                    last_error = exc
                else:
                    bb = _commit_blackboard(bb, candidate)
                    self.runtime.complete_step(step_run)
                    return bb
            assert last_error is not None
            raise last_error
        except asyncio.CancelledError:
            if step_run.status in {
                StepStatus.READY,
                StepStatus.RUNNING,
                StepStatus.RETRYING,
            }:
                self.runtime.cancel_step(step_run)
            raise

    def _exhausted(self) -> bool:
        if self.budget is None:
            return False
        self.budget.update(self.ctx.tracer.total_tokens)
        return self.budget.exhausted

    def _is_terminal(self, step: Step) -> bool:
        """能产出报告的终端步骤：预算耗尽时仍执行，保证尽力而为的报告。"""
        return step.kind == "agent" and step.agent in self._terminal_roles

    @staticmethod
    def _halted(bb: Blackboard) -> bool:
        """是否有角色请求提前终止本次运行。

        与预算耗尽的区别：预算耗尽仍跑终端角色以产出尽力而为的报告，而 halt
        跳过**包括终端在内**的全部剩余步骤——请求终止的角色已经写好了 bb.report，
        再跑 synthesizer 只会拿着空结果集覆盖掉那份说明。
        """
        return bool(bb.scratch.get(HALT_SCRATCH_KEY))

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
            stage = step.agent.upper()
            # 用户可自建名为 "orchestrator" 的角色卡：派生 Stage 一旦命中保留值
            # ORCHESTRATOR，其 error 会被 run_stream 误判为运行终态而提前断流，
            # 必须降级为非保留 Stage（catalog 侧另有名称保留校验，这里是运行时兜底）。
            if stage == "ORCHESTRATOR":
                return "STEP_ORCHESTRATOR"
            return stage
        if step.kind == "compose":
            return "COORDINATOR"
        if step.kind == "team_fanout":
            return "AGGREGATOR"
        if step.kind == "reflect_loop":
            return "REFLECTOR"
        return "ENGINE"

    async def run(self, wf: Workflow, bb: Blackboard) -> Blackboard:
        # run 级共享限流：并行图的 K 个检索型节点 / 多团队并行若各自建
        # Semaphore(max_concurrency)，总并发会被放大为 K×max_concurrency。
        # 引擎在 run 入口把信号量挂到 ctx 上（已存在则沿用，嵌套子流程共用同一把），
        # 角色未被注入时才自建，保证单测可脱离引擎独立运行。
        if getattr(self.ctx, "run_semaphore", None) is None:
            self.ctx.run_semaphore = asyncio.Semaphore(  # type: ignore[attr-defined]
                self.ctx.settings.max_concurrency
            )
        if wf.nodes:
            return await self._run_graph(wf, bb)
        return await self._run_steps(wf, bb)

    async def _run_steps(self, wf: Workflow, bb: Blackboard) -> Blackboard:
        is_root = self._run_depth == 0
        if is_root:
            self.runtime.start(wf.name, {"query": bb.query})
            self._emit_workflow_plan(wf)
        self._run_depth += 1
        restored_steps = {
            step.node_id: step
            for step in (
                self.runtime.run.steps
                if is_root and self._resuming and self.runtime.run is not None
                else []
            )
        }
        try:
            for index, step in enumerate(wf.steps):
                node_id = f"step-{index + 1}"
                previous = restored_steps.get(node_id)
                if previous is not None and previous.status in {
                    StepStatus.SUCCEEDED,
                    StepStatus.SKIPPED,
                }:
                    continue
                step_run = self.runtime.create_step(
                    node_id=node_id if is_root else f"nested-{uuid4()}",
                    label=self._step_label(step),
                    kind=step.kind,
                    agent=step.agent,
                )
                # 角色请求提前终止（如意图门禁拒识）：跳过全部剩余步骤，
                # 记为 skipped 而非消失，保证运行历史仍然完整可回放。
                if self._halted(bb):
                    reason = str(bb.scratch.get(HALT_REASON_KEY) or "halted by agent")
                    self.runtime.skip_step(step_run, reason)
                    continue
                # 预算耗尽：显式记录 skipped，而不是让步骤从运行历史中消失。
                if self._exhausted() and not self._is_terminal(step):
                    self.runtime.skip_step(step_run, "token budget exhausted")
                    self.ctx.tracer.emit(
                        "ORCHESTRATOR", "info", f"token 预算耗尽，跳过 {self._step_label(step)}"
                    )
                    continue
                try:
                    bb = await self._execute_with_policy(step, bb, step_run)
                except Exception as e:  # 单步失败隔离，但进入明确 FAILED 状态
                    self.runtime.fail_step(step_run, e)
                    self.ctx.tracer.emit(
                        self._stage_for(step),
                        "error",
                        f"{self._step_label(step)} 失败，已隔离：{e}",
                    )
                    if step.failure_policy == "fail_fast":
                        raise
                if is_root:
                    await self._checkpoint(wf, bb)
            if is_root and self._require_report and bb.report is None:
                raise RuntimeError("工作流结束但未生成报告")
            return bb
        except asyncio.CancelledError:
            if is_root and self.runtime.run is not None:
                self.runtime.finish(RunStatus.CANCELLED)
                try:
                    await self._checkpoint(wf, bb)
                except LeaseLostError:
                    pass
            raise
        except Exception:
            if is_root and self.runtime.run is not None:
                self.runtime.finish(RunStatus.FAILED)
                try:
                    await self._checkpoint(wf, bb)
                except LeaseLostError:
                    # 租约已被继任者接管：终态 checkpoint 写不进去是预期情况，
                    # 不得让它掩盖原始失败异常（与上方取消路径的处理一致）。
                    pass
            raise
        finally:
            self._run_depth -= 1
            if is_root:
                self._resuming = False
            if (
                is_root
                and self.runtime.run is not None
                and self.runtime.run.status == RunStatus.RUNNING
            ):
                run = self.runtime.finish(
                    RunStatus.SUCCEEDED,
                    output={"has_report": bb.report is not None, "result_count": len(bb.results)},
                )
                bb.scratch["_orchestration_run"] = run.model_dump(
                    mode="json", exclude={"checkpoint", "definition", "steps"}
                )
                await self._checkpoint(wf, bb, publish_event=False)

    async def _run_graph(self, wf: Workflow, bb: Blackboard) -> Blackboard:
        """Execute a DAG layer by layer; nodes in the same layer run concurrently."""
        from .orchestration import (
            WorkflowEdge,
            WorkflowNode,
            evaluate_condition,
            graph_topo_layers,
        )

        nodes = [WorkflowNode.model_validate(raw) for raw in wf.nodes]
        edges = [WorkflowEdge.model_validate(raw) for raw in wf.edges]
        layers = graph_topo_layers(nodes, edges)
        incoming: dict[str, list[WorkflowEdge]] = {node.id: [] for node in nodes}
        for edge in edges:
            incoming[edge.target].append(edge)
        is_root = self._run_depth == 0
        active_nodes: set[str] = set()
        successful_nodes: set[str] = set()
        restored = {
            step.node_id: step
            for step in (
                self.runtime.run.steps
                if is_root and self._resuming and self.runtime.run is not None
                else []
            )
        }
        active_nodes.update(
            node_id for node_id, step in restored.items() if step.status == StepStatus.SUCCEEDED
        )
        successful_nodes.update(
            node_id for node_id, step in restored.items() if step.status == StepStatus.SUCCEEDED
        )
        if is_root:
            self.runtime.start(wf.name, {"query": bb.query})
            self._emit_workflow_plan(wf)
        self._run_depth += 1
        try:
            for layer_index, layer in enumerate(layers):
                self.ctx.tracer.emit(
                    "ORCHESTRATOR",
                    "info",
                    f"执行图层 {layer_index + 1}，并行节点 {len(layer)} 个",
                    data={"graph_layer": layer_index, "nodes": [node.id for node in layer]},
                )
                layer_base = bb.model_copy(deep=True)
                layer_nodes = tuple(layer)

                async def execute_node(
                    node: WorkflowNode,
                    layer_snapshot: Blackboard = layer_base,
                ) -> tuple[Blackboard, bool, bool]:
                    child = layer_snapshot.model_copy(deep=True)
                    step = Step.model_validate(node.step)
                    previous = restored.get(node.id)
                    # A condition-derived skip is provisional: a failed
                    # predecessor may succeed during recovery, changing this
                    # node's eligibility. Budget skips remain terminal because
                    # resumed runs restore the original budget and counters.
                    previous_is_terminal = previous is not None and (
                        previous.status == StepStatus.SUCCEEDED
                        or (
                            previous.status == StepStatus.SKIPPED
                            and previous.error != _INCOMING_CONDITIONS_SKIP_REASON
                        )
                    )
                    if previous_is_terminal:
                        assert previous is not None
                        active = previous.status == StepStatus.SUCCEEDED
                        return child, active, previous.status == StepStatus.SUCCEEDED
                    step_run = self.runtime.create_step(
                        node_id=node.id if is_root else f"nested-{uuid4()}",
                        label=self._step_label(step),
                        kind=step.kind,
                        agent=step.agent,
                    )
                    node_edges = incoming[node.id]

                    def edge_active(edge: WorkflowEdge) -> bool:
                        if edge.source not in active_nodes:
                            return False
                        # 条件求值兜底：单条坏边（如运行期才暴露的非法字面量）按
                        # 「不激活」处理并发警告事件，不得经 asyncio.gather 炸穿整个 run。
                        try:
                            return evaluate_condition(edge.condition, bb)
                        except Exception as exc:
                            self.ctx.tracer.emit(
                                "ORCHESTRATOR",
                                "info",
                                f"边 {edge.id} 条件求值失败，按不激活处理：{exc}",
                                data={
                                    "event_name": "edge.condition_error",
                                    "edge": edge.id,
                                    "condition": edge.condition,
                                    "error": str(exc),
                                },
                            )
                            return False

                    active_inputs = [edge_active(edge) for edge in node_edges]
                    if not node_edges:
                        should_run = True
                    elif node.join_mode == "all":
                        should_run = all(active_inputs)
                    elif node.join_mode == "success_all":
                        should_run = all(active_inputs) and all(
                            edge.source in successful_nodes for edge in node_edges
                        )
                    else:
                        should_run = any(active_inputs)
                    if not should_run:
                        self.runtime.skip_step(step_run, _INCOMING_CONDITIONS_SKIP_REASON)
                        return child, False, False
                    if self._halted(bb):
                        reason = str(bb.scratch.get(HALT_REASON_KEY) or "halted by agent")
                        self.runtime.skip_step(step_run, reason)
                        return child, False, False
                    if self._exhausted() and not self._is_terminal(step):
                        self.runtime.skip_step(step_run, "token budget exhausted")
                        return child, False, False
                    try:
                        child = await self._execute_with_policy(step, child, step_run)
                    except Exception as exc:
                        self.runtime.fail_step(step_run, exc)
                        self.ctx.tracer.emit(
                            self._stage_for(step),
                            "error",
                            f"{self._step_label(step)} 失败，已隔离：{exc}",
                        )
                        if step.failure_policy == "fail_fast":
                            raise
                    return child, True, step_run.status == StepStatus.SUCCEEDED

                tasks = [asyncio.create_task(execute_node(node)) for node in layer]

                def merge_outcomes(
                    outcomes: Sequence[object],
                    nodes_in_layer: tuple[WorkflowNode, ...] = layer_nodes,
                    layer_snapshot: Blackboard = layer_base,
                ) -> None:
                    for node, outcome in zip(nodes_in_layer, outcomes, strict=True):
                        if isinstance(outcome, BaseException):
                            continue
                        assert isinstance(outcome, tuple) and len(outcome) == 3
                        child, active, succeeded = outcome
                        if active:
                            active_nodes.add(node.id)
                            if succeeded:
                                successful_nodes.add(node.id)
                        else:
                            continue
                        _merge_parallel_blackboard(layer_snapshot, bb, child)

                try:
                    outcomes = await asyncio.gather(*tasks)
                except (Exception, asyncio.CancelledError):
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    settled = await asyncio.gather(*tasks, return_exceptions=True)
                    # A successful sibling is already durable runtime state. Its
                    # output must enter the failure checkpoint or resume would
                    # skip the node and lose that output permanently.
                    merge_outcomes(settled)
                    raise
                changed_result_branches = sum(
                    active and child.results != layer_base.results for child, active, _ in outcomes
                )
                merge_outcomes(list(outcomes))
                if changed_result_branches > 1:
                    await verify_claim_consistency(
                        bb.results,
                        ClaimConsistencyVerifier(),
                        self.ctx.llm_for("evidence_verifier"),
                        self.ctx.tracer,
                        stage="ORCHESTRATOR",
                    )
                if is_root:
                    await self._checkpoint(wf, bb)
            if is_root and self._require_report and bb.report is None:
                raise RuntimeError("工作流结束但未生成报告")
            return bb
        except asyncio.CancelledError:
            if is_root and self.runtime.run is not None:
                self.runtime.finish(RunStatus.CANCELLED)
                try:
                    await self._checkpoint(wf, bb)
                except LeaseLostError:
                    pass
            raise
        except Exception:
            if is_root and self.runtime.run is not None:
                self.runtime.finish(RunStatus.FAILED)
                try:
                    await self._checkpoint(wf, bb)
                except LeaseLostError:
                    # 租约已被继任者接管：终态 checkpoint 写不进去是预期情况，
                    # 不得让它掩盖原始失败异常（与上方取消路径的处理一致）。
                    pass
            raise
        finally:
            self._run_depth -= 1
            if is_root:
                self._resuming = False
            if (
                is_root
                and self.runtime.run is not None
                and self.runtime.run.status == RunStatus.RUNNING
            ):
                run = self.runtime.finish(
                    RunStatus.SUCCEEDED,
                    output={"has_report": bb.report is not None, "result_count": len(bb.results)},
                )
                bb.scratch["_orchestration_run"] = run.model_dump(
                    mode="json", exclude={"checkpoint", "definition", "steps"}
                )
                await self._checkpoint(wf, bb, publish_event=False)

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

        # 团队级不再自建独立信号量：昂贵操作（检索/LLM）在各团队内部的 researcher
        # 处经 ctx.run_semaphore（run 级共享）统一限流，总并发不随团队数放大。
        # 也不得在此持共享信号量跨整个子流程——子流程内 researcher 会再取同一把锁，
        # 团队数 ≥ max_concurrency 时将互相等待形成死锁。
        async def _run_team(idx: int, task: SubTask) -> Blackboard | None:
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
        await verify_claim_consistency(
            bb.results,
            ClaimConsistencyVerifier(),
            self.ctx.llm_for("evidence_verifier"),
            self.ctx.tracer,
            stage="AGGREGATOR",
        )
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
