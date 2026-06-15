from __future__ import annotations

import pytest

from deep_research.config import Settings


@pytest.fixture
def settings() -> Settings:
    s = Settings()
    s.max_rounds = 1
    return s
