"""Small, low-overhead timing logs for operational performance checks."""

from __future__ import annotations

import logging
import time


logger = logging.getLogger("zefron.performance")


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