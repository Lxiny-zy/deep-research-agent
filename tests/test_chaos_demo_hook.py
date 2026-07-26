"""Chaos 演示注入钩子（api._build_agent + DR_DEMO_FAKE_BACKENDS）的冒烟测试。

保证两点：
  1. 默认（未设环境变量）钩子完全不生效——生产行为零变化；
  2. 开关打开时构造出的 agent 能完全离线跑通 deep 工作流并计入模拟 token
     （scripts/chaos_demo.py 的子进程正是依赖这条路径）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from deep_research import api
from deep_research.config import Settings


def test_demo_hook_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("DR_DEMO_FAKE_BACKENDS", raising=False)
    assert api._demo_fake_backends_enabled() is False
    for value in ("0", "no", "", "off"):
        monkeypatch.setenv("DR_DEMO_FAKE_BACKENDS", value)
        assert api._demo_fake_backends_enabled() is False
    monkeypatch.setenv("DR_DEMO_FAKE_BACKENDS", "1")
    assert api._demo_fake_backends_enabled() is True


async def test_demo_hook_builds_offline_agent_and_counts_tokens(monkeypatch) -> None:
    monkeypatch.setenv("DR_DEMO_FAKE_BACKENDS", "1")
    monkeypatch.setenv("DR_DEMO_STEP_DELAY", "0")  # 冒烟测试不需要人为放慢
    monkeypatch.setenv("DR_DEMO_TOKENS_PER_CALL", "1000")
    # 无任何真实 key：注入路径必须完全离线可跑
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    app = SimpleNamespace(state=SimpleNamespace(catalog=None))
    agent, search_tool = await api._build_agent(app, Settings(), workflow="deep")
    try:
        report = await agent.run("冒烟测试问题")
    finally:
        await agent.aclose()
        if search_tool is not None:
            await search_tool.aclose()
    assert report is not None and report.markdown
    # 每次 LLM 调用计 1000 模拟 token：deep 工作流至少含 planner/researcher/synthesizer 调用
    assert agent.tracer.total_tokens >= 3000


def test_chaos_demo_script_help_runs() -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "chaos_demo.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        timeout=60,
    )
    assert proc.returncode == 0
