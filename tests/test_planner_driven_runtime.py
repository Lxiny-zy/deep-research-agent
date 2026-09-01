from __future__ import annotations

import json
from pathlib import Path

import pytest

from deep_research.config import Settings
from deep_research.orchestrator import DeepResearchAgent
from deep_research.planning import stable_slug
from tests.fakes import FakeLLM, FakeSearch


@pytest.mark.asyncio
async def test_planner_driven_run_persists_plan_manifest_and_handoffs(tmp_path: Path) -> None:
    query = "offline planner integration"
    settings = Settings(
        orchestration_mode="planner-driven",
        artifact_root=str(tmp_path / "artifacts"),
        runner_enabled=False,
    )
    settings.max_rounds = 1
    agent = DeepResearchAgent(
        settings,
        llm=FakeLLM(),
        search_tool=FakeSearch(),
        workflow="quick",
    )
    try:
        report = await agent.run(query)
    finally:
        await agent.aclose()

    root = Path(settings.artifact_root)
    slug = stable_slug(query)
    plan_path = root / ".framework" / "plans" / f"{slug}.json"
    manifest_path = root / ".framework" / "manifests" / f"{slug}.json"
    assert report.markdown
    assert plan_path.is_file()
    assert manifest_path.is_file()
    assert (root / "work" / slug / "planner" / "plan.json").is_file()
    assert (root / "work" / slug / "researcher" / "results.json").is_file()
    assert (root / "output" / slug / "final" / "report.md").is_file()

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert plan["slug"] == slug
    assert plan["status"] == "done"
    assert {step["status"] for step in plan["steps"]} == {"done"}
    assert any(item["path"].startswith(f"output/{slug}/final/") for item in manifest["artifacts"])
