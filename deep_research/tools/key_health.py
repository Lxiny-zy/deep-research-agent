"""Process-local credential cooldown and concurrency, shared across runs.

Only fingerprints are retained. Event-loop scoping prevents cross-loop asyncio
locks and keeps test/application lifetimes isolated. Worker processes are independent.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import weakref
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx


def failure_kind(exc: Exception) -> str | None:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in {401, 403}:
            return "invalid"
        if status in {402, 429}:
            return "limited"
        return None
    # SDKs such as Tavily may not expose an HTTPStatusError. Do not treat
    # arbitrary 'limit' text (e.g. response size limits) as quota exhaustion.
    message = str(exc).lower()
    if any(word in message for word in ("401", "403", "invalid api key", "unauthorized")):
        return "invalid"
    if any(word in message for word in ("429", "quota", "rate limit", "credit", "exhaust")):
        return "limited"
    return None


def retry_delay(exc: Exception, kind: str) -> float:
    fallback = 300.0 if kind == "invalid" else 30.0
    if not isinstance(exc, httpx.HTTPStatusError):
        return fallback
    raw = exc.response.headers.get("retry-after", "")
    try:
        seconds = float(raw)
    except ValueError:
        try:
            deadline = parsedate_to_datetime(raw)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=UTC)
            seconds = (deadline - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return fallback
    if not 0 <= seconds < float("inf"):
        return fallback
    return max(1.0, seconds)


@dataclass
class KeyHealth:
    retry_at: float = 0.0
    status: str = "ready"
    active: int = 0
    waiting: int = 0
    last_used: float = field(default_factory=time.monotonic)
    slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(2))

    @property
    def ready(self) -> bool:
        return time.monotonic() >= self.retry_at

    def fail(self, exc: Exception, kind: str) -> float:
        delay = retry_delay(exc, kind)
        self.retry_at = max(self.retry_at, time.monotonic() + delay)
        self.status = kind
        return delay


_registries: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, KeyHealth]] = (
    weakref.WeakKeyDictionary()
)


def shared_health(provider: str, namespace: str, secret: str) -> KeyHealth:
    registry = _registries.setdefault(asyncio.get_running_loop(), {})
    identity = hashlib.sha256(f"{provider}\0{namespace}\0{secret}".encode()).hexdigest()
    if identity not in registry:
        # Expired, unused entries may be evicted. Never evict active cooldowns
        # or waiters, which would allow new pools to bypass the shared limit.
        if len(registry) >= 2048:
            for key, health in list(registry.items()):
                if health.ready and not health.active and not health.waiting:
                    if time.monotonic() - health.last_used > 300:
                        del registry[key]
        registry[identity] = KeyHealth()
    health = registry[identity]
    health.last_used = time.monotonic()
    return health
