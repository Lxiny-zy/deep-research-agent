"""内置工作流注册表：把「任务类型 → 流程」做成一张可查的表（L2 路由的落点）。

新增一种任务流程：在这里声明一个 Workflow 并加进 WORKFLOWS 即可，无需改引擎或编排器。
API / CLI 经 workflow 名选择流程；缺省走 "deep"（与重构前行为完全一致）。
"""

from __future__ import annotations

from .workflow import Step, Workflow

# 深度研究（默认）：规划 → 研究 → 反思补洞循环 → 综合。等价于重构前写死的流程。
DEEP = Workflow(
    name="deep",
    description="完整深度研究：规划、并行检索、反思补洞、综合成报告",
    steps=[
        Step(agent="planner"),
        Step(agent="researcher"),
        Step(kind="reflect_loop", reflector="reflector", researcher="researcher"),
        Step(agent="synthesizer"),
    ],
)

# 快速查询：跳过规划与反思，直接研究并综合（适合简单、单点问题，省时省 token）。
QUICK = Workflow(
    name="quick",
    description="快速查询：规划后直接检索并综合，省略反思补洞",
    steps=[
        Step(agent="planner"),
        Step(agent="researcher"),
        Step(agent="synthesizer"),
    ],
)

# 轻量简报：与 quick 共享最短的研究链，但以独立名称暴露给意图路由和前端，
# 便于后续替换为专门的简报角色而不改变调用方契约。
BRIEF = Workflow(
    name="brief",
    description="轻量研究简报：规划、检索后直接生成短报告，不做反思补洞",
    steps=[
        Step(agent="planner"),
        Step(agent="researcher"),
        Step(agent="synthesizer"),
    ],
)

# 带复核：完整深度研究后追加一个 Critic 角色做批判性复核。
# 注意：新增这条流程只是在 steps 里多写一行 "critic"——没有改引擎、没有改编排器。
REVIEWED = Workflow(
    name="reviewed",
    description="深度研究 + 报告复核：在综合后由 Critic 角色批判性复核",
    steps=[*DEEP.steps, Step(agent="critic")],
)

# AI4S/HSI literature review keeps the established deep chain and adds the
# existing critic role as a final audit stage.
HSI_REVIEW = Workflow(
    name="hsi_review",
    description="AI4S/HSI 文献审查：沿用深度证据链并追加批判性复核",
    steps=[*DEEP.steps, Step(agent="critic")],
)

# 自组合（L3）：Coordinator 角色在运行时按问题现场生成流程，引擎递归执行。
# 这一条同样没有改引擎——compose 是引擎的一类控制原语，Coordinator 是一个普通注册角色。
AUTO = Workflow(
    name="auto",
    description="自组合：Coordinator 运行时按问题生成研究流程（动态组队）",
    steps=[Step(kind="compose", agent="coordinator")],
)

# 多团队并行（L4）：planner 切出子主题后，team_fanout 把每个子主题分给隔离的子团队
# 并行研究，最后 aggregator 归并成统一报告（map-reduce）。同样未改引擎——只是新控制原语 + 新角色。
TEAMS = Workflow(
    name="teams",
    description="多团队并行：规划子主题 → 隔离子团队各自检索 → 归并成报告（map-reduce）",
    steps=[Step(agent="planner"), Step(kind="team_fanout", aggregator="aggregator")],
)

# 历史兼容流程：全局引擎门禁已经在工作流启动前执行；这里保留
# ``intent_router`` 仅用于旧 checkpoint/显式调用的兼容，不是公共模板或安全保证。
GUARDED = Workflow(
    name="guarded",
    description="意图门禁 + 深度研究：先识别任务/风险意图，拒识高危请求，再按意图执行",
    steps=[Step(agent="intent_router"), *DEEP.steps],
)

# 事实核查：保留一轮证据反思，限制补洞成本；最终仍由 Synthesizer 产出可引用报告。
FACT_CHECK = Workflow(
    name="fact_check",
    description="事实核查：规划、检索并进行一轮证据补洞后生成带引用报告",
    steps=[
        Step(agent="planner"),
        Step(agent="researcher"),
        Step(kind="reflect_loop", reflector="reflector", researcher="researcher", max_rounds=1),
        Step(agent="synthesizer"),
    ],
)

# 动态监测：按规划切分主题后限量并行，避免监测类请求无限扩大团队数量；Aggregator
# 是 team_fanout 的终端角色，产出与 teams 相同的可引用报告。
MONITORING = Workflow(
    name="monitoring",
    description="动态监测：规划监测面并行检索，最多四个子团队后归并报告",
    steps=[
        Step(agent="planner"),
        Step(kind="team_fanout", aggregator="aggregator", max_teams=4),
    ],
)

WORKFLOWS = {
    wf.name: wf
    for wf in (
        DEEP,
        QUICK,
        BRIEF,
        REVIEWED,
        HSI_REVIEW,
        AUTO,
        TEAMS,
        GUARDED,
        FACT_CHECK,
        MONITORING,
    )
}

DEFAULT_WORKFLOW = "deep"

# User-facing templates are deliberately small. Users choose a research
# outcome (or the HSI-specific application), not implementation details such
# as fan-out, a critic-only pass, or the global safety gate.
PUBLIC_WORKFLOW_NAMES = ("deep", "quick", "hsi_review")
PUBLIC_WORKFLOWS = {name: WORKFLOWS[name] for name in PUBLIC_WORKFLOW_NAMES}


def is_public_workflow(name: str | None) -> bool:
    """Return whether ``name`` is a selectable built-in template."""

    return isinstance(name, str) and name in PUBLIC_WORKFLOWS

# ``guarded`` was the original way to opt into the intent gate. The gate is
# now applied by ``DeepResearchAgent`` before every workflow (including custom
# and planner-authored workflows), so exposing a second guarded copy only
# creates two competing concepts in the UI. Keep the complete registry for
# checkpoint/CLI compatibility, but give product surfaces an explicit small
# allow-list.
RESERVED_WORKFLOW_NAMES = frozenset({*WORKFLOWS, "guarded"})


def public_workflows() -> dict[str, Workflow]:
    """Return built-in workflows intended for user selection/template cards."""

    return {name: WORKFLOWS[name] for name in PUBLIC_WORKFLOW_NAMES if name in WORKFLOWS}


def get_workflow(name: str | None) -> Workflow:
    """按名取工作流；未知名回退到默认，保证永不因拼写错误炸穿运行。"""
    if not name:
        return WORKFLOWS[DEFAULT_WORKFLOW]
    return WORKFLOWS.get(name, WORKFLOWS[DEFAULT_WORKFLOW])
