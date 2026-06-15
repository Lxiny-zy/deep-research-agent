"""运行时全局配置：把前端可改的设置持久化到 JSON 文件，启动时叠加到 Settings。

与 deep_research.config.Settings 的关系：
  Settings()            读环境变量（基础默认）
  load_overrides()      读本文件持久化的覆盖项（前端经 /api/config 写入）
  apply_overrides(...)  把覆盖项叠加到 Settings 之上（dataclasses.replace + 范围校验）

只允许覆盖白名单字段；database_url 与服务端 api_key 不可经前端改（自举 / 鉴权安全）。
密钥也可持久化，但 API 层只脱敏回显、空表单不清空（详见 api.py）。
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

from .config import Settings

# 前端可编辑并持久化的字段（其余字段仅来自环境变量）
EDITABLE_FIELDS: tuple[str, ...] = (
    "llm_model",
    "llm_base_url",
    "llm_api_key",
    "tavily_api_key",
    "max_sub_questions",
    "max_rounds",
    "max_concurrency",
    "results_per_search",
    "request_timeout",
)

# 密钥字段：API 层脱敏回显、空值＝保持不变
SECRET_FIELDS: frozenset[str] = frozenset({"llm_api_key", "tavily_api_key"})


def config_path() -> Path:
    """持久化文件路径（默认 CWD/runtime_config.json，可经 RUNTIME_CONFIG_PATH 覆盖）。"""
    return Path(os.getenv("RUNTIME_CONFIG_PATH", "runtime_config.json"))


def load_overrides() -> dict:
    """读取持久化覆盖项；文件缺失 / 损坏一律回退为空（不阻塞启动）。"""
    path = config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in EDITABLE_FIELDS}


def save_overrides(overrides: dict) -> None:
    """把覆盖项写回文件（只保留白名单键）。"""
    clean = {k: v for k, v in overrides.items() if k in EDITABLE_FIELDS}
    config_path().write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")


def apply_overrides(settings: Settings, overrides: dict) -> Settings:
    """把白名单覆盖项叠加到 settings 之上。replace 会触发 __post_init__ 范围校验。"""
    valid = {k: v for k, v in overrides.items() if k in EDITABLE_FIELDS}
    return replace(settings, **valid) if valid else settings
