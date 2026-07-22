"""Exponential backoff retry helper.

Used by :class:`agent_capture.exporter.http.HTTPExporter` for transient
failures (5xx, network errors). Never used for permanent failures (4xx,
malformed payload) — those drop after the first attempt with a loud
safelog entry.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff schedule.

    Sleep between attempt N and N+1 is::

        min(max_delay_s, base_delay_s * (multiplier ** N)) * (1 + jitter * U)

    where ``U`` is uniform in ``[-1, 1]``. Capped at ``max_delay_s``.
    """

    max_attempts: int = 5
    base_delay_s: float = 0.5
    max_delay_s: float = 30.0
    multiplier: float = 2.0
    jitter: float = 0.25
    sleep: Callable[[float], None] = time.sleep

    def delay(self, attempt: int) -> float:
        raw = min(self.max_delay_s, self.base_delay_s * (self.multiplier**attempt))
        jit = raw * self.jitter * (2 * random.random() - 1)
        return max(0.0, raw + jit)


def with_retry(
    fn: Callable[[], T],
    *,
    policy: RetryPolicy = RetryPolicy(),
    retryable: Callable[[BaseException], bool] = lambda _exc: True,
) -> T:
    """Call ``fn`` with exponential backoff. Re-raises the last error.

    Args:
        fn: Zero-arg callable that performs the operation.
        policy: Backoff schedule.
        retryable: Predicate determining whether an exception should
            trigger another attempt. Default: retry on everything.
            Pass a stricter predicate to bypass retries for permanent
            failures (4xx responses, schema errors, etc.).
    """
    last: BaseException | None = None
    for attempt in range(policy.max_attempts):
        try:
            return fn()
        except BaseException as exc:
            last = exc
            if attempt == policy.max_attempts - 1 or not retryable(exc):
                raise
            policy.sleep(policy.delay(attempt))
    assert last is not None  # unreachable
    raise last
