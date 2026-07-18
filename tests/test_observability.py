"""Tracer.emit 把累计 token 随事件携带（供前端实时统计）。"""

from __future__ import annotations

from deep_research.observability import Tracer


def test_emit_attaches_running_tokens():
    t = Tracer()
    e1 = t.emit("PLANNER", "start", "x")
    assert e1.tokens == 0

    t.add_tokens(15)
    e2 = t.emit("RESEARCHER", "finding", "y")
    assert e2.tokens == 15

    # token 事件也带累计值（虽不落库，但实时 SSE 用得到）
    e3 = t.emit("SYNTHESIZER", "token", data={"delta": "z"})
    assert e3.tokens == 15


def test_estimated_tokens_can_be_reconciled_to_exact_usage():
    t = Tracer()
    t.add_tokens(30, estimated=True)
    estimated_event = t.emit("SYNTHESIZER", "token", data={"delta": "x"})
    assert estimated_event.tokens == 30
    assert estimated_event.tokens_estimated is True

    t.reconcile_tokens(estimated=30, exact=18)
    exact_event = t.emit("SYNTHESIZER", "info", "done")
    assert exact_event.tokens == 18
    assert exact_event.tokens_estimated is False
