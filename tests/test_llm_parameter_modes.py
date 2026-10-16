from __future__ import annotations

from deep_research.llm import LLM


def test_temperature_mode_sends_only_temperature() -> None:
    llm = object.__new__(LLM)
    llm.parameter_mode = "temperature"
    llm.reasoning_effort = "medium"
    assert llm._generation_options(0.7) == {"temperature": 0.7}


def test_reasoning_mode_omits_temperature() -> None:
    llm = object.__new__(LLM)
    llm.parameter_mode = "reasoning"
    llm.reasoning_effort = "high"
    assert llm._generation_options(0.7) == {"reasoning_effort": "high"}


def test_profile_temperature_overrides_agent_sampling_hint() -> None:
    llm = object.__new__(LLM)
    llm.parameter_mode = "temperature"
    llm.default_temperature = 0.45
    assert llm._generation_options(0.7) == {"temperature": 0.45}


def test_lazy_model_exposes_the_effective_profile_parameters() -> None:
    from deep_research.orchestrator import _LazyOwnedLLM

    model = object.__new__(LLM)
    model.parameter_mode = "reasoning"
    model.reasoning_effort = "high"
    model.default_temperature = 0.65
    lazy = _LazyOwnedLLM(lambda: model).for_role("researcher")
    assert lazy.parameter_mode == "reasoning"
    assert lazy.reasoning_effort == "high"
    assert lazy.default_temperature == 0.65
