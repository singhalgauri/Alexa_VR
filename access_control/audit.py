"""
Privacy-Preserving Audit Logger.
Logs every access decision: speaker id or 'guest', category, action, tier,
allowed/denied status, reason, confidence, and timestamp.
CRITICAL: Never logs raw audio or PINs.
"""

import json
import logging
from pathlib import Path
from typing import Optional, List

from .models import AuditRecord

logger = logging.getLogger(__name__)


class AccessAuditLogger:
    """
    Appends privacy-safe audit records to a JSONL file and maintains recent records in-memory.
    """

    def __init__(self, log_path: Optional[str] = "access_control_audit.jsonl"):
        self.log_path = Path(log_path) if log_path else None
        self.records: List[AuditRecord] = []

    def log_decision(
        self,
        timestamp: str,
        speaker_id: Optional[str],
        effective_role: str,
        category: str,
        action: str,
        tier: int,
        allowed: bool,
        status: str,
        reason: str,
        confidence: float,
    ) -> AuditRecord:
        record = AuditRecord(
            timestamp=timestamp,
            speaker_id=speaker_id if speaker_id else "guest",
            effective_role=effective_role,
            category=category,
            action=action,
            tier=tier,
            allowed=allowed,
            status=status,
            reason=reason,
            confidence=round(confidence, 3),
        )
        self.records.append(record)

        if self.log_path:
            try:
                line = json.dumps(record.__dict__)
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception as e:
                logger.error(f"Failed to append audit record: {e}")

        return record

    def get_recent(self, count: int = 20) -> List[AuditRecord]:
        return self.records[-count:]
