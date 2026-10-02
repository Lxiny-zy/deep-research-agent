"""内置 Agent：各司其职，组合成深度研究流程。Critic 是「加角色不改引擎」的演示。"""

# 科研工作台角色（论文导入、各类写作者、数据分析）与内置角色同时注册，
# 放在末尾导入：它们依赖上面的 Researcher / base 已完成初始化。
from ..workbench import analysis as _workbench_analysis  # noqa: E402,F401
from ..workbench import attachment_reader as _workbench_attachments  # noqa: E402,F401
from ..workbench import intake as _workbench_intake  # noqa: E402,F401
from ..workbench import review_coverage as _workbench_review_coverage  # noqa: E402,F401
from ..workbench import writers as _workbench_writers  # noqa: E402,F401
from .aggregator import Aggregator
from .coordinator import Coordinator
from .critic import Critic
from .intent_router import IntentRouter
from .operation_runner import OperationRunnerAgent
from .plan_executor import PlanExecutor
from .planner import Planner
from .reflector import Reflector
from .researcher import Researcher
from .synthesizer import Synthesizer

__all__ = [
    "Planner",
    "Researcher",
    "Reflector",
    "Synthesizer",
    "Critic",
    "Coordinator",
    "Aggregator",
    "IntentRouter",
    "OperationRunnerAgent",
    "PlanExecutor",
]
