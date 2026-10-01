from __future__ import annotations

import pytest

from deep_research.agents.base import direct_system_prompt
from deep_research.agents.researcher import Researcher, source_context
from deep_research.models import FindingList, Source
from deep_research.observability import Tracer
from deep_research.prompting import structured_system_prompt
from deep_research.workbench.attachment_reader import source_batches
from tests.fakes import FakeLLM, FakeSearch


def sources():
    return [
        Source(
            title=f"第 {i} 节", url=f"https://paper.org/{i}", content=f"section-{i} " + "原文" * 500
        )
        for i in range(20)
    ]


def test_large_context_reads_more_than_four_complete_chunks_in_one_batch(settings):
    llm = FakeLLM()
    llm.input_capacity_chars = 200000
    settings.llm_max_input_chars = 16000  # The selected model's capacity takes precedence.
    researcher = Researcher(llm, FakeSearch(), Tracer(), settings)
    items = sources()
    batches = source_batches(items, researcher, "阅读全部章节")
    assert len(batches) == 1 and batches[0] == items
    assert "section-19" in source_context(batches[0])


def test_small_capacity_splits_without_changing_order_or_content(settings):
    llm = FakeLLM()
    llm.input_capacity_chars = 16000
    researcher = Researcher(llm, FakeSearch(), Tracer(), settings)
    question = "阅读要求" * 100
    items = sources()
    batches = source_batches(items, researcher, question)
    assert len(batches) > 1
    assert [source for batch in batches for source in batch] == items
    rules = structured_system_prompt(direct_system_prompt(researcher.system), FindingList)
    for batch in batches:
        assert (
            len(rules) + len(question) + len(source_context(batch)) + 128 < llm.input_capacity_chars
        )


def test_a_single_oversized_chunk_is_rejected_without_slicing(settings):
    llm = FakeLLM()
    llm.input_capacity_chars = 16000
    researcher = Researcher(llm, FakeSearch(), Tracer(), settings)
    source = Source(title="完整片段", url="https://paper.org", content="证据" * 20000)
    with pytest.raises(ValueError, match="未截断原文"):
        source_batches([source], researcher, "阅读")
    assert source.content == "证据" * 20000
