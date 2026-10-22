"""
Rate Limiter & Security Lockout Manager.
Tracks consecutive failed attempts on sensitive actions and locks out attackers.
"""

import time
import logging
from typing import Dict, Tuple

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Enforces lockout after N consecutive failed attempts at sensitive actions
    (e.g., incorrect PINs, repeated unauthorized access to security locks / payments).
    """

    def __init__(self, max_failures: int = 3, lockout_seconds: int = 300):
        self.max_failures = max_failures
        self.lockout_seconds = lockout_seconds
        self._failures: Dict[str, int] = {}
        self._lockouts: Dict[str, float] = {}  # key -> lockout_expiry_timestamp

    def is_locked_out(self, key: str = "global") -> Tuple[bool, int]:
        """
        Checks whether the entity/speaker is currently locked out.
        Returns: (is_locked, remaining_seconds)
        """
        now = time.time()
        expiry = self._lockouts.get(key, 0.0)
        if now < expiry:
            remaining = int(expiry - now)
            return True, remaining
        elif expiry > 0:
            # Lockout expired, clean up
            del self._lockouts[key]
            self._failures[key] = 0

        return False, 0

    def record_failure(self, key: str = "global") -> bool:
        """
        Increments failed attempt count. If threshold exceeded, initiates lockout.
        Returns: True if a lockout was triggered by this failure, False otherwise.
        """
        count = self._failures.get(key, 0) + 1
        self._failures[key] = count
        logger.warning("Security failure recorded for %s (attempt %d/%d)", key, count, self.max_failures)

        if count >= self.max_failures:
            self._lockouts[key] = time.time() + self.lockout_seconds
            logger.error("SECURITY ALERT: %s has been locked out for %d seconds!", key, self.lockout_seconds)
            return True
        return False

    def record_success(self, key: str = "global") -> None:
        """Resets the failure counter upon successful authorized request."""
        self._failures[key] = 0

    def reset_lockout(self, key: str = "global") -> None:
        """Manually clears any active lockout."""
        self._lockouts.pop(key, None)
        self._failures[key] = 0
