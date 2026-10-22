"""
app/audit.py – append-only JSONL audit log.

Each entry: { "ts": ISO8601, "user": str|null, "ip": str, "score": float|null,
              "granted": bool, "query": str|null }
No raw audio is ever written.
The last N entries can be retrieved for the UI.
"""

from __future__ import annotations

import json
import os
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

LOG_PATH: Path = Path(os.environ.get("VOICEGATE_AUDIT_LOG", "audit.jsonl"))
MAX_IN_MEMORY: int = 200  # keep in a ring buffer for fast UI reads

_lock = Lock()
_ring: deque[dict] = deque(maxlen=MAX_IN_MEMORY)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_event(
    *,
    granted: bool,
    ip: str,
    user: str | None = None,
    score: float | None = None,
    query: str | None = None,
) -> None:
    """Append one audit record to disk and the in-memory ring buffer."""
    entry = {
        "ts": _iso_now(),
        "granted": granted,
        "ip": ip,
        "user": user,
        "score": round(score, 4) if score is not None else None,
        "query": query,
    }
    with _lock:
        _ring.append(entry)
        try:
            with LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass  # Non-fatal: we still have the in-memory ring


def recent_events(n: int = 10) -> list[dict]:
    """Return the *n* most-recent audit entries (newest last)."""
    with _lock:
        entries = list(_ring)
    return entries[-n:]
