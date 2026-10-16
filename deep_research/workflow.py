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
import hashlib
import heapq
import json
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import uuid4

from pydantic import BaseModel, Field, TypeAdapter

from .execution_policy import COMMITTED_RESEARCH_PROGRESS_KEY, committed_research_progress
from .guardrails import ClaimConsistencyVerifier, report_eligible, verify_claim_consistency
from .models import ResearchResult, SubQuestion
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
from .prompting import load_global_rules
from .registry import available, create
from .research_progress import ResearchProgressError
from .workbench.roles import WORKBENCH_WRITER_ROLES

if TYPE_CHECKING:
    # 仅类型注解需要（本模块所有用法都在 `from __future__ import annotations` 的注解里）。
    # 放到 TYPE_CHECKING 下可切断 workflow → agents 包 → coordinator → workflow 的运行期循环导入。
    from .agents.base import Agent, Blackboard, RunContext
    from .token_budget import TokenBudget


def _carry_committed_research_progress(target: Blackboard, source: Blackboard) -> None:
    """Preserve durable progress without committing a failed candidate's content."""
    if progress := committed_research_progress(source.scratch):
        existing = committed_research_progress(target.scratch)
        target.scratch[COMMITTED_RESEARCH_PROGRESS_KEY] = {**existing, **progress}


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
    # Planner-driven adapters can carry declarative operation metadata without
    # changing the legacy agent fields.  The workflow engine treats this as
    # opaque data and never executes values from it as shell text.
    metadata: dict[str, Any] = Field(default_factory=dict)


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
_TERMINAL_ROLES = {"synthesizer", "aggregator", *WORKBENCH_WRITER_ROLES}
# 角色请求提前终止的黑板标记：任一角色把它设为真值，引擎即跳过后续非终端步骤。
# 通用原语而非「意图专用」——引擎不需要知道谁因为什么要求停下（当前使用方是
# IntentRouter 的风险拒识；将来的合规/配额检查可复用同一机制）。角色在设置它
# 之前必须自行产出 bb.report，否则 require_report 会判定运行失败。
HALT_SCRATCH_KEY = "_halt"
HALT_REASON_KEY = "_halt_reason"
# Intent routing writes a serializable execution policy into the checkpoint.  The
# engine deliberately treats this as an optional, duck-typed contract so old
# checkpoints and custom callers remain valid even when the intent package is
# disabled or unavailable.
INTENT_POLICY_SCRATCH_KEY = "intent_execution_policy"
# {子问题: 已验证发现数}：本次运行研究过的全部子问题（含零发现），由 Researcher 写入，
# 供反思循环去重、供 Reflector 看到「试过但无果」的方向。
ATTEMPTED_SCRATCH_KEY = "attempted_sub_questions"
# 子团队默认内部流程：在隔离子黑板上对其 focus 做一次检索（可被 SubTask.steps 覆盖）。
_DEFAULT_TEAM_STEPS = [Step(kind="agent", agent="researcher")]
_INCOMING_CONDITIONS_SKIP_REASON = "incoming conditions not matched"
_MISSING = object()
_GRAPH_STATE_KEY = "_graph_state"
_GRAPH_CONTROL_KEYS = frozenset(
    {
        "_deadline_at",
        "_task_deadline_at",
        "_task_model_call_scope",
        "_attempt_elapsed_origin",
        "_recovery",
        "_resource_wait",
        "_runtime_metrics",
        "_orchestration_run",
        COMMITTED_RESEARCH_PROGRESS_KEY,
    }
)
_DELTA_ADAPTER = TypeAdapter(dict[str, tuple[Any, Any]])


def _graph_control_keys() -> frozenset[str]:
    from .workbench.contract import CONTRACT_SCRATCH_KEY

    return _GRAPH_CONTROL_KEYS | {CONTRACT_SCRATCH_KEY}


def _result_fingerprint(result: ResearchResult) -> str:
    return hashlib.sha256(
        json.dumps(result.model_dump(mode="json"), sort_keys=True).encode()
    ).hexdigest()


def _policy_limit(bb: Blackboard, field: str, default: int) -> int:
    """Return a policy limit that can only tighten the caller's default."""
    raw = bb.scratch.get(INTENT_POLICY_SCRATCH_KEY)
    if not isinstance(raw, dict):
        return default
    value = raw.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    candidate = int(value)
    if candidate < 0:
        return default
    return min(default, candidate)


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


def _scratch_delta(base: dict[str, Any], branch: dict[str, Any]) -> tuple[dict, dict]:
    """Retain changed leaves instead of keeping whole branch checkpoints alive."""
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    for key in dict.fromkeys([*base, *branch]):
        old, new = base.get(key, _MISSING), branch.get(key, _MISSING)
        if old == new:
            continue
        if new is _MISSING:
            before[key] = None
        elif isinstance(old, dict) and isinstance(new, dict):
            before[key], after[key] = _scratch_delta(old, new)
        else:
            if old is not _MISSING:
                before[key] = old
            after[key] = new
    return before, after


