"""Small, safe condition language for workflow edges (no eval)."""

from __future__ import annotations

import json
import re
from typing import Any

_EXPR = re.compile(r"^\s*([a-zA-Z_][\w.]*)\s*(?:(==|!=|>=|<=|>|<)\s*(.+))?\s*$")


def _read(value: Any, segment: str) -> Any:
    # 路径解析必须是全函数：黑板形态由各角色运行时决定（对 dict 取 last、对无 len
    # 的对象取 length 都可能出现），解析失败按 None 处理（求值结果 falsy），
    # 不得抛异常——异常会经引擎的 asyncio.gather 炸穿整个 run。
    try:
        if segment == "length":
            return len(value) if value is not None else 0
        if segment == "last":
            return value[-1] if value else None
        if isinstance(value, dict):
            return value.get(segment)
        return getattr(value, segment, None)
    except (KeyError, TypeError, IndexError):
        return None


def resolve_path(state: Any, path: str) -> Any:
    value: Any = state
    segments = path.split(".")
    if segments[0] == "state":
        segments = segments[1:]
    for segment in segments:
        value = _read(value, segment)
    return value


def _literal(raw: str) -> Any:
    text = raw.strip()
    aliases = {"true": True, "false": False, "null": None}
    if text.lower() in aliases:
        return aliases[text.lower()]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
            return float(text) if "." in text else int(text)
        raise ValueError(f"条件值必须是 JSON 字面量：{text}") from None


def evaluate_condition(expression: str | None, state: Any) -> bool:
    if not expression:
        return True
    match = _EXPR.fullmatch(expression)
    if match is None:
        raise ValueError(f"条件表达式非法：{expression}")
    path, operator, raw_value = match.groups()
    actual = resolve_path(state, path)
    if operator is None:
        return bool(actual)
    expected = _literal(raw_value)
    try:
        if operator == "==":
            return actual == expected
        if operator == "!=":
            return actual != expected
        if operator == ">":
            return actual > expected
        if operator == ">=":
            return actual >= expected
        if operator == "<":
            return actual < expected
        return actual <= expected
    except TypeError:
        return False
