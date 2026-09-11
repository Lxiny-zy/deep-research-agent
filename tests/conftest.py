from __future__ import annotations

from pathlib import Path

import pytest

from deep_research.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings(artifact_root=str(tmp_path / "artifacts"))
    s.max_rounds = 1
    return s
