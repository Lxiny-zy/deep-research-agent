"""数据驱动角色：把数据库里的角色卡片（behavior + system_prompt + 绑定模型）
变成引擎可调度的 Agent。

behavior 决定复用哪个内置角色的执行逻辑；system_prompt（非空时）覆盖其默认提示词；
模型由 RunContext.llm_for(name) 按卡片绑定的档案解析。

这样「在角色广场新建一个角色」= 往 DB 插一行，无需写任何 Python 代码或改引擎。
"""

from __future__ import annotations

from .base import Agent, Blackboard, RunContext
from .critic import Critic
from .planner import Planner
from .reflector import Reflector
from .researcher import Researcher
from .synthesizer import Synthesizer

# behavior → 内置角色类（其 step/run 逻辑被复用）
_BEHAVIOR_IMPL: dict[str, type] = {
    "plan": Planner,
    "research": Researcher,
    "reflect": Reflector,
    "synthesize": Synthesizer,
    "critique": Critic,
}


def behavior_impls() -> dict[str, type]:
    return dict(_BEHAVIOR_IMPL)


class CardAgent:
    """按角色卡片驱动的角色。委托给 behavior 对应的内置角色实例，但用卡片的
    name（决定模型解析）与 system_prompt（决定提示词）。
    """

    def __init__(self, *, name: str, behavior: str, system_prompt: str = "") -> None:
        impl_cls = _BEHAVIOR_IMPL.get(behavior)
        if impl_cls is None:
            raise ValueError(f"未知 behavior：{behavior}（可选：{sorted(_BEHAVIOR_IMPL)}）")
        self.name = name
        self.behavior = behavior
        self._impl: Agent = impl_cls()  # 无参构造（依赖在 step 时经 ctx 注入）
        self._impl.name = name  # 让委托实例用卡片名解析专属模型（llm_for）
        # 卡片提供自定义提示词时覆盖内置默认；内置角色 step 只重绑依赖、不碰 system，
        # 故此处设置在整个生命周期有效。空 prompt＝沿用该 behavior 的内置默认。
        if system_prompt.strip():
            self._impl.system = system_prompt

    async def step(self, bb: Blackboard, ctx: RunContext) -> Blackboard:
        return await self._impl.step(bb, ctx)

    def __repr__(self) -> str:  # pragma: no cover
        return f"CardAgent(name={self.name!r}, behavior={self.behavior!r})"
