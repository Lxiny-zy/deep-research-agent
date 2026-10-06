"""Run the actual worker CLI shutdown path against cancellation-resistant cleanup."""

import asyncio
import sys

from deep_research import render_service
from deep_research import worker as worker_module
from deep_research.config import Settings
from deep_research.execution import ExecutionContext, RunExecutor
from deep_research.persistence.memory_repository import InMemoryRepository


async def stubborn():
    while True:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass


async def main(mode):
    settings = Settings(app_env="test", worker_shutdown_grace_seconds=0)
    worker_module.Settings = lambda: settings
    worker_module._CLEANUP_SECONDS = 0.2
    repo = InMemoryRepository()
    worker = worker_module.Worker(repo, RunExecutor(ExecutionContext(repo=repo)), settings)

    class Rendering:
        requires_hard_exit = False

        async def poll_pending(self):
            if mode == "dispatcher":
                await stubborn()
            else:
                await asyncio.Event().wait()

        async def close(self, **kwargs):
            if mode == "close":
                await stubborn()

    class Engine:
        async def dispose(self):
            if mode == "dispose":
                await stubborn()

    async def build(_):
        return worker, Engine()

    if mode == "run":
        work = asyncio.create_task(stubborn(), name="stuck-run")
        worker._running.add(work)
    if mode == "unregister":
        repo.remove_worker = lambda _: stubborn()
    worker_module._build_worker = build
    render_service.service_for = lambda *_: Rendering()
    asyncio.get_running_loop().call_later(0.05, worker.request_stop)
    print("shutdown-fixture-started", flush=True)
    return await worker_module.main_async(["--name", "shutdown-test"])


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1])))