def _blackboard_delta(base: Blackboard, branch: Blackboard) -> dict[str, tuple[Any, Any]]:
    delta: dict[str, tuple[Any, Any]] = {}
    controls = _graph_control_keys()
    for key in ("query", "plan", "report", "results", "reflections"):
        old, new = getattr(base, key), getattr(branch, key)
        if old == new:
            continue
        if key in {"results", "reflections"} and new[: len(old)] == old:
            # Append-only result branches need only their new items.
            delta[key] = ([], new[len(old) :])
        else:
            delta[key] = (old, new)
    before, after = _scratch_delta(
        {k: v for k, v in base.scratch.items() if k not in controls},
        {k: v for k, v in branch.scratch.items() if k not in controls},
    )
    if before or after:
        delta["scratch"] = (before, after)
    return delta


def _merge_graph_sequence(
    before: list[Any],
    current: list[Any],
    after: list[Any],
    keys: list[str],
    anchors: list[str],
    *,
    node_id: str,
    field: str,
) -> tuple[list[Any], list[str]]:
    """Apply edits to the producer's slots, not positions in an unrelated branch."""
    if len(anchors) != len(before) or len(keys) != len(current):
        raise ValueError("invalid graph sequence checkpoint")
    updates = {
        key: after[index]
        for index, key in enumerate(anchors)
        if index < len(after) and after[index] != before[index]
    }
    removed = set(anchors[len(after) :])
    merged, merged_keys = [], []
    for key, item in zip(keys, current, strict=True):
        if key not in removed:
            merged.append(deepcopy(updates.get(key, item)))
            merged_keys.append(key)
    # A later branch may deliberately restore a slot removed by an earlier one.
    for key, item in updates.items():
        if key not in merged_keys:
            merged.append(deepcopy(item))
            merged_keys.append(key)
    for index, item in enumerate(after[len(before) :]):
        merged.append(deepcopy(item))
        merged_keys.append(json.dumps([node_id, field, index]))
    return merged, merged_keys


def _apply_blackboard_delta(
    bb: Blackboard,
    delta: dict[str, tuple[Any, Any]],
    *,
    origins: dict[str, list[str]],
    anchors: dict[str, list[str]],
    node_id: str,
) -> None:
    for key, (before, after) in delta.items():
        if key in {"results", "reflections"}:
            values, origins[key] = _merge_graph_sequence(
                before,
                getattr(bb, key),
                after,
                origins[key],
                anchors[key],
                node_id=node_id,
                field=key,
            )
            setattr(bb, key, values)
        elif key == "scratch":
            bb.scratch = _merge_parallel_value(before, bb.scratch, after)
        else:
            setattr(bb, key, deepcopy(after))


def _load_blackboard_delta(raw: dict) -> dict[str, tuple[Any, Any]]:
    from .agents.base import Blackboard

    delta = _DELTA_ADAPTER.validate_python(raw)
    for key, pair in delta.items():
        if key not in Blackboard.model_fields:
            raise ValueError(f"unknown graph checkpoint field: {key}")
        adapter: TypeAdapter[Any] = TypeAdapter(Blackboard.model_fields[key].annotation)
        delta[key] = (adapter.validate_python(pair[0]), adapter.validate_python(pair[1]))
    return delta


def _eligible_finding_count(bb: Blackboard) -> int:
    return sum(
        1 for result in bb.results for finding in result.findings if report_eligible(finding)
    )


def _normalize_question(text: str) -> str:
    return "".join(text.casefold().split()).rstrip("?？。.")


def _unseen_sub_questions(
    bb: Blackboard, questions: list[str], search_queries: dict[str, list[str]] | None = None
) -> list[SubQuestion]:
    """过滤掉本次运行已研究过的子问题（含零发现的），并对新问题自身去重。"""
    seen = {_normalize_question(sq.question) for sq in (bb.plan.sub_questions if bb.plan else [])}
    seen.update(_normalize_question(result.sub_question) for result in bb.results)
    attempted = bb.scratch.get(ATTEMPTED_SCRATCH_KEY)
    if isinstance(attempted, dict):
        seen.update(_normalize_question(str(question)) for question in attempted)
    for raw_round in bb.scratch.get("reflection_rounds", []):
        if isinstance(raw_round, dict):
            for item in raw_round.get("sub_questions", []):
                question = item.question if isinstance(item, SubQuestion) else item.get("question")
                if question:
                    seen.add(_normalize_question(str(question)))
    fresh: list[SubQuestion] = []
    for question in questions:
        key = _normalize_question(question)
        if not key or key in seen:
            continue
        seen.add(key)
        fresh.append(
            SubQuestion(question=question, search_queries=(search_queries or {}).get(question, []))
        )
    return fresh


