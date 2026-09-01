"""Small, dependency-free Prometheus exposition for service-level metrics.

The registry intentionally keeps a bounded label vocabulary. It is suitable
for API-process metrics without requiring a separate metrics dependency.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from collections.abc import Iterable, Mapping


def _labels_key(labels: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(key), str(value)) for key, value in labels.items()))


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class MetricsRegistry:
    """Thread-safe counters and gauges with deterministic exposition."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._types: dict[str, str] = {}

    def inc(self, name: str, labels: Mapping[str, str] | None = None, value: float = 1.0) -> None:
        self._register(name, "counter")
        key = (name, _labels_key(labels or {}))
        with self._lock:
            self._counters[key] += value

    def set_gauge(self, name: str, value: float, labels: Mapping[str, str] | None = None) -> None:
        self._register(name, "gauge")
        key = (name, _labels_key(labels or {}))
        with self._lock:
            self._gauges[key] = value

    def _register(self, name: str, kind: str) -> None:
        with self._lock:
            existing = self._types.get(name)
            if existing is not None and existing != kind:
                raise ValueError(f"metric {name!r} already registered as {existing}")
            self._types[name] = kind

    def snapshot_counters(self, names: Iterable[str]) -> dict[str, float]:
        """Return current counter values keyed by ``name{label="value",…}``.

        Exposition text is for scraping; tests and offline comparisons need the
        numbers without reparsing it.
        """
        wanted = set(names)
        with self._lock:
            counters = dict(self._counters)
        out: dict[str, float] = {}
        for (name, labels), value in counters.items():
            if name not in wanted:
                continue
            if labels:
                rendered = ",".join(f'{key}="{_escape(val)}"' for key, val in labels)
                out[f"{name}{{{rendered}}}"] = value
            else:
                out[name] = value
        return out

    def render(self) -> str:
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            types = dict(self._types)

        families: dict[str, list[tuple[tuple[tuple[str, str], ...], float]]] = defaultdict(list)
        for (name, labels), value in counters.items():
            families[name].append((labels, value))
        for (name, labels), value in gauges.items():
            families[name].append((labels, value))

        lines: list[str] = []
        for name in sorted(families):
            lines.append(f"# TYPE {name} {types[name]}")
            for labels, value in sorted(families[name]):
                rendered_labels = ""
                if labels:
                    rendered_labels = (
                        "{" + ",".join(f'{key}="{_escape(value)}"' for key, value in labels) + "}"
                    )
                lines.append(f"{name}{rendered_labels} {value:g}")
        return "\n".join(lines) + ("\n" if lines else "")


metrics = MetricsRegistry()
