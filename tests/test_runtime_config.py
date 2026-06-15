"""runtime_config 持久化 / 叠加 / 校验 + API 层密钥脱敏。"""

from __future__ import annotations

import pytest

from deep_research import runtime_config
from deep_research.api import _config_view, _mask_secret
from deep_research.config import Settings


def test_load_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNTIME_CONFIG_PATH", str(tmp_path / "nope.json"))
    assert runtime_config.load_overrides() == {}


def test_save_then_load_filters_to_whitelist(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNTIME_CONFIG_PATH", str(tmp_path / "cfg.json"))
    runtime_config.save_overrides({"llm_model": "x", "not_allowed": 1, "max_rounds": 3})
    assert runtime_config.load_overrides() == {"llm_model": "x", "max_rounds": 3}


def test_load_corrupt_returns_empty(tmp_path, monkeypatch):
    p = tmp_path / "cfg.json"
    p.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("RUNTIME_CONFIG_PATH", str(p))
    assert runtime_config.load_overrides() == {}


def test_apply_overrides_merges_and_validates():
    base = Settings()
    merged = runtime_config.apply_overrides(base, {"llm_model": "m2", "max_rounds": 4})
    assert merged.llm_model == "m2"
    assert merged.max_rounds == 4
    assert merged is not base


def test_apply_overrides_rejects_invalid():
    with pytest.raises(ValueError):
        runtime_config.apply_overrides(Settings(), {"max_concurrency": 0})


def test_mask_secret():
    assert _mask_secret("") == ""
    assert _mask_secret("sk-abcd1234") == "…1234"
    assert _mask_secret("ab") == "…ab"


def test_config_view_masks_keys():
    s = Settings(llm_api_key="sk-secret-key-9999", tavily_api_key="tvly-xyz0")
    view = _config_view(s)
    assert view.llm_api_key_set is True
    assert view.llm_api_key_hint == "…9999"
    assert view.tavily_api_key_set is True
    # 明文绝不出现在脱敏视图
    assert "sk-secret-key-9999" not in view.model_dump_json()
