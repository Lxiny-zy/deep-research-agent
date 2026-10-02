"""Real HTTP/model acceptance of a revision child run from trusted local evidence."""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import logging
import pickle
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def main() -> None:
    import httpx
    import uvicorn

    from deep_research import api
    from deep_research.config import Settings
    from deep_research.execution import ExecutionContext, RunExecutor
    from deep_research.llm import LLM
    from deep_research.orchestrator import DeepResearchAgent
    from deep_research.persistence.memory_repository import InMemoryRepository
    from deep_research.workbench.content_revision import REVISION_KEY, source_version
    from deep_research.workbench.intake import _FixedSources
    from scripts.accept_delivery_live import remote_profile

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorized-ssh", required=True)
    parser.add_argument("--detail", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    logging.disable(logging.CRITICAL)
    raw = args.detail.read_bytes()
    parent = pickle.loads(raw)  # Only snapshots created by the local acceptance runner.
    profile = remote_profile(args.authorized_ssh)
    settings = Settings(
        api_key="",
        api_credentials=(),
        execution_mode="inline",
        llm_api_key=profile["api_key"],
        llm_base_url=profile["base_url"],
        llm_model=profile["model"],
        artifact_root=str(args.output / "store"),
        request_timeout=180,
    )
    repo = InMemoryRepository()
    parent_id = await repo.create_run(parent.query, execution=copy.deepcopy(parent.orchestration))
    await repo.replace_artifacts(
        parent_id,
        plan=None,
        reflection_rounds=[],
        results=copy.deepcopy(parent.results),
        report=parent.report.model_copy(deep=True),
    )
    await repo.save_sources(parent_id, parent.sources)
    await repo.set_status(parent_id, "done")
    loaded = await repo.get_run(parent_id)
    frozen_parent = source_version(loaded)
    executions = []
    calls = []

    def save(name: str, data: object) -> None:
        text = json.dumps(data, ensure_ascii=False, indent=2)
        if profile["api_key"] in text:
            raise RuntimeError("Refusing to persist a credential")
        (args.output / name).write_text(text, encoding="utf-8")

    class NoSearch(_FixedSources):
        async def search(self, *args, **kwargs):
            raise AssertionError("A content revision must not repeat retrieval")

    class Capture(LLM):
        async def _stream_once(self, system, user, *, temperature=0.4):
            number = len(calls) + 1
            record = {"call": number, "input_chars": len(system) + len(user)}
            calls.append(record)
            chunks = []
            started = time.monotonic()
            try:
                async for chunk in super()._stream_once(system, user, temperature=temperature):
                    chunks.append(chunk)
                    yield chunk
                record["status"] = "complete"
            finally:
                record.update(seconds=time.monotonic() - started, output="".join(chunks))
                save(f"response-{number}.json", record)

    class LocalExecutor(RunExecutor):
        async def build_agent(self, current, **kwargs):
            executions.append(str(kwargs.get("run_id", "")))
            agent = DeepResearchAgent(current, search_tool=NoSearch([]), **kwargs)
            agent.llm = Capture.from_params(
                agent.tracer,
                api_key=profile["api_key"],
                base_url=profile["base_url"],
                model=profile["model"],
                timeout=current.request_timeout,
                user_agent=current.llm_user_agent,
                temperature=profile.get("temperature", 0.3),
                parameter_mode=profile.get("parameter_mode", "temperature"),
                reasoning_effort=profile.get("reasoning_effort", "medium"),
                context_window_tokens=profile.get("context_window_tokens"),
                max_output_tokens=profile.get("max_output_tokens"),
            )
            return agent, None

    app = api.app
    app.state.settings, app.state.repo, app.state.catalog = settings, repo, None
    app.state.live, app.state.tasks, app.state.run_tasks = {}, set(), {}
    app.state.cancellation_requested = set()
    app.state.config_lock = asyncio.Lock()
    app.state.delivery_cache, app.state.delivery_pending = {}, {}
    app.state.run_admission = api.RunAdmission(settings.max_active_runs, settings.max_queued_runs)
    app.state.executor = LocalExecutor(
        ExecutionContext(repo=repo, catalog=None, live=app.state.live)
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(
        uvicorn.Config(app, lifespan="off", log_level="critical", access_log=False)
    )
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    started = time.monotonic()
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if serving.done():
                    await serving
                    raise RuntimeError("Local HTTP server exited")
                await asyncio.sleep(0.02)
        async with httpx.AsyncClient(base_url=url, timeout=None, trust_env=False) as client:
            registry = await client.get(f"/api/runs/{parent_id}/deliverables")
            registry.raise_for_status()
            original = registry.json()
            save("parent-delivery.json", original)
            body = {"source_version": frozen_parent, "request_id": "live-content-revision"}
            first, duplicate = await asyncio.gather(
                *[client.post(f"/api/runs/{parent_id}/revise", json=body) for _ in range(2)]
            )
            first.raise_for_status()
            duplicate.raise_for_status()
            child_id = first.json()["run_id"]
            assert duplicate.json()["run_id"] == child_id != parent_id
            print("Revision API created one child for two identical requests", flush=True)
            observed: dict[str, int | float] = {"token_events": 0, "reasoning_events": 0}
            async with client.stream("GET", f"/api/runs/{child_id}/stream") as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line[6:])
                    data = event.get("data") or {}
                    if event.get("type") == "token":
                        observed["token_events"] += 1
                        observed.setdefault("first_token_seconds", time.monotonic() - started)
                    if data.get("reasoning_delta"):
                        observed["reasoning_events"] += 1
                        observed.setdefault("first_reasoning_seconds", time.monotonic() - started)
                    if event.get("stage") != "LLM" and event.get("type") in {
                        "start",
                        "info",
                        "done",
                        "error",
                    }:
                        print(
                            f"{event.get('stage')}: {str(event.get('message', ''))[:130]}".replace(
                                profile["api_key"], "[REDACTED]"
                            ),
                            flush=True,
                        )
            await asyncio.gather(*list(app.state.tasks))
            child = await repo.get_run(child_id)
            current_parent = await repo.get_run(parent_id)
            assert source_version(current_parent) == frozen_parent
            assert len(executions) == 1
            assert (
                child.orchestration.checkpoint["scratch"][REVISION_KEY]["parent_run_id"]
                == parent_id
            )
            saved = pickle.dumps(child)
            if profile["api_key"].encode() in saved:
                raise RuntimeError("Refusing to persist a credential")
            (args.output / "local-detail.pickle").write_bytes(saved)
            delivered = await client.get(f"/api/runs/{child_id}/deliverables")
            delivered.raise_for_status()
            result = delivered.json()
            files = args.output / "delivery"
            files.mkdir()
            for item in result["items"]:
                download = await client.get(
                    f"/api/runs/{child_id}/deliverables/{item['name']}",
                    params={"version": result["content_version"]},
                )
                download.raise_for_status()
                path = files / item["name"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(download.content)
            replay = await client.post(f"/api/runs/{parent_id}/revise", json=body)
            assert replay.json()["run_id"] == child_id and len(executions) == 1
            save(
                "acceptance.json",
                {
                    "source_sha256": hashlib.sha256(raw).hexdigest(),
                    "parent_preserved": True,
                    "same_child_for_duplicate_and_completed_replay": True,
                    "executions": len(executions),
                    "calls": len(calls),
                    "new_tokens": child.total_tokens,
                    "seconds": time.monotonic() - started,
                    "stream": observed,
                    "run_status": child.status,
                    "delivery": result,
                },
            )
            print(
                f"Live content revision: {result['status']}; files={len(result['items'])}",
                flush=True,
            )
    finally:
        await asyncio.gather(*list(app.state.tasks), return_exceptions=True)
        server.should_exit = True
        await serving
        listener.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"Live revision stopped: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
