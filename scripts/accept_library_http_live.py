"""Opt-in local HTTP research acceptance with the authorized server's model.

No server mutation. Credentials stay in memory; SQLite, source records and
downloaded deliverables are local. Public search is disabled by default; enable
--public-search for a separate real arXiv/OpenAlex acceptance scenario.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import sys
import time
import traceback
from contextlib import aclosing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def main() -> None:
    import httpx
    from fastapi.encoders import jsonable_encoder

    from deep_research import api
    from deep_research.config import Settings
    from deep_research.execution import ExecutionContext, RunExecutor
    from deep_research.library.repository import SqlLibraryRepository
    from deep_research.llm import LLM
    from deep_research.orchestrator import DeepResearchAgent
    from deep_research.persistence.db import create_all, make_engine, make_sessionmaker
    from deep_research.persistence.sql_repository import SqlRepository
    from deep_research.tools.base import SearchTool
    from deep_research.workbench.qa_store import SqlQaStore
    from scripts.accept_delivery_live import remote_profile

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorized-ssh", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sources", type=Path, nargs="+")
    parser.add_argument("--query-file", type=Path)
    parser.add_argument("--qa-query-file", type=Path)
    parser.add_argument("--public-search", action="store_true")
    parser.add_argument("--tier", choices=["light", "standard", "deep"])
    parser.add_argument(
        "--resume", action="store_true", help="Resume this script's saved local run"
    )
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    target = args.output.resolve()
    if args.resume:
        if not (target / "run.json").is_file() or not (target / "acceptance.db").is_file():
            raise ValueError("A saved local run is required")
        if (target / "replay.json").is_file():
            replay = json.loads((target / "replay.json").read_text(encoding="utf-8"))
            if replay.get("status") != "pass" or not replay.get("completion_audits_match"):
                raise ValueError("The replay copy has not passed verification")
        previous_input = json.loads((target / "inputs.json").read_text(encoding="utf-8"))
        if bool(previous_input.get("public_search")) != args.public_search:
            raise ValueError("Keep the original public/library search mode when resuming")
    else:
        if not args.query_file or (not args.public_search and not args.sources):
            parser.error("New acceptance requires a query and either sources or --public-search")
        target.mkdir(parents=True, exist_ok=False)
    profile = remote_profile(args.authorized_ssh)
    query = args.query_file.read_text(encoding="utf-8-sig") if args.query_file else ""
    if args.resume and not query:
        query = previous_input["query"]
    settings = Settings(
        api_key="",
        api_credentials=(),
        intent_enabled=False,
        execution_mode="inline",
        orchestration_mode="legacy",
        artifact_root=str(target / "work"),
        llm_api_key=profile["api_key"],
        llm_base_url=None,
        llm_model=profile["model"],
        request_timeout=max(120, profile.get("request_timeout", 120)),
        max_concurrency=1,
        provider_max_concurrency=1,
        max_rounds=0,
        quality=profile.get("quality", {}),
    )
    engine = make_engine(f"sqlite+aiosqlite:///{(target / 'acceptance.db').as_posix()}")
    await create_all(engine)
    sessions = make_sessionmaker(engine)
    repo, library = SqlRepository(sessions), SqlLibraryRepository(sessions)
    response_count = max(
        (int(path.stem.split("-")[-1]) for path in target.glob("response-*.json")), default=0
    )
    agents = []
    retrieval_count = max(
        (int(path.stem.split("-")[-1]) for path in target.glob("retrieval-*.json")), default=0
    )

    def save(name, value):
        serialized = json.dumps(value, ensure_ascii=False, indent=2)
        if profile["api_key"] and profile["api_key"] in serialized:
            raise RuntimeError("Refusing to persist a credential")
        (target / name).write_text(serialized, encoding="utf-8")

    class Diagnostics(logging.Handler):
        def emit(self, record):
            if record.exc_info:
                save(
                    "execution-error.json",
                    {
                        "type": record.exc_info[0].__name__,
                        "frames": [
                            {"file": frame.filename, "line": frame.lineno, "function": frame.name}
                            for frame in traceback.extract_tb(record.exc_info[2])
                        ],
                    },
                )

    logging.getLogger().handlers = [Diagnostics()]
    logging.disable(logging.NOTSET)
    logging.getLogger().setLevel(logging.ERROR)

    class CaptureLLM(LLM):
        async def _stream_once(self, system, user, *, temperature=0.4):
            nonlocal response_count
            response_count += 1
            number, started, chunks = response_count, time.monotonic(), []
            status = "interrupted"
            try:
                async with aclosing(
                    super()._stream_once(system, user, temperature=temperature)
                ) as stream:
                    async for chunk in stream:
                        chunks.append(chunk)
                        yield chunk
                status = "complete"
            finally:
                save(
                    f"response-{number}.json",
                    {
                        "status": status,
                        "seconds": time.monotonic() - started,
                        "input_chars": len(system) + len(user),
                        "input_sha256": hashlib.sha256((system + "\0" + user).encode()).hexdigest(),
                        "output": "".join(chunks),
                    },
                )

    class EmptyPublicSearch(SearchTool):
        async def search(self, query, *, max_results=5):
            return []

    class PublicSearch(SearchTool):
        def __init__(self, current_settings):
            from deep_research.tools.arxiv_search import ArxivSearch
            from deep_research.tools.composite import MultiBackendSearch
            from deep_research.tools.openalex import OpenAlexSearch

            options = {
                "fulltext": current_settings.fulltext_enabled,
                "timeout": current_settings.request_timeout,
                "fulltext_max_chars": current_settings.fulltext_max_chars,
            }
            self.delegate = MultiBackendSearch([OpenAlexSearch(**options), ArxivSearch(**options)])

        async def search(self, query, *, max_results=5):
            nonlocal retrieval_count
            rows = await self.delegate.search(query, max_results=max_results)
            retrieval_count += 1
            save(
                f"retrieval-{retrieval_count}.json",
                {
                    "query": query,
                    "sources": [source.model_dump(mode="json") for source in rows],
                },
            )
            return rows

        async def aclose(self):
            await self.delegate.aclose()

    class Executor(RunExecutor):
        async def build_agent(self, settings, **kwargs):
            search = PublicSearch(settings) if args.public_search else EmptyPublicSearch()
            agent = DeepResearchAgent(settings, search_tool=search, **kwargs)
            llm = CaptureLLM.from_params(
                agent.tracer,
                api_key=profile["api_key"],
                base_url=profile["base_url"],
                model=profile["model"],
                timeout=settings.request_timeout,
                user_agent="deep-research-local-library-acceptance",
                temperature=profile.get("temperature", 0.3),
                parameter_mode=profile.get("parameter_mode", "temperature"),
                reasoning_effort=profile.get("reasoning_effort", "medium"),
                context_window_tokens=profile.get("context_window_tokens"),
                max_output_tokens=profile.get("max_output_tokens"),
            )
            agent.llm = llm
            for role in (agent.planner, agent.researcher, agent.reflector, agent.synthesizer):
                role.llm = llm
            agent.researcher.verification_llm = llm
            agents.append(agent)
            return agent, search

    live = {}
    for name, value in {
        "settings": settings,
        "repo": repo,
        "library": library,
        "catalog": None,
        "live": live,
        "tasks": set(),
        "run_tasks": {},
        "cancellation_requested": set(),
        "config_lock": asyncio.Lock(),
        "delivery_cache": {},
        "delivery_pending": {},
        "run_admission": api.RunAdmission(1, 1),
        "qa_store": SqlQaStore(sessions),
        "qa_requests": None,
        "qa_requests_store": None,
        "qa_live_turns": {},
        "qa_tasks": set(),
        "executor": Executor(ExecutionContext(repo=repo, library=library, live=live)),
    }.items():
        setattr(api.app.state, name, value)
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://local"
        ) as client:
            if args.resume:
                identity = json.loads((target / "run.json").read_text(encoding="utf-8"))
                run_id, project_id = identity["run_id"], identity["project_id"]
                resumed = await client.post(f"/api/runs/{run_id}/resume")
                resumed.raise_for_status()
            elif args.public_search:
                project_id = None
                save(
                    "inputs.json",
                    {
                        "query": query,
                        "sources": [],
                        "model": profile["model"],
                        "public_search": True,
                        "tier": args.tier,
                    },
                )
                created = await client.post(
                    "/api/runs",
                    json={
                        "query": query,
                        "template": "autoResearch",
                        "strategy": "quick",
                        "tier": args.tier,
                        "clarified": True,
                    },
                )
                created.raise_for_status()
                run_id = created.json()["run_id"]
                save("run.json", {"run_id": run_id, "project_id": None})
            else:
                created = await client.post(
                    "/api/projects", json={"name": "Local task-path acceptance"}
                )
                created.raise_for_status()
                project_id = created.json()["id"]
                corpus = (await client.get(f"/api/projects/{project_id}/corpora")).json()[0]
                inputs = []
                for path in args.sources:
                    raw = path.read_bytes()
                    response = await client.post(
                        f"/api/projects/{project_id}/sources/import",
                        json={
                            "corpus_id": corpus["id"],
                            "title": path.stem,
                            "kind": "markdown",
                            "text": raw.decode("utf-8-sig"),
                        },
                    )
                    response.raise_for_status()
                    inputs.append(
                        {
                            "filename": path.name,
                            "sha256": hashlib.sha256(raw).hexdigest(),
                            "source_id": response.json()["id"],
                        }
                    )
                save("inputs.json", {"query": query, "sources": inputs, "model": profile["model"]})
                created = await client.post(
                    "/api/runs",
                    json={
                        "query": query,
                        "template": "autoResearch",
                        "strategy": "quick",
                        "project_id": project_id,
                        "clarified": True,
                    },
                )
                created.raise_for_status()
                run_id = created.json()["run_id"]
                save("run.json", {"run_id": run_id, "project_id": project_id})
            while await repo.get_run_status(run_id) in {"pending", "running"}:
                await asyncio.sleep(10)
                print(
                    f"research: {await repo.get_run_status(run_id)}; calls={response_count}",
                    flush=True,
                )
            detail = await repo.get_run(run_id)
            save("run-detail.json", jsonable_encoder(detail))
            if detail.status != "done":
                raise RuntimeError("Research did not finish successfully")
            response = await client.get(f"/api/runs/{run_id}/deliverables")
            response.raise_for_status()
            registry = response.json()
            save("deliverables.json", registry)
            for item in registry["items"]:
                download = await client.get(f"/api/runs/{run_id}/deliverables/{item['name']}")
                download.raise_for_status()
                if hashlib.sha256(download.content).hexdigest() != item["sha256"]:
                    raise RuntimeError("Download hash mismatch")
                destination = target / "downloads" / item["name"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(download.content)
            created = await client.post(
                "/api/qa/conversations", json={"title": "Research follow-up"}
            )
            created.raise_for_status()
            cid = created.json()["id"]
            answer = await client.post(
                f"/api/qa/conversations/{cid}/messages/stream",
                json={
                    "query": args.qa_query_file.read_text(encoding="utf-8-sig")
                    if args.qa_query_file
                    else query
                    if args.public_search
                    else ("根据资料库，目前任务场景通路的验证还缺哪些内容？请区分已验证与待验证。"),
                    "sources": ["web"] if args.public_search else ["library"],
                    "project_id": project_id,
                    "request_id": "research-live-followup",
                },
            )
            answer.raise_for_status()
            history = (await client.get(f"/api/qa/conversations/{cid}")).json()
            save("qa.json", history)
            complete = (
                "event: complete" in answer.text
                and bool(history.get("messages"))
                and all(message["status"] == "done" for message in history["messages"])
            )
            result = {
                "status": "pass" if registry["status"] != "fail" and complete else "fail",
                "run_id": run_id,
                "run_status": detail.status,
                "delivery_status": registry["status"],
                "downloaded_files": len(registry["items"]),
                "qa_complete": complete,
                "calls": response_count,
                "public_search_calls": retrieval_count,
                "tokens": sum(a.tracer.total_tokens for a in agents),
                "seconds": time.monotonic() - started,
                "scope": "Local ASGI HTTP, SQLite, real model; "
                + ("live arXiv/OpenAlex" if args.public_search else "public search disabled"),
            }
            save("acceptance.json", result)
            print(
                f"Library HTTP acceptance: {result['status']}; calls={response_count}", flush=True
            )
    finally:
        await asyncio.gather(
            *list(api.app.state.tasks), *list(api.app.state.qa_tasks), return_exceptions=True
        )
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"Acceptance stopped: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
