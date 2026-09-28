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

# ---- 科研工作台任务流程（见 workbench/templates.py）------------------------
# 调研类统一为「检索 → 核验 → 补洞 → 按模板章节写作」；论文类以用户点名的论文
# 为证据源（paper_intake），不做开放检索替换；数据分析与导图各自有专属终端角色。

LIT_REVIEW = Workflow(
    name="lit_review",
    description="文献综述：多子问题检索、证据补洞后按主题撰写带引用的结构化综述",
    steps=[
        Step(agent="attachment_reader"),
        Step(agent="planner"),
        Step(agent="researcher"),
        Step(kind="reflect_loop", reflector="reflector", researcher="researcher"),
        Step(agent="survey_writer"),
    ],
)

PEER_REVIEW = Workflow(
    name="peer_review",
    description="同行评审：取回指定论文并核验证据，按五段式给出评审意见与 1–10 分",
    steps=[
        Step(agent="attachment_reader"),
        Step(agent="paper_intake"),
        Step(agent="peer_reviewer"),
    ],
)

PAPER_READ = Workflow(
    name="paper_read",
    description="论文精读：取回指定论文并核验证据，输出贡献、方法、结果、局限与摘要翻译",
    steps=[Step(agent="attachment_reader"), Step(agent="paper_intake"), Step(agent="paper_reader")],
)

DATA_ANALYSIS = Workflow(
    name="data_analysis",
    description="数据分析：确定性统计与显著性检验、生成图表，并撰写只解释给定数字的报告",
    steps=[Step(agent="attachment_reader"), Step(agent="data_analyst")],
)

SLIDES = Workflow(
    name="slides",
    description="幻灯片：检索并核验要点，生成带演讲备注的 PPTX 演示文稿",
    steps=[
        Step(agent="attachment_reader"),
        Step(agent="planner"),
        Step(agent="researcher"),
        Step(agent="slide_writer"),
    ],
)

MINDMAP = Workflow(
    name="mindmap",
    description="思维导图：检索主题核心概念，整理为多分支知识结构并渲染交互导图",
    steps=[
        Step(agent="attachment_reader"),
        Step(agent="planner"),
        Step(agent="researcher"),
        Step(agent="mindmap_writer"),
    ],
)

# 同一任务的其它检索策略：交付角色不变，只替换「怎么找证据」这一段。
LIT_REVIEW_QUICK = Workflow(
    name="lit_review_quick",
    description="文献综述（快速检索）：检索一轮后按主题撰写综述，不做反思补洞",
    steps=[
        Step(agent="attachment_reader"),
        Step(agent="planner"),
        Step(agent="researcher"),
        Step(agent="survey_writer"),
    ],
)

SLIDES_DEEP = Workflow(
    name="slides_deep",
    description="幻灯片（深度检索）：多轮检索与证据补洞后生成演示文稿",
    steps=[
        Step(agent="attachment_reader"),
        Step(agent="planner"),
        Step(agent="researcher"),
        Step(kind="reflect_loop", reflector="reflector", researcher="researcher"),
        Step(agent="slide_writer"),
    ],
)

MINDMAP_DEEP = Workflow(
    name="mindmap_deep",
    description="思维导图（深度检索）：多轮检索补全概念后整理知识结构",
    steps=[
        Step(agent="attachment_reader"),
        Step(agent="planner"),
        Step(agent="researcher"),
        Step(kind="reflect_loop", reflector="reflector", researcher="researcher"),
        Step(agent="mindmap_writer"),
    ],
)

# 课题调研：与 deep / quick 同一条检索链，终端换成带质量管线的调研写作者。
# deep / quick 本身保持不变（CLI、评测与历史运行依赖它们的既有行为）。
RESEARCH = Workflow(
    name="research",
    description="课题调研（深度检索）：多子问题检索、反思补洞后按章节撰写并经质量返工",
    steps=[
        Step(agent="attachment_reader"),
        Step(agent="planner"),
        Step(agent="researcher"),
        Step(kind="reflect_loop", reflector="reflector", researcher="researcher"),
        Step(agent="research_writer"),
    ],
)

RESEARCH_QUICK = Workflow(
    name="research_quick",
    description="课题调研（快速检索）：检索一轮后按章节撰写并经质量返工",
    steps=[
        Step(agent="attachment_reader"),
        Step(agent="planner"),
        Step(agent="researcher"),
        Step(agent="research_writer"),
    ],
)

WORKBENCH_WORKFLOWS = (
    RESEARCH,
    RESEARCH_QUICK,
    LIT_REVIEW,
    LIT_REVIEW_QUICK,
    PEER_REVIEW,
    PAPER_READ,
    DATA_ANALYSIS,
    SLIDES,
    SLIDES_DEEP,
    MINDMAP,
    MINDMAP_DEEP,
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
        *WORKBENCH_WORKFLOWS,
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
