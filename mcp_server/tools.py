"""
mcp_server/tools.py – All MCP tool implementations.

Data is per-user and stored in memory (a dict). On restart, data resets –
acceptable for a hackathon demo; production would use a DB.

ALL tools retrieve the caller's identity from mcp_server.auth.current_user
(a ContextVar set by the middleware). The user name is NEVER accepted as a
tool parameter.
"""

from __future__ import annotations

import os
import sys
import random
import time
from datetime import datetime, timezone
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from mcp_server.auth import current_user  # noqa: E402

# ---------------------------------------------------------------------------
# In-memory per-user storage
# ---------------------------------------------------------------------------

_reminders: dict[str, list[str]] = defaultdict(list)
_lists: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
_timers: dict[str, list[dict]] = defaultdict(list)
_devices: dict[str, dict[str, str]] = defaultdict(
    lambda: {
        "living room light": "off",
        "bedroom light": "off",
        "thermostat": "68°F",
    }
)


def _user() -> str:
    u = current_user.get()
    if not u:
        raise RuntimeError("No authenticated user in context")
    return u


# ---------------------------------------------------------------------------
# Tool implementations (called by mcp_server/server.py via FastMCP decorators)
# ---------------------------------------------------------------------------

def get_time() -> str:
    """Return the current time."""
    now = datetime.now(timezone.utc)
    return f"It is {now.strftime('%I:%M %p')} UTC."


def add_reminder(text: str) -> str:
    """Add a reminder."""
    u = _user()
    _reminders[u].append(text)
    return f"Reminder added: {text}"


def list_reminders() -> str:
    """List your reminders."""
    u = _user()
    items = _reminders[u]
    if not items:
        return "You have no reminders."
    numbered = " | ".join(f"{i+1}. {r}" for i, r in enumerate(items))
    return f"Your reminders: {numbered}"


def add_to_list(list_name: str, item: str) -> str:
    """Add an item to a named list."""
    u = _user()
    _lists[u][list_name.lower()].append(item)
    return f"Added {item} to your {list_name} list."


def read_list(list_name: str) -> str:
    """Read items from a named list."""
    u = _user()
    items = _lists[u].get(list_name.lower(), [])
    if not items:
        return f"Your {list_name} list is empty."
    return f"Your {list_name} list: {', '.join(items)}."


def set_timer(minutes: float, label: str = "timer") -> str:
    """Set a timer."""
    u = _user()
    due = time.time() + minutes * 60
    _timers[u].append({"label": label, "due": due, "minutes": minutes})
    return f"Timer set: {label} for {minutes} minute{'s' if minutes != 1 else ''}."


def control_device(device: str, state: str) -> str:
    """Control a simulated smart-home device (lights or thermostat)."""
    u = _user()
    device_key = device.lower().strip()
    valid_devices = list(_devices[u].keys())
    if device_key not in valid_devices:
        return (
            f"Unknown device '{device}'. "
            f"Available: {', '.join(valid_devices)}."
        )
    _devices[u][device_key] = state.lower()
    return f"{device.title()} is now {state.lower()}."


def get_weather_stub(city: str) -> str:
    """Return simulated weather data (clearly labeled as simulated)."""
    conditions = ["sunny", "partly cloudy", "overcast", "light rain", "clear"]
    temps = list(range(55, 90, 5))
    condition = random.choice(conditions)
    temp = random.choice(temps)
    return (
        f"[SIMULATED] Weather for {city}: {condition}, {temp}°F. "
        "This is demo data, not a real forecast."
    )
