from __future__ import annotations

import pytest
from pydantic import ValidationError

from deep_research.llm import extract_json
from deep_research.models import Finding


def test_finding_confidence_bounds():
    Finding(statement="x", source_url="u", confidence=0.5)
    with pytest.raises(ValidationError):
        Finding(statement="x", source_url="u", confidence=1.5)


def test_extract_json_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_codefence():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_with_noise():
    assert extract_json('好的，结果是：{"a": 1} 完毕') == {"a": 1}
