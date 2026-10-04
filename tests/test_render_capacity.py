"""Physical render slots remain occupied until the actual process releases them."""

import multiprocessing
import time


def _occupy(root, entered, release):
    from deep_research.render_capacity import rendering_capacity

    with rendering_capacity(root):
        entered.put(True)
        release.wait(15)


def test_render_capacity_is_shared_across_processes(tmp_path):
    context = multiprocessing.get_context("spawn")
    entered = context.Queue()
    releases = [context.Event() for _ in range(3)]
    workers = [
        context.Process(target=_occupy, args=(str(tmp_path), entered, release))
        for release in releases
    ]
    try:
        for worker in workers:
            worker.start()
        assert entered.get(timeout=15)
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
