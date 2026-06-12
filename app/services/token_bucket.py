"""In-memory token bucket for per-user chat rate limits (single-replica dev)."""

from __future__ import annotations

import time


class TokenBucket:
    """Continuous token bucket with configurable burst."""

    def __init__(self, *, rate_per_minute: float, burst: int) -> None:
        self._rate = max(rate_per_minute, 0.0) / 60.0
        self._burst = max(int(burst), 1)
        self._tokens = float(self._burst)
        self._updated = time.monotonic()

    def consume(self, amount: float = 1.0) -> bool:
        """Return True when ``amount`` tokens were consumed."""
        now = time.monotonic()
        elapsed = max(0.0, now - self._updated)
        self._updated = now
        if self._rate > 0:
            self._tokens = min(float(self._burst), self._tokens + elapsed * self._rate)
        else:
            self._tokens = float(self._burst)
        if self._tokens >= amount:
            self._tokens -= amount
            return True
        return False

    @property
    def remaining(self) -> int:
        return max(0, int(self._tokens))

    @property
    def limit(self) -> int:
        return self._burst

    def retry_after_seconds(self) -> int:
        """Seconds until one token is available (minimum 1)."""
        if self._tokens >= 1 or self._rate <= 0:
            return 1
        deficit = 1.0 - self._tokens
        return max(1, int(deficit / self._rate) + 1)


class PerUserRateLimiter:
    """Lazy per-key token buckets (process-local)."""

    def __init__(self) -> None:
        self._buckets: dict[str, TokenBucket] = {}

    def check(self, key: str, *, rate_per_minute: float, burst: int) -> tuple[bool, TokenBucket]:
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = TokenBucket(rate_per_minute=rate_per_minute, burst=burst)
            self._buckets[key] = bucket
        allowed = bucket.consume(1.0)
        return allowed, bucket
