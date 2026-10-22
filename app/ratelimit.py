"""
app/ratelimit.py – simple in-memory rate limiter for failed auth attempts.

Strategy: sliding-window per client IP.  After MAX_FAILURES failures within
WINDOW_SECONDS seconds, subsequent requests are rejected for BLOCK_SECONDS.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

MAX_FAILURES: int = 5
WINDOW_SECONDS: int = 60
BLOCK_SECONDS: int = 300  # 5 minutes


class RateLimiter:
    """Thread-safe sliding-window rate limiter keyed by client IP."""

    def __init__(
        self,
        max_failures: int = MAX_FAILURES,
        window: int = WINDOW_SECONDS,
        block: int = BLOCK_SECONDS,
    ) -> None:
        self._max = max_failures
        self._window = window
        self._block = block
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._blocked_until: dict[str, float] = {}
        self._lock = Lock()

    def is_blocked(self, ip: str) -> bool:
        """Return True if *ip* is currently rate-limited."""
        with self._lock:
            unblock_at = self._blocked_until.get(ip, 0)
            if time.time() < unblock_at:
                return True
            # Clear expired block
            if unblock_at:
                del self._blocked_until[ip]
            return False

    def record_failure(self, ip: str) -> None:
        """Record a failed verification attempt for *ip*."""
        with self._lock:
            now = time.time()
            q = self._failures[ip]
            # Drop old entries outside the window
            while q and now - q[0] > self._window:
                q.popleft()
            q.append(now)
            if len(q) >= self._max:
                self._blocked_until[ip] = now + self._block
                q.clear()

    def record_success(self, ip: str) -> None:
        """Clear failure history for *ip* after a successful verification."""
        with self._lock:
            self._failures.pop(ip, None)
            self._blocked_until.pop(ip, None)


# Module-level singleton
_limiter = RateLimiter()


def is_blocked(ip: str) -> bool:
    return _limiter.is_blocked(ip)


def record_failure(ip: str) -> None:
    _limiter.record_failure(ip)


def record_success(ip: str) -> None:
    _limiter.record_success(ip)
