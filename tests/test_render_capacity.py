"""Physical render slots remain occupied until the actual process releases them."""

import multiprocessing
import os
import subprocess
import sys
import time

import pytest


def _occupy(root, entered, release):
    from deep_research.render_capacity import rendering_capacity

    with rendering_capacity(root):
        entered.put(True)
        release.wait(15)


@pytest.mark.parametrize("limit", [1, 2])
def test_render_capacity_is_shared_across_processes(tmp_path, monkeypatch, limit):
    monkeypatch.setenv("DR_RENDER_MAX_PROCESSES", str(limit))
    context = multiprocessing.get_context("spawn")
    entered = context.Queue()
    releases = [context.Event() for _ in range(limit + 1)]
    workers = [
        context.Process(target=_occupy, args=(str(tmp_path), entered, release))
        for release in releases
    ]
    try:
        for worker in workers:
            worker.start()
        for _ in range(limit):
            assert entered.get(timeout=15)
        time.sleep(0.2)
        assert entered.empty()
        for release in releases:
            release.set()
        assert entered.get(timeout=15)
        for worker in workers:
            worker.join(15)
            assert worker.exitcode == 0
    finally:
        for release in releases:
            release.set()
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(5)
        entered.close()


@pytest.mark.parametrize("value", ["1", "2", "0", "-1", "nan", "1.5"])
def test_dispatcher_and_child_capacity_share_validated_environment(value):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from deep_research.render_capacity import RENDER_CAPACITY as physical; "
                "from deep_research.render_service import RENDER_CAPACITY as dispatched; "
                "assert physical == dispatched; print(physical)"
            ),
        ],
        env={**os.environ, "DR_RENDER_MAX_PROCESSES": value},
        capture_output=True,
        text=True,
        timeout=30,
    )
    if value in {"1", "2"}:
        assert result.returncode == 0 and result.stdout.strip() == value
    else:
        assert result.returncode != 0 and "must be a positive integer" in result.stderr


def test_crashed_process_releases_its_physical_slot(tmp_path):
    context = multiprocessing.get_context("spawn")
    entered, release = context.Queue(), context.Event()
    first = context.Process(target=_occupy, args=(str(tmp_path), entered, release))
    first.start()
    try:
        assert entered.get(timeout=15)
        first.terminate()
        first.join(5)
        from deep_research.render_capacity import rendering_capacity

        with rendering_capacity(str(tmp_path), limit=1):
            pass
    finally:
        if first.is_alive():
            first.terminate()
            first.join(5)
        entered.close()
