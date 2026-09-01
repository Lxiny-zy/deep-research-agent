"""多 Agent 协作的统一地基：共享黑板（Blackboard）+ 运行上下文（RunContext）+ Agent 协议。

设计目标：让「角色」与「编排」彻底解耦。
  - 每个角色统一实现 `async step(bb, ctx) -> Blackboard`，读写同一块黑板；
    不再让编排器知道每个角色各自不同的方法签名（旧 Planner.run(query) /
    Reflector.run(query, results) 各不相同，无法被通用引擎调度）。
  - 新增角色（Critic / FactChecker / Coder…）只要实现该协议并注册，即可被
    工作流引擎调度，无需改动编排器源码。
  - Blackboard.scratch 给新角色一块自由挂数据的区域，避免每加一个角色就改核心 schema。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from ..config import Settings
from ..llm import LLM
from ..models import Reflection, Report, ResearchPlan, ResearchResult
from ..observability import Tracer
from ..prompting import compose_system_prompt, load_global_rules
from ..tools.base import SearchTool


class Blackboard(BaseModel):
    """单次运行的共享状态：所有角色读写它，逐步累积直到产出报告。

    把流程状态从「编排器局部变量」提升为「显式共享对象」，是声明式工作流引擎
    得以存在的前提——引擎只搬运黑板，不关心每个角色具体做了什么。
    """

    query: str
    plan: ResearchPlan | None = None
    results: list[ResearchResult] = Field(default_factory=list)
    reflections: list[Reflection] = Field(default_factory=list)
    report: Report | None = None
    # 角色私有数据挂这里（如 Critic 的评审意见、Coder 的产物），不污染核心字段
    scratch: dict[str, Any] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}


def effective_require_corroboration(bb: Blackboard, settings: Settings) -> bool:
    """Resolve the effective corroboration gate without widening run settings.

    Intent policies are optional checkpoint metadata. A policy may require
    stricter evidence, but it can never turn off an explicit user/deployment
    requirement or mutate the shared ``Settings`` object.
    """
    if settings.require_corroboration:
        return True
    raw = bb.scratch.get("intent_execution_policy")
    return isinstance(raw, dict) and raw.get("requires_corroboration") is True


class RunContext:
    """跨角色共享的运行期依赖（替代旧版散落在各角色构造函数里的重复参数）。

    llm_resolver 可选：给定角色名返回该角色专属的 LLM（数据驱动角色按卡片绑定的
    模型档案构造不同 LLM）。返回 None 或未提供时，角色用默认 ctx.llm 兜底。
    """

    def __init__(
        self,
        *,
        llm: LLM,
        search_tool: SearchTool,
        tracer: Tracer,
        settings: Settings,
        llm_resolver: Callable[[str], LLM | None] | None = None,
        # Optional execution capabilities are injected by the runner.  They
        # stay typed as ``Any`` here to avoid importing filesystem/process
        # adapters into every agent (and to keep the legacy constructor intact).
        artifact_store: Any | None = None,
        command_runner: Any | None = None,
        skill_resolver: Any | None = None,
        resource_policy: Any | None = None,
        run_id: str | None = None,
        artifact_slug: str | None = None,
        global_rules: str | None = None,
    ) -> None:
        self.llm = llm
        self.search_tool = search_tool
        self.tracer = tracer
        self.settings = settings
        self._llm_resolver = llm_resolver
        self.artifact_store = artifact_store
        self.command_runner = command_runner
        self.skill_resolver = skill_resolver
        self.resource_policy = resource_policy
        self.run_id = run_id
        self.artifact_slug = artifact_slug
        # ``None`` preserves the lightweight direct-agent API used by tests
        # and integrations. The orchestrator supplies the repository-wide
        # Vela rules for real runs.
        self.global_rules = global_rules

    def llm_for(self, agent_name: str) -> LLM:
        """取某角色应使用的 LLM：有专属档案则用之，否则回退默认。"""
        if self._llm_resolver is not None:
            resolved = self._llm_resolver(agent_name)
            if resolved is not None:
                return resolved
        return self.llm

    def system_prompt(self, role_prompt: str) -> str:
        """Render a role prompt with the run-wide orchestration rules."""

        return compose_system_prompt(role_prompt, self.global_rules)


def direct_system_prompt(role_prompt: str) -> str:
    """Render the shared policy for legacy direct-agent entry points.

    The workflow engine passes a :class:`RunContext` to ``step`` and therefore
    already uses ``RunContext.system_prompt``.  The compatibility ``run``
    methods remain public for CLI/evaluation callers, though, and do not have a
    context object.  Keeping this helper here gives those calls the same
    repository-wide Vela rules without mutating a role's custom prompt.
    """

    return compose_system_prompt(role_prompt, load_global_rules())


@runtime_checkable
class Agent(Protocol):
    """统一角色协议：消费并返回黑板。

    约定：step 原地更新并返回同一个 bb（便于链式与引擎复用）。角色内部的失败
    应自行隔离或经 tracer 记录，不应让单点失败炸穿整条工作流。
    """

    name: str

    async def step(self, bb: Blackboard, ctx: RunContext) -> Blackboard: ...
