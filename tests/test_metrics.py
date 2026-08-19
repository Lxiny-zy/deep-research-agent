from __future__ import annotations

import threading

from deep_research.metrics import MetricsRegistry


def test_metrics_registry_renders_escaped_labels_and_types() -> None:
    registry = MetricsRegistry()
    registry.inc("requests_total", {"route": '/api/"quoted"'})
    registry.set_gauge("workers_gauge", 3, {"state": "ready"})

    rendered = registry.render()

    assert "# TYPE requests_total counter" in rendered
    assert 'requests_total{route="/api/\\"quoted\\""} 1' in rendered
    assert "# TYPE workers_gauge gauge" in rendered
    assert 'workers_gauge{state="ready"} 3' in rendered


def test_metrics_registry_increments_concurrently() -> None:
    registry = MetricsRegistry()

    def increment() -> None:
        for _ in range(200):
            registry.inc("jobs_total", {"kind": "test"})

    threads = [threading.Thread(target=increment) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert 'jobs_total{kind="test"} 1600' in registry.render()
