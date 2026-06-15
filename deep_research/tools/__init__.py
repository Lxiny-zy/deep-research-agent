"""检索后端：抽象 + 具体实现。

只在此处暴露抽象 SearchTool；TavilySearch 由调用方按需导入，
这样 import deep_research 时不强制依赖 tavily（便于测试注入假实现）。
"""

from .base import SearchTool

__all__ = ["SearchTool"]
