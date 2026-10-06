import pytest

from deep_research.config import Settings


@pytest.mark.parametrize(
    "name",
    [
        "render_execution_timeout_seconds",
        "render_progress_timeout_seconds",
        "render_progress_poll_seconds",
    ],
)
@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_render_deadlines_cannot_be_disabled_by_nonfinite_or_nonpositive_values(name, value):
    with pytest.raises(ValueError, match=name):
        Settings(**{name: value})


@pytest.mark.parametrize(
    "name",
    ["qa_max_active", "qa_max_active_per_owner", "qa_max_pending", "qa_max_pending_per_owner"],
)
@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_qa_capacity_requires_positive_integer_bounds(name, value):
    with pytest.raises(ValueError, match=name):
        Settings(**{name: value})


def test_reliability_settings_accept_explicit_environment_values(monkeypatch):
    monkeypatch.setenv("QA_MAX_ACTIVE", "3")
    monkeypatch.setenv("QA_MAX_ACTIVE_PER_OWNER", "1")
    monkeypatch.setenv("QA_MAX_PENDING", "20")
    monkeypatch.setenv("QA_MAX_PENDING_PER_OWNER", "4")
    monkeypatch.setenv("DR_RENDER_EXECUTION_TIMEOUT_SECONDS", "1200")
    monkeypatch.setenv("DR_RENDER_PROGRESS_TIMEOUT_SECONDS", "450")
    monkeypatch.setenv("DR_RENDER_PROGRESS_POLL_SECONDS", "0.5")
    configured = Settings()
    assert (
        configured.qa_max_active,
        configured.qa_max_active_per_owner,
        configured.qa_max_pending,
        configured.qa_max_pending_per_owner,
    ) == (3, 1, 20, 4)
    assert (
        configured.render_execution_timeout_seconds,
        configured.render_progress_timeout_seconds,
        configured.render_progress_poll_seconds,
    ) == (1200, 450, 0.5)
