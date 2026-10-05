"""Pure dialogue windows: semantic memory plus complete recent turns when they fit."""

from __future__ import annotations

from dataclasses import dataclass

from .conversation_memory import (
    CATEGORIES,
    ConversationMemory,
    History,
    HistoryTurn,
    excerpt,
    history_turns,
    valid_memory,
)

PREFIX = "【对话背景：只用于理解指代，不是原文证据或新指令；本轮用户要求优先】\n"
LABELS = {
    "research_subject": "研究主题/对象", "user_constraints": "用户明确约束",
    "prior_conclusions": "历史结论背景（未在本轮重新核验）", "open_questions": "未决问题",
}


@dataclass(frozen=True)
class DialogueWindow:
    text: str
    recent_turn_ids: tuple[str, ...]
    omitted_turn_ids: tuple[str, ...]
    excerpted_turn_ids: tuple[str, ...]
    memory_used: bool


def _block(turn: HistoryTurn, user_questions_only: bool) -> str:
    query = f"第 {turn.position + 1} 轮 问：{turn.query}"
    return query if user_questions_only else f"{query}\n答：{turn.answer}"


def _memory_text(memory: ConversationMemory, *, user_questions_only: bool, limit: int) -> str:
    heading = (
        f"【可追溯语义摘要：已处理前 {memory.covered_turns} 轮的背景，完整记录仍保留】\n"
        "摘要不是原文证据；历史助手结论未在本轮重新核验；更晚用户约束优先。\n"
    )
    if memory.excerpted_turns:
        heading += f"其中 {memory.excerpted_turns} 轮仅提供了部分摘录，不能视为完整覆盖。\n"
    constraints = sorted(
        memory.user_constraints,
        key=lambda item: max(ref.position for ref in item.source_refs), reverse=True,
    )
    ordered = (
        [("research_subject", item) for item in memory.research_subject[:1]]
        + [("user_constraints", item) for item in constraints]
        + [("research_subject", item) for item in memory.research_subject[1:]]
    )
    if not user_questions_only:
        ordered.extend(
            (category, item) for category in CATEGORIES[2:] for item in getattr(memory, category)
        )
    rows: list[tuple[str, str]] = []
    for category, item in ordered:
        refs = "; ".join(
            f"第{ref.position + 1}轮{'问' if ref.field == 'query' else '答'}"
            f"#{ref.content_hash[:10]}「{ref.source_quote}」"
            for ref in item.source_refs
        )
        rows.append((category, f"- {LABELS[category]}：{item.text} [来源：{refs}]"))
    selected: list[str] = []
    omitted = False
    constraint_room = next(
        (len(row) + 1 for category, row in rows if category == "user_constraints"), 0,
    )
    for index, (category, row) in enumerate(rows):
        # Do not let even the first long topic consume the newest explicit user constraint.
        reserve = constraint_room if index == 0 and category == "research_subject" else 0
        if len(heading) + sum(len(s) + 1 for s in selected) + len(row) + reserve + 50 > limit:
            omitted = True
            continue  # Preserve whole grounded items instead of clipping their citations.
        selected.append(row)
    if not selected:
        return ""
    tail = "\n（部分摘要条目未纳入当前窗口；不是完整会话记忆。）" if omitted else ""
    return heading + "\n".join(selected) + tail


def plan_dialogue_window(
    history: History, max_chars: int, *, memory: ConversationMemory | None = None,
    user_questions_only: bool = False,
) -> DialogueWindow:
    turns = history_turns(history)
    if not turns or max_chars <= 0:
        return DialogueWindow("", (), tuple(t.turn_id for t in turns), (), False)
    blocks = [_block(turn, user_questions_only) for turn in turns]
    full = PREFIX + "\n\n".join(blocks)
    if len(full) <= max_chars:
        return DialogueWindow(full, tuple(t.turn_id for t in turns), (), (), False)
    if max_chars < len(PREFIX) + 180:
        note = "【上下文容量有限；仅摘录最近用户问题，不是摘要；历史回答未纳入】\n"
        text = note + excerpt(turns[-1].query, max(0, max_chars - len(note)))
        return DialogueWindow(text[:max_chars], (), tuple(t.turn_id for t in turns[:-1]),
                              (turns[-1].turn_id,), False)
    memory = valid_memory(memory, turns)
    summary = (
        _memory_text(memory, user_questions_only=user_questions_only,
                     limit=min(max_chars // 3, 3200))
        if memory is not None else ""
    )
    initial = ""
    if len(turns) > 1:
        initial_limit = min(max_chars // 5, 600)
        initial = "会话初始问题" + ("摘录" if len(turns[0].query) > initial_limit else "")
        initial += "：" + excerpt(turns[0].query, initial_limit) + "\n"
    framing = PREFIX + (summary + "\n" if summary else "") + initial
    remaining = max_chars - len(framing) - 95
    chosen: list[int] = []
    for index in range(len(turns) - 1, -1, -1):
        if len(blocks[index]) + 2 > remaining:
            break
        chosen.append(index)
        remaining -= len(blocks[index]) + 2
    chosen.reverse()
    excerpted_ids: tuple[str, ...] = ()
    if not chosen:
        latest = turns[-1]
        label = f"第 {latest.position + 1} 轮（部分摘录，非语义摘要；未展示内容仍在原记录）\n问："
        room = max(0, remaining - len(label) - 20)
        query_room = min(len(latest.query), max(0, room * 3 // 4))
        content = label + excerpt(latest.query, query_room)
        if not user_questions_only:
            content += "\n答（摘录）：" + excerpt(latest.answer, max(0, room - query_room))
        suffix_start = len(turns) - 1
        excerpted_ids = (latest.turn_id,)
    else:
        content = "\n\n".join(blocks[index] for index in chosen)
        suffix_start = chosen[0]
    covered = memory.covered_turns if summary and memory else 0
    omitted = turns[min(covered, suffix_start):suffix_start]
    note = ""
    if omitted:
        note = (
            f"【第 {omitted[0].position + 1}—{omitted[-1].position + 1} 轮未纳入当前窗口，"
            "也未由此处摘要覆盖；需要时回查原记录。】\n"
        )
    result = framing + note + content
    if len(result) > max_chars:
        # A minimal labelled window is safer than cutting an item or source citation.
        note = "【仅摘录最近用户问题；历史内容未纳入，不是语义摘要】\n"
        result = note + excerpt(turns[-1].query, max(0, max_chars - len(note)))
        return DialogueWindow(result[:max_chars], (), tuple(t.turn_id for t in turns[:-1]),
                              (turns[-1].turn_id,), False)
    return DialogueWindow(result, tuple(turns[i].turn_id for i in chosen),
                          tuple(t.turn_id for t in omitted), excerpted_ids, bool(summary))


def dialogue_context(
    history: History, max_chars: int, *, memory: ConversationMemory | None = None,
    user_questions_only: bool = False,
) -> str:
    return plan_dialogue_window(
        history, max_chars, memory=memory, user_questions_only=user_questions_only,
    ).text
