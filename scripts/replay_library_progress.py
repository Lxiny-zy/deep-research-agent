"""Replay locally retained model responses to recover interrupted research work.

This script never calls a model. It requires matching request lengths and the
original per-question completion counts, and stops at the original interruption.
The failed input database remains unchanged; recovery uses a separate copy.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def main() -> None:
    from deep_research.artifacts import ArtifactStore
    from deep_research.config import Settings
    from deep_research.execution import settings_for_resume
    from deep_research.library.repository import SqlLibraryRepository
    from deep_research.library.search import ProjectCorpusSearch
    from deep_research.llm import LLM
    from deep_research.orchestrator import DeepResearchAgent
    from deep_research.persistence.db import make_engine, make_sessionmaker
    from deep_research.persistence.sql_repository import SqlRepository
    from deep_research.planning import stable_slug
    from deep_research.tools.base import SearchTool

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    target = args.target.resolve()
    target.mkdir(parents=True, exist_ok=False)
    with sqlite3.connect(
        f"file:{(args.source / 'acceptance.db').as_posix()}?mode=ro", uri=True
    ) as src:
        with sqlite3.connect(target / "acceptance.db") as dst:
            src.backup(dst)
    engine = make_engine(f"sqlite+aiosqlite:///{(target / 'acceptance.db').as_posix()}")
    sessions = make_sessionmaker(engine)
    repo = SqlRepository(sessions)
    identity = json.loads((args.source / "run.json").read_text(encoding="utf-8"))
    detail = await repo.get_run(identity["run_id"])
    assert detail is not None and detail.orchestration is not None
    expected_events = {
        event.data["sub_question"]: event.data
        for event in await repo.get_events(detail.id)
        if event.stage == "RESEARCHER" and event.type == "finding" and event.data
    }
    expected = {question: event["count"] for question, event in expected_events.items()}
    paths = sorted(args.source.glob("response-*.json"), key=lambda p: int(p.stem.split("-")[-1]))
    records = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    assert "sub_questions" in json.loads(records[0]["output"])
    records = records[1:]  # The successful planner is already in the checkpoint.
    position = 0

    class ReplayLLM(LLM):
        async def _stream_once(self, system, user, *, temperature=0.4):
            nonlocal position
            if position >= len(records):
                raise AssertionError("No retained response; refusing a new model call")
            record = records[position]
            actual = len(system) + len(user)
            if actual != record["input_chars"]:
                raise AssertionError(
                    f"Request {position + 2} length mismatch: {actual} != {record['input_chars']}"
                )
            if (
                record.get("input_sha256")
                and record["input_sha256"]
                != hashlib.sha256((system + "\0" + user).encode()).hexdigest()
            ):
                raise AssertionError(f"Request {position + 2} content hash mismatch")
            position += 1
            if record["status"] != "complete":
                if position == len(records):
                    raise asyncio.CancelledError()
                raise TimeoutError("Retained call ended without a complete response")
            yield record["output"]

    class EmptyPublicSearch(SearchTool):
        async def search(self, query, *, max_results=5):
            return []

    execution = detail.orchestration.model_copy(deep=True)
    execution.checkpoint["scratch"].pop("_deadline_at", None)
    settings = settings_for_resume(
        Settings(artifact_root=str(target / "work"), llm_api_key="offline-replay"), execution
    )
    store = ArtifactStore(target / "work" / "runs" / detail.id)
    library = SqlLibraryRepository(sessions)
    project = await library.get_project(detail.project_id)
    assert project is not None
    agent = DeepResearchAgent(
        settings,
        search_tool=EmptyPublicSearch(),
        run_id=detail.id,
        workflow=execution.workflow_name,
        resume_execution=execution,
        artifact_store=store,
        artifact_slug=stable_slug(detail.query),
        search_overlay=ProjectCorpusSearch(library, project.id, project.owner_id),
    )
    model = json.loads((args.source / "inputs.json").read_text(encoding="utf-8"))["model"]
    llm = ReplayLLM.from_params(
        agent.tracer,
        api_key="offline-replay",
        model=model,
        base_url=None,
        timeout=120,
        user_agent="offline-replay",
    )
    agent.llm = llm
    agent._owns_llm = False
    for role in (agent.planner, agent.researcher, agent.reflector, agent.synthesizer):
        role.llm = llm
    agent.researcher.verification_llm = llm
    try:
        try:
            await agent.run(detail.query)
        except asyncio.CancelledError:
            pass
        cached = [
            json.loads(p.read_text(encoding="utf-8"))["result"]
            for p in store.framework_root.glob("research/*.json")
        ]
        actual = {item["sub_question"]: len(item["findings"]) for item in cached}
        actual_events = {
            event.data["sub_question"]: event.data
            for event in agent.tracer.events
            if event.stage == "RESEARCHER" and event.type == "finding" and event.data
        }
        if position != len(records) or actual != expected or actual_events != expected_events:
            raise RuntimeError(
                f"Replay diverged: responses={position}/{len(records)}, "
                f"counts_match={actual == expected}, "
                f"audits_match={actual_events == expected_events}"
            )
        record = {
            "status": "pass",
            "source": str(args.source),
            "replayed_responses": position,
            "completed_subquestions": len(cached),
            "findings": sum(actual.values()),
            "completion_audits_match": True,
            "model_calls": 0,
            "scope": "Retained response replay; no new evidence generated",
        }
        (target / "replay.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        for name in ("run.json", "inputs.json"):
            (target / name).write_bytes((args.source / name).read_bytes())
        print(json.dumps(record, ensure_ascii=False))
    finally:
        await llm.aclose()
        await agent.aclose()
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"Offline replay stopped: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
