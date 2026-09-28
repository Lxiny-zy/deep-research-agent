"""命令行入口：python -m deep_research.cli "你的研究问题" """

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .config import Settings
from .observability import Event
from .orchestrator import DeepResearchAgent
from .planner_runtime import coerce_execution_plan

_ICON = {
    "start": ">",
    "info": "-",
    "finding": "+",
    "round": "~",
    "report": "#",
    "done": "OK",
    "error": "!!",
}


def _print(event: Event) -> None:
    icon = _ICON.get(event.type, "·")
    print(f"  [{event.elapsed:6.1f}s] {icon} [{event.stage:<12}] {event.message}")


async def _amain() -> None:
    parser = argparse.ArgumentParser(description="Deep Research 多 Agent 研究系统")
    parser.add_argument(
        "query",
        nargs="?",
        default="2026 年主流 AI Agent 框架有哪些？各自的设计取舍是什么？",
        help="研究问题",
    )
    parser.add_argument("-o", "--output", default="research_report.md", help="报告输出路径")
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "-w", "--workflow", default=None, help="任务流程（如 deep / quick）；默认 deep"
    )
    source.add_argument("--plan", type=Path, help="执行项目标准或 Vela 兼容的计划 JSON 文件")
    args = parser.parse_args()
    execution_plan = None
    if args.plan is not None:
        try:
            execution_plan = coerce_execution_plan(
                args.plan.read_text(encoding="utf-8-sig"), query=args.query, initial=True
            )
        except (OSError, ValueError) as exc:
            parser.error(f"无法加载执行计划：{exc}")

    print(f"\n研究问题：{args.query}\n")
    agent = DeepResearchAgent(Settings(), workflow=args.workflow, execution_plan=execution_plan)
    try:
        agent.tracer.subscribe(_print)  # 把事件实时打印到控制台
        report = await agent.run(args.query)

        print("\n" + "=" * 64)
        print(report.markdown)
        # 写文件是阻塞 IO，放到线程里执行，避免阻塞事件循环
        await asyncio.to_thread(_write_report, args.output, args.query, report.markdown)
        print(f"已保存：{args.output}")
    finally:
        await agent.aclose()


def _write_report(path: str, query: str, markdown: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# 研究问题：{query}\n\n{markdown}")


def main() -> None:
    # Windows 下 stdout/stderr 重定向到文件时编码常是 GBK（非 UTF-8 控制台同理），
    # emoji 图标与报告全文 print 会抛 UnicodeEncodeError 直接崩掉进程；
    # errors="replace" 让无法编码的字符降级为占位符（报告文件本身始终 UTF-8 写出，不受影响）。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