def _carry_replan_ledger(target: Blackboard, failed: Blackboard) -> None:
    """失败尝试的候选黑板会被丢弃，但其中的重规划账本不能跟着丢。

    账本记录已经花掉的补救额度与审计日志；若随失败的尝试一起消失，下一次重试
    又从零开始计数，``MAX_REPLANS`` / 每步一次补救的上限就形同虚设。
    """
    from .workbench.replan import REPLAN_SCRATCH_KEY

    ledger = failed.scratch.get(REPLAN_SCRATCH_KEY)
    if ledger is not None:
        target.scratch[REPLAN_SCRATCH_KEY] = deepcopy(ledger)


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
        if _step_produces_report(step, terminals):
            report_steps.append((node.id, step))
    if len(terminal_steps) == 1 and len(report_steps) == 1 and report_steps[0][0] not in outgoing:
        return []
    return ["所有工作流分支都必须汇入唯一的终端报告角色（如 Synthesizer）"]


def validate_workflow_steps_terminal(
    steps: list[Step], *, terminal_roles: set[str] | None = None
) -> list[str]:
    terminals = terminal_roles if terminal_roles is not None else _TERMINAL_ROLES
    report_indexes = [
        index for index, step in enumerate(steps) if _step_produces_report(step, terminals)
    ]
    if report_indexes == [len(steps) - 1]:
        return []
    return ["流程必须由唯一的终端角色（如 synthesizer）收尾"]


