"""Two real HTTP/API processes share one Q&A execution and stream through reconnect."""

from __future__ import annotations

import argparse
import asyncio
import json
import multiprocessing
import socket
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx


def serve(database: str, directory: str, identity: str, ports: Any) -> None:
    import uvicorn
    from fastapi import FastAPI

    from deep_research import api
    from deep_research.config import Settings
    from deep_research.persistence.db import make_engine, make_sessionmaker
    from deep_research.workbench import qa_api
    from deep_research.workbench.qa_store import SqlQaStore

    root = Path(directory)
    engine = make_engine(database)
    server = None

    async def wait_for_file(name: str) -> None:
        async with asyncio.timeout(30):
            while not (root / name).exists():  # noqa: ASYNC110 - filesystem IPC between processes
                await asyncio.sleep(0.02)

    async def generated(*args: Any, on_delta: Any, on_event: Any) -> dict:
        with (root / "executions.log").open("a", encoding="utf-8") as log:
            log.write(identity + "\n")
        on_event({"type": "reasoning", "call_id": "first", "reasoning_delta": "First reasoning."})
        on_delta("First provisional answer.")
        await wait_for_file("revise")
        on_event({"type": "reset", "message": "Revising answer."})
        on_event({"type": "reasoning", "call_id": "second", "reasoning_delta": "Second reasoning."})
        on_delta("Revised provisional answer.")
        await wait_for_file("finish")
        return {"answer": "Validated final answer.", "status": "done", "tokens": 0}

    async def no_external_admission(*args: Any, **kwargs: Any) -> None:
        pass

    # Only inference/admission is simulated. HTTP, jobs, SQL leases, event
    # persistence, routing and disconnect/reconnect use application code.
    qa_api._answer = generated
    api._check_rate_limit = no_external_admission

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        async def shutdown_on_signal() -> None:
            while not (root / "stop").exists():  # noqa: ASYNC110 - filesystem IPC
                await asyncio.sleep(0.05)
            assert server is not None
            server.should_exit = True

        watcher = asyncio.create_task(shutdown_on_signal())
        try:
            yield
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            tasks = list(getattr(app.state, "qa_tasks", ()))
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await engine.dispose()

    app = FastAPI(lifespan=lifespan)
    app.state.settings = Settings()
    app.state.qa_store = SqlQaStore(make_sessionmaker(engine))
    app.include_router(qa_api.router)

    @app.get("/health")
    async def health() -> dict:
        return {"ready": True}

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        ports.put((identity, listener.getsockname()[1]))
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        server.run(sockets=[listener])


async def check(directory: Path, ports: dict[str, int]) -> dict[str, Any]:
    urls = {name: f"http://127.0.0.1:{port}" for name, port in ports.items()}
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        for url in urls.values():
            async with asyncio.timeout(20):
                while True:
                    try:
                        if (await client.get(url + "/health")).status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    await asyncio.sleep(0.05)
        created = await client.post(urls["owner"] + "/api/qa/conversations", json={"title": "SSE"})
        created.raise_for_status()
        cid = created.json()["id"]
        route = f"/api/qa/conversations/{cid}/messages/stream"
        body = {"query": "Verify shared stream.", "request_id": "shared-process-request"}
        seen: list[str] = []
        async with client.stream("POST", urls["owner"] + route, json=body) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if "First provisional answer." in line:
                    break
        disconnected_at = time.monotonic()
        async with client.stream("POST", urls["observer"] + route, json=body) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                seen.append(line)
                if "First provisional answer." in line:
                    replay_delay = time.monotonic() - disconnected_at
                    assert any("First reasoning." in event for event in seen)
                    status = await client.get(
                        urls["observer"]
                        + f"/api/qa/conversations/{cid}/requests/{body['request_id']}"
                    )
                    assert status.json()["status"] == "running"
                    (directory / "revise").touch()
                if "Revised provisional answer." in line:
                    assert any("event: reset" in event for event in seen)
                    assert any("Second reasoning." in event for event in seen)
                    (directory / "finish").touch()
                if "Validated final answer." in line:
                    break
        saved = (await client.get(urls["observer"] + f"/api/qa/conversations/{cid}")).json()
        assert len(saved["messages"]) == 1
        assert saved["messages"][0]["answer"] == "Validated final answer."
        executions = (directory / "executions.log").read_text(encoding="utf-8").splitlines()
        assert executions == ["owner"]
        return {
            "api_processes": 2,
            "executions": len(executions),
            "real_model_calls": 0,
            "reconnect_replay_seconds": round(replay_delay, 3),
            "final_messages": 1,
        }


def main() -> None:
    from deep_research.migrate import upgrade_head

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    root = parser.parse_args().output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    database = f"sqlite+aiosqlite:///{(root / 'qa.db').as_posix()}"
    asyncio.run(upgrade_head(database))
    context = multiprocessing.get_context("spawn")
    ports = context.Queue()
    processes = [
        context.Process(target=serve, args=(database, str(root), name, ports))
        for name in ("owner", "observer")
    ]
    try:
        for process in processes:
            process.start()
        addresses = dict(ports.get(timeout=20) for _ in processes)
        result = asyncio.run(check(root, addresses))
        (root / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result))
    finally:
        (root / "stop").touch()
        for process in processes:
            if process.pid is not None:
                process.join(timeout=10)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
        ports.close()


if __name__ == "__main__":
    main()
