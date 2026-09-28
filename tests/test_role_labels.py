"""每个内置角色在前端步骤轨道里都要有中文名，否则用户会看到 research_writer 这类内部 ID。"""

from __future__ import annotations

import re
from pathlib import Path

import deep_research.api  # noqa: F401  导入即触发全部内置角色注册
from deep_research.registry import available

WORKBENCH_TS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "lib" / "workbench.ts"


def test_every_builtin_role_has_a_step_label():
    source = WORKBENCH_TS.read_text(encoding="utf-8")
    block = re.search(r"const ROLE_LABEL[^{]*\{(.*?)\n\}", source, re.S)
    assert block, "ROLE_LABEL map not found in workbench.ts"
    labelled = set(re.findall(r"^\s*([a-z_]+):", block.group(1), re.M))
    missing = sorted(set(available()) - labelled)
    assert not missing, f"frontend/src/lib/workbench.ts ROLE_LABEL 缺少角色：{missing}"
