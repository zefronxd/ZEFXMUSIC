"""Small, low-overhead timing logs for operational performance checks."""

from __future__ import annotations

import logging
import time
from collections import deque
from threading import Lock


logger = logging.getLogger("zefron.performance")

_WINDOW_SECONDS = 60.0
_incoming_updates: deque[float] = deque()
_outgoing_requests: deque[float] = deque()
_api_latencies_ms: deque[tuple[float, float]] = deque()
_traffic_lock = Lock()


def _prune(now: float) -> None:
    cutoff = now - _WINDOW_SECONDS
    while _incoming_updates and _incoming_updates[0] <= cutoff:
        _incoming_updates.popleft()
    while _outgoing_requests and _outgoing_requests[0] <= cutoff:
        _outgoing_requests.popleft()
    while _api_latencies_ms and _api_latencies_ms[0][0] <= cutoff:
        _api_latencies_ms.popleft()


def record_incoming(count: int = 1) -> None:
    """Record raw Telegram updates received by the bot."""
    now = time.monotonic()
    with _traffic_lock:
        _prune(now)
        _incoming_updates.extend([now] * max(0, count))


def record_outgoing(started: float | None = None) -> None:
    """Record a Telegram API request and optionally its start time."""
    now = time.monotonic()
    with _traffic_lock:
        _prune(now)
        _outgoing_requests.append(now)
        if started is not None:
            _api_latencies_ms.append((now, (now - started) * 1000))


def traffic_snapshot() -> dict[str, float | int]:
    """Return rolling last-minute Telegram traffic and API speed stats."""
    now = time.monotonic()
    with _traffic_lock:
        _prune(now)
        latency_count = len(_api_latencies_ms)
        avg_latency = (
            sum(latency for _, latency in _api_latencies_ms) / latency_count
            if latency_count
            else 0.0
        )
        return {
            "incoming_per_minute": len(_incoming_updates),
            "outgoing_per_minute": len(_outgoing_requests),
            "api_avg_ms": round(avg_latency, 1),
        }


def log_timing(operation: str, started: float, **fields: object) -> None:
    """Log an operation duration in milliseconds without logging secrets."""
    elapsed_ms = (time.perf_counter() - started) * 1000
    details = " ".join(f"{key}={value}" for key, value in fields.items())
    logger.info(
        "timing operation=%s elapsed_ms=%.1f%s",
        operation,
        elapsed_ms,
        f" {details}" if details else "",
    )