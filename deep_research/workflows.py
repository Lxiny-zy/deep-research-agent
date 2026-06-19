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
    description="快速查询：直接检索并综合，省略规划与反思",
    steps=[
        Step(agent="planner"),
        Step(agent="researcher"),
        Step(agent="synthesizer"),
    ],
)

WORKFLOWS: dict[str, Workflow] = {wf.name: wf for wf in (DEEP, QUICK)}

# 带复核：完整深度研究后追加一个 Critic 角色做批判性复核。
# 注意：新增这条流程只是在 steps 里多写一行 "critic"——没有改引擎、没有改编排器。
REVIEWED = Workflow(
    name="reviewed",
    description="深度研究 + 报告复核：在综合后由 Critic 角色批判性复核",
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

WORKFLOWS = {wf.name: wf for wf in (DEEP, QUICK, REVIEWED, AUTO, TEAMS)}

DEFAULT_WORKFLOW = "deep"


def get_workflow(name: str | None) -> Workflow:
    """按名取工作流；未知名回退到默认，保证永不因拼写错误炸穿运行。"""
    if not name:
        return WORKFLOWS[DEFAULT_WORKFLOW]
    return WORKFLOWS.get(name, WORKFLOWS[DEFAULT_WORKFLOW])
