"""Opt-in local API acceptance using the authorized model; no production writes."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.accept_delivery_live import remote_profile


async def main() -> None:
    import httpx

    from deep_research import api
    from deep_research.config import Settings
    from deep_research.llm import LLM
    from deep_research.orchestrator import DeepResearchAgent
    from deep_research.persistence.db import create_all, make_engine, make_sessionmaker
    from deep_research.persistence.memory_repository import InMemoryRepository
    from deep_research.workbench.intake import _FixedSources
    from deep_research.workbench.qa_store import QaMessage, SqlQaStore

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorized-ssh", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--context-check", action="store_true")
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    args.output.mkdir(parents=True, exist_ok=True)
    profile = remote_profile(args.authorized_ssh)
    settings = Settings(
        api_key="",
        api_credentials=(),
        llm_api_key=profile["api_key"],
        llm_base_url=profile["base_url"],
        llm_model=profile["model"],
        artifact_root=str(args.output / "artifacts"),
        request_timeout=180,
    )
    database = f"sqlite+aiosqlite:///{(args.output / 'qa.db').resolve().as_posix()}"
    engine = make_engine(database)
    await create_all(engine)
    api.app.state.settings = settings
    api.app.state.repo = InMemoryRepository()
    api.app.state.catalog = None
    api.app.state.qa_store = SqlQaStore(make_sessionmaker(engine))
    executions = 0

    async def local_agent(app, current):
        nonlocal executions
        executions += 1
        agent = DeepResearchAgent(current, search_tool=_FixedSources([]))
        agent.llm = LLM.from_params(
            agent.tracer,
            api_key=profile["api_key"],
            base_url=profile["base_url"],
            model=profile["model"],
            timeout=180,
            user_agent=settings.llm_user_agent,
            temperature=profile.get("temperature", 0.3),
            parameter_mode=profile.get("parameter_mode", "temperature"),
            reasoning_effort=profile.get("reasoning_effort", "medium"),
            context_window_tokens=profile.get("context_window_tokens"),
            max_output_tokens=profile.get("max_output_tokens"),
        )
        return agent, None

    original = api._build_agent
    api._build_agent = local_agent
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://local", timeout=None
        ) as client:
            created = await client.post("/api/qa/conversations", json={"title": "本地请求防重验收"})
            cid = created.json()["id"]
            body = {
                "query": "请用两句话说明配对差异检验与相关分析的区别。",
                "request_id": "local-idempotency-request",
            }
            path = f"/api/qa/conversations/{cid}/messages"
            print(f"Local real-model API test started: {profile['model']}", flush=True)
            first, duplicate = await asyncio.gather(
                client.post(path, json=body), client.post(path, json=body)
            )
            assert first.status_code == duplicate.status_code == 201
            a, b = first.json(), duplicate.json()
            assert a["id"] == b["id"] and a["status"] == "done" and a["answer"]
            assert executions == 1
            await engine.dispose()
            engine = make_engine(database)
            api.app.state.qa_store = SqlQaStore(make_sessionmaker(engine))
            api.app.state.qa_live_turns = {}
            replay = await client.post(path, json=body)
            assert replay.status_code == 201 and replay.json()["id"] == a["id"]
            assert executions == 1
            result = {
                "model": profile["model"],
                "executions": executions,
                "same_message_for_duplicate": True,
                "reopened_database_replay": True,
                "message": a,
            }
            if args.context_check:
                # Synthetic prior turns isolate context transport from the
                # model's ability to generate a long answer on demand.
                marker = "RQA-" + uuid4().hex
                for index in range(6):
                    await api.app.state.qa_store.append(
                        cid,
                        QaMessage(
                            id="",
                            position=0,
                            query="验收回合甲" if index == 0 else f"后续验收回合 {index}",
                            answer=("这是用于检查完整对话保留的合成记录，不是科研结论。" * 30)
                            + (f"\n校验标识：{marker}" if index == 0 else "\n本轮没有新增标识。"),
                        ),
                    )
                followup = await client.post(
                    path,
                    json={
                        "query": "验收回合甲的答复末尾，校验标识是什么？只返回标识。",
                        "request_id": "local-full-context-request",
                    },
                )
                assert followup.status_code == 201
                context_answer = followup.json()
                result["context_check"] = {
                    "history": "six synthetic turns; marker after char 300 in earliest answer",
                    "expected_marker": marker,
                    "message": context_answer,
                    "passed": marker in context_answer["answer"],
                }
                result["executions"] = executions
            text = json.dumps(result, ensure_ascii=False, indent=2)
            if profile["api_key"] in text:
                raise RuntimeError("Refusing to persist a credential")
            (args.output / "acceptance.json").write_text(text, encoding="utf-8")
            if args.context_check:
                assert result["context_check"]["passed"], "Full-context marker was not recalled"
                print(
                    "Passed: real model recalled earlier long answer beyond four turns", flush=True
                )
            print(
                f"Passed: duplicate and reopen reused one generation; tokens={a.get('tokens')}",
                flush=True,
            )
    finally:
        api._build_agent = original
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"Local API acceptance stopped: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