def _step_produces_report(step: Step, terminal_roles: set[str]) -> bool:
    """Return whether a step owns the report-producing terminal role.

    ``team_fanout`` invokes its aggregator after merging child blackboards, so the
    control step is terminal when that aggregator is a configured terminal role.
    Treating it like an explicit terminal agent keeps validation and budget
    exhaustion semantics consistent for both ``teams`` and bounded fan-out
    templates such as ``monitoring``.
    """
    if step.kind == "agent":
        # Generic external-plan executors are only report producers on the
        # authored terminal step.  Earlier steps use the same role name but
        # must remain subject to the normal budget/skip rules.
        if (
            step.agent in {"plan_executor", "operation_runner"}
            and step.metadata.get("is_terminal") is False
        ):
            return False
        return step.agent in terminal_roles
    if step.kind == "team_fanout":
        return step.aggregator in terminal_roles
    return False


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
        recover_transient: bool = False,
    ) -> None:
        self.ctx = ctx
        self._recover_transient = recover_transient
        # 角色解析器：默认从代码注册表取；编排器可注入「先查 DB 角色卡片，再回退注册表」
        # 的解析器，实现数据驱动角色与内置角色统一调度。
        self._resolve = resolver or create
        # token 预算（None＝不限）；以 ctx.tracer.total_tokens 为唯一真相源，避免双重计数。
        self.budget = budget
        self.runtime = OrchestrationRuntime(self._emit_runtime_event)
        self._run_depth = 0
        self._checkpoint_sink = checkpoint_sink
        self._graph_state: dict[str, Any] | None = None
        self._graph_repairs: dict[str, ResearchResult] = {}
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
        if self._graph_state is not None:
            checkpoint[_GRAPH_STATE_KEY] = self._graph_state
        if publish_event:
            run = self.runtime.save_checkpoint(checkpoint, definition)
        else:
            current_run = self.runtime.run
            assert current_run is not None
            current_run.checkpoint = checkpoint
            current_run.definition = definition
            run = current_run
        if self._checkpoint_sink is not None:
            # Other graph nodes may finish while the sink awaits I/O. Freeze
            # their statuses with the outputs actually included in this checkpoint.
            snapshot = run.model_copy(deep=True) if self._graph_state is not None else run
            await self._checkpoint_sink(snapshot)

    def _emit_runtime_event(self, name: str, data: dict) -> None:
        """把统一运行时生命周期桥接到已有 SSE/持久化事件流。"""
        if name == "checkpoint.saved":
            # Event.tokens 顶层字段不落库（见 observability.Event），跨进程回放一律读到 0。
            # 断点处的累计 token 是「续跑省了多少」的唯一依据，必须放进会持久化的 data，
            # 否则 execution_mode=worker 下这个数字永远无法审计。
            data = {**data, "total_tokens": self.ctx.tracer.total_tokens}
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
        # A planner-driven operation adapter reads this short-lived envelope
        # from the blackboard.  Keeping it on the candidate blackboard makes
        # parallel branches isolated; it is removed before the candidate is
        # committed so arbitrary planner metadata cannot accumulate forever.
        metadata_key = "_active_step_metadata"
        previous_metadata = bb.scratch.get(metadata_key, _MISSING)
        if step.metadata:
            bb.scratch[metadata_key] = deepcopy(step.metadata)
        result = bb
        try:
            if agent_override is not None:
                result = await self._resolve(agent_override).step(bb, self.ctx)
            elif step.kind == "reflect_loop":
                await self._reflect_loop(step, bb)
            elif step.kind == "compose":
                await self._compose(step, bb)
            elif step.kind == "team_fanout":
                await self._team_fanout(step, bb)
            else:
                result = await self._resolve(step.agent).step(bb, self.ctx)
            return result
        finally:
            # An agent is allowed to return a replacement Blackboard.  Clean
            # the transient metadata envelope on both objects; otherwise a
            # returned candidate could leak the previous step's prompt into a
            # checkpoint or a parallel branch.
            if previous_metadata is _MISSING:
                bb.scratch.pop(metadata_key, None)
                if result is not bb:
                    result.scratch.pop(metadata_key, None)
            else:
                bb.scratch[metadata_key] = previous_metadata
                if result is not bb:
                    result.scratch[metadata_key] = previous_metadata

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
                except (LeaseLostError, ResearchProgressError):
                    raise
                except Exception as exc:
                    last_error = exc
                    _carry_replan_ledger(bb, candidate)
                    from .execution_policy import resource_busy

                    if self._recover_transient and resource_busy(exc) is not None:
                        raise
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
                finally:
                    _carry_committed_research_progress(bb, candidate)

            if step.fallback_agent:
                candidate = bb.model_copy(deep=True)
                try:
                    candidate = await self._invoke_with_timeout(
                        step, candidate, agent_override=step.fallback_agent
                    )
                except (LeaseLostError, ResearchProgressError):
                    raise
                except Exception as exc:
                    last_error = exc
                else:
                    bb = _commit_blackboard(bb, candidate)
                    self.runtime.complete_step(step_run)
                    return bb
                finally:
                    _carry_committed_research_progress(bb, candidate)
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

    def _run_output(self, bb: Blackboard) -> dict[str, Any]:
        """运行终态摘要；有步骤被隔离失败时显式标记 degraded。

        failure_policy=continue 让单步失败不拖垮整条链路，但代价是「Planner 挂了、
        报告只剩一句无可用素材」的运行仍以 succeeded 收尾。监控只看状态会以为一切
        正常，所以把失败步骤列进 output，并发一条可检索的告警事件。
        """
        run = self.runtime.run
        failed = [
            step.label or step.node_id
            for step in (run.steps if run is not None else [])
            if step.status == StepStatus.FAILED
        ]
        if failed:
            self.ctx.tracer.emit(
                "ORCHESTRATOR",
                "info",
                f"运行以降级方式完成，失败步骤：{'、'.join(failed)}",
                data={"event_name": "run.degraded", "failed_steps": failed},
            )
        return {
            "has_report": bb.report is not None,
            "result_count": len(bb.results),
            "degraded": bool(failed),
            "failed_steps": failed,
        }

    async def repair_step(self, step: Step, bb: Blackboard) -> Blackboard:
        """Execute one auxiliary repair using this workflow's frozen resolver and budget."""
        run = self.runtime.run
        if run is None:
            raise RuntimeError("自动修订缺少工作流状态")
        if self._exhausted():
            from .token_budget import TokenBudgetExceeded

            raise TokenBudgetExceeded("总 token 预算已用尽")
        previous = run.status
        run.status = RunStatus.RUNNING
        attempt = self.runtime.create_step(
            node_id=f"completion-repair-{uuid4().hex[:12]}",
            label="交付前自动修订",
            kind="agent",
            agent=step.agent,
        )
        try:
            return await self._execute_with_policy(step, bb, attempt)
        except Exception as exc:
            self.runtime.fail_step(attempt, exc)
            raise
        finally:
            run.status = previous

    def _exhausted(self) -> bool:
        if self.budget is None:
            return False
        self.budget.update(self.ctx.tracer.total_tokens)
        return self.budget.exhausted

    def _is_terminal(self, step: Step) -> bool:
        """能产出报告的终端步骤：预算耗尽时仍执行，保证尽力而为的报告。"""
        return _step_produces_report(step, self._terminal_roles)

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
        # Every engine entry point gets the same Vela-derived rules. The
        # orchestrator supplies this eagerly for normal runs; lazy injection
        # keeps direct WorkflowEngine integrations consistent as well.
        if getattr(self.ctx, "global_rules", None) is None:
            self.ctx.global_rules = load_global_rules()
        from .workbench.quote_recovery import recover_blackboard_quotes

        previous = deepcopy(bb.results) if wf.nodes and self._run_depth == 0 else []
        await recover_blackboard_quotes(bb, self.ctx)
        if wf.nodes and self._run_depth == 0:
            self._graph_repairs = {
                _result_fingerprint(old): new.model_copy(deep=True)
                for old, new in zip(previous, bb.results, strict=True)
                if old != new
            }
        from .workbench.contract import CONTRACT_SCRATCH_KEY, contract_from_scratch
        from .workbench.coverage_review import effective_contract

        contract = contract_from_scratch(bb.scratch)
        if bb.scratch.get(CONTRACT_SCRATCH_KEY) is not None and contract is None:
            raise ValueError("研究任务契约无法解析，不能跳过冻结的用户要求")
        if contract is not None and contract.requirements_version == 0:
            bb.scratch[CONTRACT_SCRATCH_KEY] = effective_contract(contract, bb.scratch).model_dump(
                mode="json"
            )
        # run 级共享限流：并行图的 K 个检索型节点 / 多团队并行若各自建
        # Semaphore(max_concurrency)，总并发会被放大为 K×max_concurrency。
        # 引擎在 run 入口把信号量挂到 ctx 上（已存在则沿用，嵌套子流程共用同一把），
        # 角色未被注入时才自建，保证单测可脱离引擎独立运行。
        if getattr(self.ctx, "run_semaphore", None) is None:
            # A routed intent may request a narrower parallelism budget (for
            # example, fact checking should avoid a wide fan-out).  It can
            # never widen the user/run setting; the checkpoint value is
            # validated defensively because it may come from an older version.
            concurrency = _policy_limit(bb, "parallelism", self.ctx.settings.max_concurrency)
            self.ctx.run_semaphore = asyncio.Semaphore(  # type: ignore[attr-defined]
                max(1, concurrency)
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
                    from .execution_policy import transient_failure

                    if self._recover_transient and transient_failure(e):
                        raise
                    if isinstance(e, (LeaseLostError, ResearchProgressError)):
                        raise
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
                run = self.runtime.finish(RunStatus.SUCCEEDED, output=self._run_output(bb))
                bb.scratch["_orchestration_run"] = run.model_dump(
                    mode="json", exclude={"checkpoint", "definition", "steps"}
                )
                await self._checkpoint(wf, bb, publish_event=False)

    async def _run_graph(self, wf: Workflow, bb: Blackboard) -> Blackboard:
        """Run dependency-ready nodes with bounded tasks and deterministic inputs."""
        from .agents.base import Blackboard
        from .orchestration import evaluate_condition, graph_topo_layers

        nodes = [WorkflowNode.model_validate(raw) for raw in wf.nodes]
        edges = [WorkflowEdge.model_validate(raw) for raw in wf.edges]
        layers = graph_topo_layers(nodes, edges)
        ordered = [node.id for layer in layers for node in layer]
        order = {node_id: index for index, node_id in enumerate(ordered)}
        levels = {node.id: index for index, layer in enumerate(layers) for node in layer}
        by_id = {node.id: node for node in nodes}
        incoming: dict[str, list[WorkflowEdge]] = {node.id: [] for node in nodes}
        outgoing: dict[str, set[str]] = {node.id: set() for node in nodes}
        dependencies: dict[str, set[str]] = {node.id: set() for node in nodes}
        for edge in edges:
            incoming[edge.target].append(edge)
            outgoing[edge.source].add(edge.target)
            dependencies[edge.target].add(edge.source)
        ancestors: dict[str, frozenset[str]] = {}
        for node_id in ordered:
            parents = dependencies[node_id]
            ancestors[node_id] = frozenset(parents).union(*(ancestors[p] for p in parents))
        is_root = self._run_depth == 0
        restored = {
            step.node_id: step
            for step in (
                self.runtime.run.steps
                if is_root and self._resuming and self.runtime.run is not None
                else []
            )
        }
        completed = {
            node_id
            for node_id, step in restored.items()
            if node_id in by_id
            and (
                step.status == StepStatus.SUCCEEDED
                or (
                    step.status == StepStatus.SKIPPED
                    and step.error != _INCOMING_CONDITIONS_SKIP_REASON
                )
            )
        }
        active_nodes = {n for n in completed if restored[n].status == StepStatus.SUCCEEDED}
        successful_nodes = set(active_nodes)
        definition_hash = hashlib.sha256(
            json.dumps([wf.name, wf.nodes, wf.edges], sort_keys=True).encode()
        ).hexdigest()
        saved = (
            self.runtime.run.checkpoint.get(_GRAPH_STATE_KEY)
            if is_root and self._resuming and self.runtime.run is not None
            else None
        )
        deltas: dict[str, dict[str, tuple[Any, Any]]] = {}
        sequence_inputs: dict[str, dict[str, list[str]]] = {}
        repairs: dict[str, ResearchResult] = {}
        if isinstance(saved, dict):
            if saved.get("version") != 1 or saved.get("definition") != definition_hash:
                raise ValueError("graph checkpoint does not match the workflow")
            baseline = Blackboard.model_validate(saved["baseline"])
            deltas = {
                node_id: _load_blackboard_delta(raw)
                for node_id, raw in saved.get("deltas", {}).items()
                if node_id in active_nodes
            }
            sequence_inputs = {
                node_id: value
                for node_id, value in saved.get("sequence_inputs", {}).items()
                if node_id in active_nodes
            }
            repairs = {
                key: ResearchResult.model_validate(value)
                for key, value in saved.get("repairs", {}).items()
            }
        else:
            # Pre-journal checkpoints already contain their completed nodes'
            # outputs. Adopt them as the baseline without replaying those nodes.
            baseline = bb.model_copy(deep=True)
        if is_root:
            repairs.update(self._graph_repairs)
        baseline_payload = baseline.model_dump(mode="json")

        def normalize(state: Blackboard) -> None:
            if not repairs:
                return
            updated = []
            for result in state.results:
                seen: set[str] = set()
                key = _result_fingerprint(result)
                while key in repairs and key not in seen:
                    seen.add(key)
                    result = repairs[key].model_copy(deep=True)
                    key = _result_fingerprint(result)
                updated.append(result)
            state.results = updated

        def compose(
            scope: set[str] | frozenset[str],
        ) -> tuple[Blackboard, dict[str, list[str]]]:
            state = baseline.model_copy(deep=True)
            origins = {
                field: [json.dumps([None, field, i]) for i in range(len(getattr(state, field)))]
                for field in ("results", "reflections")
            }
            for node_id in ordered:
                if node_id in scope and node_id in deltas:
                    if node_id not in sequence_inputs:
                        # Upgrade journals written before sequence origins were recorded.
                        _, inherited = compose(ancestors[node_id])
                        sequence_inputs[node_id] = {
                            field: inherited[field] if pair[0] else []
                            for field, pair in deltas[node_id].items()
                            if field in {"results", "reflections"}
                        }
                    _apply_blackboard_delta(
                        state,
                        deltas[node_id],
                        origins=origins,
                        anchors=sequence_inputs[node_id],
                        node_id=node_id,
                    )
            # Deadlines, wait counters and durable progress belong to the run,
            # not to a branch's historical input snapshot.
            for key in _graph_control_keys():
                if key in bb.scratch:
                    state.scratch[key] = deepcopy(bb.scratch[key])
                else:
                    state.scratch.pop(key, None)
            normalize(state)
            return state, origins

        initial, committed_origins = compose(completed)
        _commit_blackboard(bb, initial)
        committed_order = [n for n in ordered if n in completed]

        def synchronize() -> None:
            nonlocal committed_order, committed_origins
            current_order = [n for n in ordered if n in completed]
            if current_order[: len(committed_order)] == committed_order:
                for node_id in current_order[len(committed_order) :]:
                    if node_id in deltas:
                        _apply_blackboard_delta(
                            bb,
                            deltas[node_id],
                            origins=committed_origins,
                            anchors=sequence_inputs[node_id],
                            node_id=node_id,
                        )
            else:
                # A slow earlier branch finished after a later branch. Reapply
                # sparse deltas in topology order so timing cannot choose winners.
                rebuilt, committed_origins = compose(completed)
                _commit_blackboard(bb, rebuilt)
            normalize(bb)
            committed_order = current_order
            if is_root:
                self._graph_state = {
                    "version": 1,
                    "definition": definition_hash,
                    "baseline": baseline_payload,
                    "sequence_inputs": deepcopy(sequence_inputs),
                    "repairs": {
                        key: value.model_dump(mode="json") for key, value in repairs.items()
                    },
                    "deltas": {
                        node_id: _DELTA_ADAPTER.dump_python(delta, mode="json")
                        for node_id, delta in deltas.items()
                    },
                }

        verification_lock = asyncio.Lock()
        verified: dict[str, Any] = {}

        async def verify_scope(state: Blackboard, scope: set[str] | frozenset[str]) -> None:
            writers = [n for n in ordered if n in scope and "results" in deltas.get(n, {})]
            if not any(
                a not in ancestors[b] for i, a in enumerate(writers) for b in writers[i + 1 :]
            ):
                return

            def signature() -> str:
                return hashlib.sha256(
                    json.dumps(
                        [result.model_dump(mode="json") for result in state.results],
                        sort_keys=True,
                    ).encode()
                ).hexdigest()

            async with verification_lock:
                key = signature()
                if key in verified:
                    state.results = deepcopy(verified[key])
                    return
                state.results = deepcopy(state.results)
                await verify_claim_consistency(
                    state.results,
                    ClaimConsistencyVerifier(),
                    self.ctx.llm_for("evidence_verifier"),
                    self.ctx.tracer,
                    stage="ORCHESTRATOR",
                )
                verified[key] = deepcopy(state.results)
                verified[signature()] = verified[key]
                while len(verified) > 8:
                    verified.pop(next(iter(verified)))

        remaining = {n: len(dependencies[n] - completed) for n in ordered}
        ready = [(order[n], n) for n in ordered if n not in completed and remaining[n] == 0]
        heapq.heapify(ready)
        running: dict[asyncio.Task, str] = {}
        inputs: dict[str, Blackboard] = {}
        input_origins: dict[str, dict[str, list[str]]] = {}
        children: dict[str, Blackboard] = {}
        limit = self.ctx.settings.workflow_max_concurrency
        repair_lock = asyncio.Lock()

        async def repair_input(state: Blackboard) -> None:
            from .workbench.quality import policy_from
            from .workbench.quote_recovery import needs_quote_repair, recover_blackboard_quotes

            if not self._resuming:
                return
            async with repair_lock:
                normalize(state)
                maximum = policy_from(self.ctx.settings).max_evidence_quote_chars
                if not any(needs_quote_repair(result, maximum) for result in state.results):
                    return
                old = deepcopy(state.results)
                state.results = deepcopy(state.results)
                await recover_blackboard_quotes(state, self.ctx)
                for before, after in zip(old, state.results, strict=True):
                    if before != after:
                        repairs[_result_fingerprint(before)] = after.model_copy(deep=True)

        async def execute_node(
            node: WorkflowNode, child: Blackboard
        ) -> tuple[Blackboard, bool, bool]:
            step = Step.model_validate(node.step)
            step_run = self.runtime.create_step(
                node_id=node.id if is_root else f"nested-{uuid4()}",
                label=self._step_label(step),
                kind=step.kind,
                agent=step.agent,
            )

            def edge_active(edge: WorkflowEdge) -> bool:
                if edge.source not in active_nodes:
                    return False
                try:
                    return evaluate_condition(edge.condition, child)
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

            # Conditions and consumers must see the same verified dependency set.
            await repair_input(child)
            await verify_scope(child, ancestors[node.id])
            node_edges = incoming[node.id]
            active_inputs = [edge_active(edge) for edge in node_edges]
            if not node_edges:
                should_run = True
            elif node.join_mode == "all":
                should_run = all(active_inputs)
            elif node.join_mode == "success_all":
                should_run = all(active_inputs) and all(
                    e.source in successful_nodes for e in node_edges
                )
            else:
                should_run = any(active_inputs)
            if not should_run:
                self.runtime.skip_step(step_run, _INCOMING_CONDITIONS_SKIP_REASON)
                return child, False, False
            if self._halted(bb):
                self.runtime.skip_step(
                    step_run, str(bb.scratch.get(HALT_REASON_KEY) or "halted by agent")
                )
                return child, False, False
            if self._exhausted() and not self._is_terminal(step):
                self.runtime.skip_step(step_run, "token budget exhausted")
                return child, False, False
            try:
                child = await self._execute_with_policy(step, child, step_run)
            except Exception as exc:
                self.runtime.fail_step(step_run, exc)
                from .execution_policy import transient_failure

                if self._recover_transient and transient_failure(exc):
                    raise
                if isinstance(exc, (LeaseLostError, ResearchProgressError)):
                    raise
                self.ctx.tracer.emit(
                    self._stage_for(step),
                    "error",
                    f"{self._step_label(step)} 失败，已隔离：{exc}",
                )
                if step.failure_policy == "fail_fast":
                    raise
            return child, True, step_run.status == StepStatus.SUCCEEDED

        def collect_finished() -> BaseException | None:
            failure: BaseException | None = None
            for task in sorted((t for t in running if t.done()), key=lambda t: order[running[t]]):
                node_id = running.pop(task)
                child, base = children.pop(node_id), inputs.pop(node_id)
                origins = input_origins.pop(node_id)
                _carry_committed_research_progress(bb, child)
                try:
                    outcome, active, succeeded = task.result()
                except BaseException as exc:
                    if failure is None or isinstance(exc, asyncio.CancelledError):
                        failure = exc
                    continue
                completed.add(node_id)
                if active:
                    active_nodes.add(node_id)
                    if succeeded:
                        successful_nodes.add(node_id)
                    deltas[node_id] = _blackboard_delta(base, outcome)
                    sequence_inputs[node_id] = {
                        field: origins[field] if pair[0] else []
                        for field, pair in deltas[node_id].items()
                        if field in {"results", "reflections"}
                    }
                for successor in outgoing[node_id]:
                    if successor not in completed and remaining[successor] > 0:
                        remaining[successor] -= 1
                        if remaining[successor] == 0:
                            heapq.heappush(ready, (order[successor], successor))
            synchronize()
            return failure

        if is_root:
            self.runtime.start(wf.name, {"query": bb.query})
            self._emit_workflow_plan(wf)
        synchronize()
        self._run_depth += 1
        try:
            try:
                while ready or running:
                    snapshots: dict[frozenset[str], tuple[Blackboard, dict[str, list[str]]]] = {}
                    dispatched: dict[int, list[str]] = {}
                    while ready and len(running) < limit:
                        _, node_id = heapq.heappop(ready)
                        scope = ancestors[node_id]
                        if scope not in snapshots:
                            snapshots[scope] = compose(scope)
                        base, origins = snapshots[scope]
                        # Policy execution already copies each mutable attempt;
                        # only its ledger needs a separate outer scratch mapping.
                        child = base.model_copy(update={"scratch": dict(base.scratch)})
                        inputs[node_id], children[node_id] = base, child
                        input_origins[node_id] = origins
                        task = asyncio.create_task(execute_node(by_id[node_id], child))
                        running[task] = node_id
                        dispatched.setdefault(levels[node_id], []).append(node_id)
                    snapshots.clear()
                    for level, dispatched_nodes in dispatched.items():
                        self.ctx.tracer.emit(
                            "ORCHESTRATOR",
                            "info",
                            f"图层 {level + 1} 就绪节点 {len(dispatched_nodes)} 个",
                            data={
                                "graph_layer": level,
                                "nodes": dispatched_nodes,
                                "active_nodes": len(running),
                                "max_nodes": limit,
                            },
                        )
                    await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
                    failure = collect_finished()
                    if failure is not None:
                        raise failure
                    if is_root:
                        await self._checkpoint(wf, bb)
                await verify_scope(bb, completed)
            except BaseException:
                for task in running:
                    task.cancel()
                await asyncio.gather(*running, return_exceptions=True)
                # Include completed siblings and verified progress before the
                # failure checkpoint; never persist a successful step alone.
                collect_finished()
                raise
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
                run = self.runtime.finish(RunStatus.SUCCEEDED, output=self._run_output(bb))
                bb.scratch["_orchestration_run"] = run.model_dump(
                    mode="json", exclude={"checkpoint", "definition", "steps"}
                )
                self._graph_state = None
                await self._checkpoint(wf, bb, publish_event=False)
            if is_root:
                self._graph_state = None
                self._graph_repairs = {}

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

        if self._exhausted():
            self.ctx.tracer.emit(
                "ORCHESTRATOR",
                "info",
                "token 预算耗尽，跳过团队检索并直接执行归并终端",
            )
            await self._resolve(step.aggregator).step(bb, self.ctx)
            return

        subtasks = self._collect_subtasks(bb, step.max_teams)
        if not subtasks:
            self.ctx.tracer.emit("ORCHESTRATOR", "info", "无子任务可分派，直接生成归并报告")
            # team_fanout 的 aggregator 是声明上的报告终端。即使 Planner
            # 返回空计划或前序步骤因预算被跳过，也必须实际执行终端角色，
            # 让 require_report 得到一份说明性而非直接失败的结果。
            await self._resolve(step.aggregator).step(bb, self.ctx)
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
            # Team children are isolated for data writes, but execution policy
            # is a run-level contract and must follow them into nested steps.
            policy = bb.scratch.get(INTENT_POLICY_SCRATCH_KEY)
            if isinstance(policy, dict):
                child.scratch[INTENT_POLICY_SCRATCH_KEY] = deepcopy(policy)
            child.scratch["pending_sub_questions"] = [SubQuestion(question=task.focus)]
            team_steps = task.steps or _DEFAULT_TEAM_STEPS
            try:
                await self.run(Workflow(name=f"team-{idx}", steps=list(team_steps)), child)
                return child
            except (LeaseLostError, ResearchProgressError):
                raise
            except Exception as e:  # 单团队失败隔离：记一条 error，返回 None 不拖垮其余团队
                self.ctx.tracer.emit("AGGREGATOR", "error", f"子团队 {idx} 失败，已隔离：{e}")
                return None

        tasks = [asyncio.create_task(_run_team(i, t)) for i, t in enumerate(subtasks)]
        try:
            children = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
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
        configured_rounds = (
            step.max_rounds if step.max_rounds is not None else self.ctx.settings.max_rounds
        )
        rounds = _policy_limit(bb, "max_rounds", configured_rounds)
        # 每一轮都是独立的提交单元：本轮在副本上执行，成功才并回 bb。整个循环是
        # 一个 step，若让异常逃出去，_execute_with_policy 会丢弃整块候选黑板——
        # 前几轮已经补到、验证过的证据会连同失败的这一轮一起消失。因此轮内失败
        # 一律视为「无法继续补洞」并停在最后一次成功提交的状态上。
        for rnd in range(rounds):
            if self._exhausted():
                self.ctx.tracer.emit("ORCHESTRATOR", "info", "token 预算耗尽，停止反思补洞")
                break
            before = _eligible_finding_count(bb)
            candidate = bb.model_copy(deep=True)
            try:
                candidate = await reflector.step(candidate, self.ctx)
            except (LeaseLostError, ResearchProgressError):
                raise  # 租约已被接管：本 worker 必须立刻停手，不能当作普通失败继续付费调用
            except Exception as exc:
                self.ctx.tracer.emit(
                    "REFLECTOR", "error", f"第 {rnd + 1} 轮反思失败，保留已有证据进入综合：{exc}"
                )
                break
            reflection = candidate.reflections[-1] if candidate.reflections else None
            if reflection is None or reflection.is_sufficient or not reflection.new_sub_questions:
                _commit_blackboard(bb, candidate)
                break
            new_subs = _unseen_sub_questions(
                candidate, reflection.new_sub_questions, reflection.search_queries
            )
            if not new_subs:
                # 反思提出的全是已研究过的问题：再跑只会重复同样的检索与抽取。
                _commit_blackboard(bb, candidate)
                self.ctx.tracer.emit("REFLECTOR", "info", "新增子问题均已研究过，停止补洞")
                break
            self.ctx.tracer.emit("ORCHESTRATOR", "round", f"第 {rnd + 1} 轮补洞")
            # 记录补洞轮次，供编排器落库（origin="reflection"）；不影响无 repo 运行
            candidate.scratch.setdefault("reflection_rounds", []).append(
                {"round": rnd + 1, "sub_questions": new_subs}
            )
            # 把补洞子问题交给 researcher 增量研究（经 scratch 传递，不重研已做过的）
            candidate.scratch["pending_sub_questions"] = new_subs
            try:
                candidate = await researcher.step(candidate, self.ctx)
            except (LeaseLostError, ResearchProgressError):
                raise
            except Exception as exc:
                self.ctx.tracer.emit(
                    "RESEARCHER", "error", f"第 {rnd + 1} 轮补洞研究失败，保留已有证据：{exc}"
                )
                break
            _commit_blackboard(bb, candidate)
            gained = _eligible_finding_count(bb) - before
            if gained <= 0:
                # 收敛判据：这一轮没有带来任何可进报告的新证据，继续补洞的边际收益为零。
                self.ctx.tracer.emit("ORCHESTRATOR", "info", "本轮未新增合格证据，停止补洞")
                break
