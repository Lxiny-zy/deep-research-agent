"""配置环境变量覆盖、范围校验，以及 API 的 per-run 参数覆盖逻辑。"""

from __future__ import annotations

import pytest

from deep_research.api import ResearchParams, _settings_for
from deep_research.config import Settings


def test_int_env_override(monkeypatch):
    monkeypatch.setenv("MAX_SUB_QUESTIONS", "9")
    assert Settings().max_sub_questions == 9


def test_int_env_invalid_fails_fast(monkeypatch):
    monkeypatch.setenv("MAX_ROUNDS", "abc")
    with pytest.raises(ValueError, match="MAX_ROUNDS"):
        Settings()


def test_bool_env_override(monkeypatch):
    monkeypatch.setenv("REQUIRE_CORROBORATION", "true")
    assert Settings().require_corroboration is True


def test_bool_env_invalid_fails_fast(monkeypatch):
    monkeypatch.setenv("REQUIRE_CORROBORATION", "sometimes")
    with pytest.raises(ValueError, match="REQUIRE_CORROBORATION"):
        Settings()


def test_optional_int_env_invalid_fails_fast(monkeypatch):
    monkeypatch.setenv("MAX_TOKENS", "not-a-number")
    with pytest.raises(ValueError, match="MAX_TOKENS"):
        Settings()


def test_post_init_rejects_invalid():
    with pytest.raises(ValueError):
        Settings(max_concurrency=0)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_post_init_rejects_non_finite_request_timeout(value: float):
    with pytest.raises(ValueError, match="request_timeout"):
        Settings(request_timeout=value)


def test_settings_for_none_returns_base():
    base = Settings()
    assert _settings_for(base, None) is base


def test_settings_for_empty_params_returns_base():
    base = Settings()
    assert _settings_for(base, ResearchParams()) is base


def test_settings_for_overrides_only_provided():
    base = Settings()
    merged = _settings_for(base, ResearchParams(max_sub_questions=8))
    assert merged is not base
    assert merged.max_sub_questions == 8
    assert merged.max_rounds == base.max_rounds  # 未提供的字段保持不变
    assert merged.llm_api_key == base.llm_api_key  # 密钥等非行为字段照搬


def test_settings_for_can_enable_strict_corroboration_gate():
    base = Settings(require_corroboration=False)
    merged = _settings_for(base, ResearchParams(require_corroboration=True))
    assert merged.require_corroboration is True
    assert base.require_corroboration is False


def test_research_params_rejects_out_of_range():
    with pytest.raises(ValueError):
        ResearchParams(max_sub_questions=0)  # ge=1
