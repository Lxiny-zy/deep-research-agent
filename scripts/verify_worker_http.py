"""Release regression: separate API/worker processes over real HTTP and SQLite.

Only providers are synthetic; migrations, configuration, queue, SSE, cancellation,
recovery and report export use production code and disposable local state.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts" / "worker-http"


async def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dra-worker-http-") as directory:
        root = Path(directory)
        env = dict(os.environ)
        env.update(
            APP_ENV="test",
            DATABASE_URL=f"sqlite+aiosqlite:///{root / 'research.sqlite'}",
            RUNTIME_CONFIG_PATH=str(root / "runtime_config.json"),
            DR_ARTIFACT_ROOT=str(root / "artifacts"),
            API_KEY="http-regression-key",
            DR_API_KEYS="[]",
            LLM_API_KEY="synthetic-llm-key",
            LLM_BASE_URL="",
            TAVILY_API_KEY="synthetic-search-key",
            BRAVE_API_KEY="",
            SERPER_API_KEY="",
            XAI_API_KEY="",
            DR_SEARCH_BACKENDS="tavily",
            CATALOG_ENCRYPTION_KEY=Fernet.generate_key().decode(),
            DR_EXECUTION_MODE="worker",
            DR_WORKER_POLL_SECONDS="0.05",
            MAX_ACTIVE_RUNS="1",
            MAX_QUEUED_RUNS="0",
            MAX_RUN_SECONDS="90",
            INTENT_ENABLED="false",
            DR_RUNNER_ENABLED="false",
            DR_DEMO_FAKE_BACKENDS="1",
            DR_DEMO_STEP_DELAY="0.25",
            DR_DEMO_TOKENS_PER_CALL="50",
            PYTHONIOENCODING="utf-8",
        )
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        children: list[asyncio.subprocess.Process] = []
        logs = []

        async def launch(name, *command):
            log = (OUTPUT / f"{name}.log").open("wb")
            logs.append(log)
            child = await asyncio.create_subprocess_exec(
                sys.executable,
                "-u",
                "-X",
                "utf8",
                *command,
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=asyncio.subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            children.append(child)
            return child

        async def eventually(fetch, accept, seconds=25):
            async with asyncio.timeout(seconds):
                while True:
                    try:
                        value = await fetch()
                    except httpx.TransportError:
                        value = None
                    if value is not None and accept(value):
                        return value
                    assert all(child.returncode is None for child in children), "service exited"
                    await asyncio.sleep(0.1)

        try:
            migration = await launch("migration", "-m", "deep_research.migrate")
            assert await migration.wait() == 0, "migration failed"
            children.remove(migration)
            await launch(
                "api",
                "-m",
                "uvicorn",
                "deep_research.api:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            )
            async with httpx.AsyncClient(
                base_url=origin, headers={"X-API-Key": env["API_KEY"]}, timeout=30
            ) as client:
                await eventually(
                    lambda: client.get("/livez"), lambda response: response.status_code == 200
                )
                assert (await client.get("/readyz")).status_code == 503
                worker = await launch("worker", "-m", "deep_research.worker")
                await eventually(
                    lambda: client.get("/readyz"), lambda response: response.status_code == 200
                )
                config = (await client.get("/api/config")).json()
                changed = await client.put(
                    "/api/config",
                    json={
                        "version": config["version"],
                        "llm_model": "http-shared-model",
                        "llm_api_key": "rotated-synthetic-key",
                    },
                )
                assert changed.status_code == 200, changed.text

                async def submit(key):
                    return await client.post(
                        "/api/runs",
                        json={
                            "query": "解释可再生能源技术的主要证据",
                            "workflow": "quick",
                        },
                        headers={"Idempotency-Key": key},
                    )

                created = await submit("first-request")
                assert created.status_code == 202, created.text
                run_id = created.json()["run_id"]
                replay = await submit("first-request")
                assert replay.status_code == 202 and replay.json()["run_id"] == run_id
                assert (await submit("over-capacity")).status_code == 503
                events = []
                async with client.stream("GET", f"/api/runs/{run_id}/stream") as response:
                    assert response.status_code == 200
                    async for line in response.aiter_lines():
                        if line.startswith("data:"):
                            events.append(json.loads(line[5:].strip()))
                assert any(event["type"] == "token" for event in events), events
                assert any(event["type"] == "done" for event in events), events
                assert all(event.get("seq") is not None for event in events)
                sequences = [event["seq"] for event in events]
                assert sequences == sorted(set(sequences))
                detail = (await client.get(f"/api/runs/{run_id}")).json()
                assert detail["status"] == "done" and detail["total_tokens"] > 0
                assert detail["manifest"]["llm_model"] == "http-shared-model"
                document = await client.get(f"/api/runs/{run_id}/document.md")
                assert document.status_code == 200 and "测试报告" in document.text
                replayed = (
                    await client.get(
                        f"/api/runs/{run_id}/events", params={"after_seq": sequences[0] + 1}
                    )
                ).json()
                assert replayed and all(event["seq"] > sequences[0] for event in replayed)

                second = await submit("cancel-request")
                assert second.status_code == 202, second.text
                interrupted_id = second.json()["run_id"]
                await eventually(
                    lambda: client.get(f"/api/runs/{interrupted_id}/events"),
                    lambda response: any(
                        event["stage"] == "RESEARCHER" for event in response.json()
                    ),
                )
                cancelled = await client.post(f"/api/runs/{interrupted_id}/cancel")
                assert cancelled.status_code == 202
                await eventually(
                    lambda: client.get(f"/api/runs/{interrupted_id}"),
                    lambda response: response.json().get("status") == "cancelled",
                )
                resumed = await client.post(f"/api/runs/{interrupted_id}/resume")
                assert resumed.status_code == 409  # Explicit cancellation is terminal.
                # A real storage failure is recoverable. Restart only our own
                # worker with an exhausted quota, then restore normal capacity.
                worker.terminate()
                await worker.wait()
                children.remove(worker)
                env["DR_ARTIFACT_TOTAL_BYTES"] = "1"
                worker = await launch("worker-storage-fault", "-m", "deep_research.worker")
                failing = await submit("storage-recovery")
                assert failing.status_code == 202, failing.text
                recover_id = failing.json()["run_id"]
                await eventually(
                    lambda: client.get(f"/api/runs/{recover_id}"),
                    lambda response: response.json().get("status") == "error",
                )
                worker.terminate()
                await worker.wait()
                children.remove(worker)
                env["DR_ARTIFACT_TOTAL_BYTES"] = str(10 * 1024**3)
                await launch("worker-recovery", "-m", "deep_research.worker")
                resumed = await client.post(f"/api/runs/{recover_id}/resume")
                assert resumed.status_code == 202, resumed.text
                finished = await eventually(
                    lambda: client.get(f"/api/runs/{recover_id}"),
                    lambda response: response.json().get("status") in {"done", "error"},
                )
                assert finished.json()["status"] == "done", finished.text
                attempts = (await client.get(f"/api/runs/{recover_id}/events")).json()
                assert any(event["attempt"] == 2 and event["type"] == "done" for event in attempts)
                assert (await client.get(f"/api/runs/{recover_id}/document.md")).status_code == 200
                (OUTPUT / "results.json").write_text(
                    json.dumps(
                        {
                            "separate_processes": True,
                            "shared_configuration": True,
                            "idempotent_at_capacity": True,
                            "token_events": sum(e["type"] == "token" for e in events),
                            "cancel_resume_export": True,
                            "synthetic_providers": True,
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
        finally:
            for child in reversed(children):
                if child.returncode is None:
                    child.terminate()
                try:
                    await asyncio.wait_for(child.wait(), 5)
                except TimeoutError:
                    child.kill()
                    await child.wait()
            for log in logs:
                log.close()
    print(f"Separate API/worker HTTP checks passed. {OUTPUT}")


if __name__ == "__main__":
    asyncio.run(main())
