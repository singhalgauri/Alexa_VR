"""
mcp_server/user_data.py – Per-user persistent data store.

Stores schedule events, reminders, shopping lists, routines, and profile
for each enrolled user in individual JSON files under data/<user>.json.

The data/ directory is git-ignored.  On first access for a user, the file
is created with empty collections.  The seed script populates demo data.

Thread safety: a module-level lock guards all reads/writes.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any, Optional

DATA_DIR = Path(os.environ.get("VOICEGATE_USER_DATA_DIR", "data"))

_lock = threading.Lock()


def _user_path(user: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / f"{user}.json"


def _empty_store() -> dict[str, Any]:
    return {
        "profile": {
            "display_name": "",
            "greeting_style": "friendly",
        },
        "schedule": [],       # [{title, date, time, description?}]
        "reminders": [],      # [str]
        "shopping_list": [],   # [str]
        "lists": {},           # {list_name: [str]}
        "routines": [],        # [{name, steps: [str]}]
    }


def _load(user: str) -> dict[str, Any]:
    p = _user_path(user)
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Back-fill missing keys
            empty = _empty_store()
            for k, v in empty.items():
                data.setdefault(k, v)
            if "profile" in empty:
                for k2, v2 in empty["profile"].items():
                    data["profile"].setdefault(k2, v2)
            return data
        except Exception:
            pass
    return _empty_store()


def _save(user: str, data: dict[str, Any]) -> None:
    p = _user_path(user)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_user_data(user: str) -> dict[str, Any]:
    """Return entire user data store (copy)."""
    with _lock:
        return _load(user)


def save_user_data(user: str, data: dict[str, Any]) -> None:
    """Persist user data."""
    with _lock:
        _save(user, data)


def get_profile(user: str) -> dict[str, Any]:
    with _lock:
        return _load(user).get("profile", {})


def set_profile(user: str, profile: dict[str, Any]) -> None:
    with _lock:
        d = _load(user)
        d["profile"].update(profile)
        _save(user, d)


# Schedule -------------------------------------------------------------------

def get_schedule(user: str, day: Optional[str] = None) -> list[dict]:
    """Return schedule events, optionally filtered by day (YYYY-MM-DD)."""
    with _lock:
        events = _load(user).get("schedule", [])
    if day:
        events = [e for e in events if e.get("date") == day]
    return events


def add_event(user: str, title: str, when: str, time_str: str = "", description: str = "") -> dict:
    """Add a schedule event. 'when' is YYYY-MM-DD."""
    event = {
        "title": title,
        "date": when,
        "time": time_str,
        "description": description,
    }
    with _lock:
        d = _load(user)
        d["schedule"].append(event)
        _save(user, d)
    return event


def next_event(user: str) -> Optional[dict]:
    """Return the next upcoming event from today onwards."""
    today = date.today().isoformat()
    now_time = datetime.now().strftime("%H:%M")
    with _lock:
        events = _load(user).get("schedule", [])
    future = []
    for e in events:
        edate = e.get("date", "")
        etime = e.get("time", "00:00")
        if edate > today or (edate == today and etime >= now_time):
            future.append(e)
    if not future:
        return None
    future.sort(key=lambda e: (e.get("date", ""), e.get("time", "")))
    return future[0]


# Reminders ------------------------------------------------------------------

def get_reminders(user: str) -> list[str]:
    with _lock:
        return list(_load(user).get("reminders", []))


def add_reminder(user: str, text: str) -> None:
    with _lock:
        d = _load(user)
        d["reminders"].append(text)
        _save(user, d)


# Shopping list --------------------------------------------------------------

def get_shopping_list(user: str) -> list[str]:
    with _lock:
        return list(_load(user).get("shopping_list", []))


def add_to_shopping_list(user: str, item: str) -> None:
    with _lock:
        d = _load(user)
        d["shopping_list"].append(item)
        _save(user, d)


# Named lists ----------------------------------------------------------------

def get_list(user: str, list_name: str) -> list[str]:
    with _lock:
        lname = list_name.lower()
        if lname == "shopping":
            items = _load(user).get("shopping_list", [])
            if not items:
                items = _load(user).get("lists", {}).get("shopping", [])
            return list(items)
        return list(_load(user).get("lists", {}).get(lname, []))


def add_to_list(user: str, list_name: str, item: str) -> None:
    with _lock:
        d = _load(user)
        lname = list_name.lower()
        if lname == "shopping":
            d["shopping_list"].append(item)
        if lname not in d["lists"]:
            d["lists"][lname] = []
        d["lists"][lname].append(item)
        _save(user, d)


# Routines -------------------------------------------------------------------

def get_routines(user: str) -> list[dict]:
    with _lock:
        return list(_load(user).get("routines", []))


def add_routine(user: str, name: str, steps: list[str]) -> None:
    with _lock:
        d = _load(user)
        d["routines"].append({"name": name, "steps": steps})
        _save(user, d)


# Utility: check if a user data file exists ----------------------------------

def user_has_data(user: str) -> bool:
    return _user_path(user).exists()


def list_users_with_data() -> list[str]:
    if not DATA_DIR.exists():
        return []
    return sorted(p.stem for p in DATA_DIR.glob("*.json") if not p.stem.startswith("_"))
