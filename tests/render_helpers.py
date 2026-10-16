"""Explicit cooperative execution for unit tests that patch parent renderers.

Process liveness tests do not use this fixture: they launch the production
RenderProcess and inject faults in a real child instead.
"""

import asyncio
from contextlib import asynccontextmanager

from deep_research import render_tasks
from deep_research.blocking import run_rendering
from deep_research.persistence.repository import LeaseLostError
from deep_research.render_capacity import publication_guard, render_run_lock, rendering_capacity
from deep_research.render_process import RenderProcess


@asynccontextmanager
async def render_worker(repo, settings):
    """Supply the independent render consumer required by worker-mode HTTP tests."""
    from deep_research.render_service import service_for

    consumer = service_for(repo, settings, consume=True)
    managed = getattr(repo, "_render_dispatcher_managed", False)
    repo._render_dispatcher_managed = True
    polling = asyncio.create_task(consumer.poll_pending())
    try:
        yield consumer
    finally:
        polling.cancel()
        await asyncio.gather(polling, return_exceptions=True)
        for service in list(getattr(repo, "_render_services", {}).values()):
            await service.close()
        repo._render_dispatcher_managed = managed


def use_cooperative_render(monkeypatch):
    async def run(self, job, root, quota, authority):
        loop = asyncio.get_running_loop()

        def check():
            if self._aborted:
                raise LeaseLostError("render cancelled")
            if not asyncio.run_coroutine_threadsafe(authority(), loop).result(timeout=10):
                raise LeaseLostError("render authority lost")

        def execute():
            with (
                render_run_lock(root, job.run_id),
                rendering_capacity(root),
                publication_guard(check),
            ):
                check()
                return render_tasks.execute_render(job, root, quota)

        return await run_rendering(execute)

    monkeypatch.setattr(RenderProcess, "run", run)
