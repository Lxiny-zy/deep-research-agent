"""内置 Agent：各司其职，组合成深度研究流程。Critic 是「加角色不改引擎」的演示。"""

from .critic import Critic
from .planner import Planner
from .reflector import Reflector
from .researcher import Researcher
from .synthesizer import Synthesizer

__all__ = ["Planner", "Researcher", "Reflector", "Synthesizer", "Critic"]
