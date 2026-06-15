"""角色注册表：声明式工作流引擎按名字查找角色，从而与具体角色实现解耦。

新增角色只需在其模块顶部用 @register("name") 装饰，并确保该模块被 import
（见 agents/__init__.py 的统一导入），即可被任意工作流按名引用，无需改引擎或编排器。

注册的是「无参可构造的角色工厂」：角色实例自身不持有依赖（llm/search/tracer），
依赖在 step(bb, ctx) 时经 ctx 注入，因此一个角色实例可被任意 run 复用。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agents.base import Agent

_REGISTRY: dict[str, Callable[[], Agent]] = {}


def register(name: str) -> Callable[[type], type]:
    """类装饰器：把一个 Agent 实现以 name 注册（要求可无参构造）。"""

    def deco(cls: type) -> type:
        if name in _REGISTRY:
            raise ValueError(f"角色名重复注册：{name}")
        _REGISTRY[name] = cls  # type: ignore[assignment]
        cls.name = name  # type: ignore[attr-defined]
        return cls

    return deco


def create(name: str) -> Agent:
    """按名实例化角色；未注册则报错并列出可用角色，便于排查工作流拼写错误。"""
    if name not in _REGISTRY:
        raise KeyError(f"未注册的角色：{name}（可用：{sorted(_REGISTRY)}）")
    return _REGISTRY[name]()


def available() -> list[str]:
    """已注册角色名列表（供 Coordinator 白名单、API 透出可选角色等）。"""
    return sorted(_REGISTRY)
