"""人话进度叙述：把机器事件流压成用户读得懂的几段进展。

事件流是给系统与审计用的（每次检索、每次核验、每个 token 都是一条），直接
展示给用户就是一面滚不完的日志墙。叙述层做两件事：

1. **分段**：按阶段（理解任务 → 检索与核验 → 补洞 → 撰写 → 交付）聚合事件；
2. **摘要**：每段一句话，只报数字与结论（检索了几个子问题、保留多少条证据、
   拦截多少来源、补了几轮洞），不复述过程细节。

叙述是事件的纯函数，不调用模型：同一份事件永远得到同一段叙述，历史回放与
实时运行看到的完全一致；按 ``after_seq`` 增量取也只需在服务端重算一次。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..observability import Event

_PHASES = (
    ("understand", "理解任务"),
    ("research", "检索与核验证据"),
    ("reflect", "反思补洞"),
    ("write", "撰写交付物"),
    ("finish", "完成"),
)
_STAGE_PHASE = {
    "INTENT": "understand",
    "INTENT_ROUTER": "understand",
    "PLANNER": "understand",
    "RESEARCHER": "research",
    "REFLECTOR": "reflect",
    "SYNTHESIZER": "write",
    "AGGREGATOR": "write",
    "CRITIC": "write",
    "PLAN_EXECUTOR": "write",
}


@dataclass
class NarrativeSection:
    key: str
    title: str
    status: str = "pending"  # pending | active | done | error
    lines: list[str] = field(default_factory=list)
    first_seq: int | None = None
    last_seq: int | None = None
    elapsed: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "status": self.status,
            "lines": self.lines,
            "first_seq": self.first_seq,
            "last_seq": self.last_seq,
            "elapsed": round(self.elapsed, 1),
        }


def _phase(event: Event) -> str | None:
    if event.stage == "ORCHESTRATOR":
        if event.type in {"done", "error", "cancelled"}:
            return "finish"
        if event.type == "round":
            return "reflect"
        return None
    return _STAGE_PHASE.get(event.stage, "research" if event.stage.startswith("RESEARCH") else None)


def build_narrative(events: list[Event], *, run_status: str = "running") -> dict[str, Any]:
    sections = {key: NarrativeSection(key, title) for key, title in _PHASES}
    counters: dict[str, int] = {
        "sub_questions": 0,
        "verified": 0,
        "candidates": 0,
        "blocked": 0,
        "allowed": 0,
        "rounds": 0,
        "errors": 0,
        "paper_sections": 0,
    }
    last_seq = -1
    for event in events:
        if event.type == "token":
            continue
        phase = _phase(event)
        if event.seq is not None:
            last_seq = max(last_seq, event.seq)
        if phase is None:
            continue
        section = sections[phase]
        if section.first_seq is None:
            section.first_seq = event.seq
        section.last_seq = event.seq
        section.elapsed = event.elapsed
        section.status = "active" if section.status == "pending" else section.status
        data = event.data if isinstance(event.data, dict) else {}
        if event.type == "error":
            counters["errors"] += 1
            section.lines.append(f"遇到问题并已隔离：{event.message[:80]}")
        if event.stage == "PLANNER" and event.type == "info" and data.get("sub_questions"):
            count = len(data["sub_questions"])
            counters["sub_questions"] = count
            section.lines.append(f"把问题拆成 {count} 个可独立检索的子问题")
        if event.stage == "RESEARCHER" and event.type == "finding":
            counters["verified"] += int(data.get("verified_count", data.get("count", 0)) or 0)
            counters["candidates"] += int(data.get("candidate_count", 0) or 0)
        if data.get("category") == "source_policy":
            counters["allowed"] += int(data.get("allowed", 0) or 0)
            counters["blocked"] += int(data.get("blocked", 0) or 0)
        if data.get("category") == "paper_intake":
            counters["paper_sections"] += int(data.get("sources", 0) or 0)
        if event.stage == "ORCHESTRATOR" and event.type == "round":
            counters["rounds"] += 1
    research = sections["research"]
    if research.status != "pending":
        parts = []
        if counters["paper_sections"]:
            parts.append(f"取回指定论文的 {counters['paper_sections']} 个章节")
        if counters["allowed"] or counters["blocked"]:
            parts.append(f"来源门禁放行 {counters['allowed']} 个、拦截 {counters['blocked']} 个")
        if counters["candidates"]:
            parts.append(
                f"模型提出 {counters['candidates']} 条候选发现，"
                f"其中 {counters['verified']} 条通过逐字原文核验"
            )
        elif counters["verified"]:
            parts.append(f"得到 {counters['verified']} 条通过逐字核验的发现")
        research.lines = parts + research.lines
    if counters["rounds"]:
        sections["reflect"].lines.insert(0, f"发现证据缺口，追加 {counters['rounds']} 轮补充检索")
    elif sections["reflect"].status != "pending" and not sections["reflect"].lines:
        sections["reflect"].lines.append("评估后认为证据已足够，不再追加检索")
    write = sections["write"]
    if write.status != "pending" and not write.lines:
        write.lines.append("基于已核验证据撰写正文，并复核引用编号与数值")
    finish = sections["finish"]
    terminal = {"done": "done", "error": "error", "cancelled": "error"}.get(run_status)
    if terminal:
        finish.status = terminal
        finish.lines.append(
            {"done": "研究完成，交付物已生成", "error": "运行未能完成", "cancelled": "运行已取消"}[
                run_status
            ]
        )
    # 已被后续阶段越过的阶段视为完成；最后一个活跃阶段保持 active（运行中）
    ordered = [sections[key] for key, _ in _PHASES]
    reached = [s for s in ordered if s.status != "pending"]
    for s in reached[:-1]:
        if s.status == "active":
            s.status = "done"
    if terminal and reached:
        for s in reached:
            if s.status == "active":
                s.status = "done"
    analysed = any(
        isinstance(event.data, dict) and event.data.get("category") == "analysis"
        for event in events
    )
    headline = (
        {
            "done": "已完成：统计分析与图表已生成，交付物已登记",
            "error": "运行已结束，未完全完成",
            "cancelled": "运行已结束，未完全完成",
        }.get(run_status, "进行中：正在执行统计分析")
        if analysed
        else _headline(counters, run_status)
    )
    return {
        "headline": headline,
        "sections": [s.to_dict() for s in ordered if s.status != "pending" or s.key == "finish"],
        "counters": counters,
        "last_seq": last_seq,
    }


def _headline(counters: dict[str, int], run_status: str) -> str:
    if run_status == "done":
        return f"已完成：{counters['verified']} 条已核验证据支撑最终交付物"
    if run_status in {"error", "cancelled"}:
        return "运行已结束，未完全完成"
    if counters["verified"]:
        return f"进行中：已得到 {counters['verified']} 条已核验证据"
    return "进行中：正在理解任务并准备检索"


__all__ = ["NarrativeSection", "build_narrative"]
