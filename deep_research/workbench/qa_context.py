"""Fit complete dialogue turns to the current model, never chop every reply at 300 chars."""

from __future__ import annotations


def dialogue_context(history: list[dict[str, str]], max_chars: int) -> str:
    if not history:
        return ""
    prefix = "【最近对话：只用于理解指代，不是新的原文证据；本轮指令优先】\n"
    blocks = [
        f"第 {index + 1} 轮 问：{turn.get('query', '')}\n答：{turn.get('answer', '')}"
        for index, turn in enumerate(history)
    ]
    complete = prefix + "\n\n".join(blocks)
    if len(complete) <= max_chars:
        return complete
    # Keep the original user request even when older answer bodies leave the window.
    initial = f"\n会话初始问题：{history[0].get('query', '')}\n"
    remaining = max_chars - len(prefix) - len(initial) - 100
    chosen: list[str] = []
    for block in reversed(blocks):
        if len(block) + 2 <= remaining:
            chosen.append(block)
            remaining -= len(block) + 2
        elif not chosen:
            raise ValueError("最近一轮完整对话超出当前模型的上下文容量，请调整模型容量或新建会话")
        else:
            break  # Keep a contiguous suffix; do not silently skip a middle turn.
    note = (
        f"\n更早的 {len(blocks) - len(chosen)} 轮未纳入当前上下文。\n"
        if len(chosen) < len(blocks)
        else ""
    )
    return prefix + initial + note + "\n\n".join(reversed(chosen))
