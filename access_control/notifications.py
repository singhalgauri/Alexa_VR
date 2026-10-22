"""
Notification Hook: Alerts enrolled users when sensitive actions are blocked or security lockouts occur.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SecurityNotification:
    timestamp: str
    target_enrolled_user: str
    event_type: str                   # e.g., "SENSITIVE_ACTION_BLOCKED", "SECURITY_LOCKOUT", "SPOOF_ATTEMPT"
    details: str
    severity: str                     # "WARNING", "CRITICAL"


class NotificationManager:
    """
    Dispatches security notifications to enrolled users.
    Stores past alerts for inspection and supports custom webhook/push handlers.
    """

    def __init__(self, external_dispatcher: Optional[Callable[[SecurityNotification], None]] = None):
        self.notifications: List[SecurityNotification] = []
        self._external_dispatcher = external_dispatcher

    def alert_enrolled_user(
        self,
        target_enrolled_user: str,
        event_type: str,
        details: str,
        severity: str = "WARNING",
    ) -> SecurityNotification:
        timestamp = datetime.now(timezone.utc).isoformat()
        notice = SecurityNotification(
            timestamp=timestamp,
            target_enrolled_user=target_enrolled_user,
            event_type=event_type,
            details=details,
            severity=severity,
        )
        self.notifications.append(notice)
        logger.warning(
            "NOTIFICATION to [%s] - Severity: %s - %s: %s",
            target_enrolled_user, severity, event_type, details
        )
        if self._external_dispatcher:
            try:
                self._external_dispatcher(notice)
            except Exception as e:
                logger.error("Failed to run external notification dispatcher: %e", e)

        return notice

    def get_recent_alerts(self, limit: int = 10) -> List[SecurityNotification]:
        return self.notifications[-limit:]
