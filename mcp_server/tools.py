"""
mcp_server/tools.py – All MCP tool implementations.

Data is per-user and backed by mcp_server/user_data.py (JSON file per user
under data/).  All tools retrieve the caller's identity from
mcp_server.auth.current_user (a ContextVar set by the middleware).

Cross-user access goes through mcp_server.sharing.authorize() – the single
choke point for all data access authorization.

The user name is NEVER accepted as a tool parameter for the ACTING user.
An optional 'owner' argument is accepted on some tools to allow cross-user
reads/writes (subject to sharing policy).

Category mapping for sharing enforcement:
  - schedule tools     -> "calendar_schedule"
  - reminder tools     -> "reminders"
  - shopping list      -> "shopping"
  - named lists        -> "shopping"  (same category)
  - routines           -> "routines"
  - device control     -> "smart_home"
  - timer              -> "generic_timers"
"""

from __future__ import annotations

import os
import sys
import random
import re
from datetime import datetime, timezone, date, timedelta
from collections import defaultdict
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from mcp_server.auth import current_user  # noqa: E402
from mcp_server import user_data  # noqa: E402
from mcp_server.sharing import authorize  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _user() -> str:
    u = current_user.get()
    if not u:
        raise RuntimeError("No authenticated user in context")
    return u


def _resolve_owner(owner: Optional[str] = None) -> str:
    """Resolve the data owner: defaults to the authenticated user."""
    if owner and owner.strip():
        return owner.strip().lower()
    return _user().lower()


def _check_access(category: str, owner: Optional[str] = None, write: bool = False) -> tuple[str, str]:
    """
    Resolve owner and check sharing authorization.
    Returns (actor, owner) if allowed.
    Raises RuntimeError with a speakable refusal if denied.
    """
    actor = _user().lower()
    resolved_owner = _resolve_owner(owner)

    allowed, reason = authorize(actor, resolved_owner, category, write=write)
    if not allowed:
        # Speakable refusal that leaks nothing about the data
        if actor != resolved_owner:
            actor_display = actor.replace("_", " ").title()
            owner_display = resolved_owner.replace("_", " ").title()
            raise RuntimeError(
                f"Sorry {actor_display}, {owner_display} hasn't shared their "
                f"{category.replace('_', ' ')} with you."
            )
        raise RuntimeError(f"Access denied: {reason}")

    return actor, resolved_owner


def _display_name(user: str) -> str:
    """Get display name for a user, falling back to titlecased username."""
    profile = user_data.get_profile(user)
    return profile.get("display_name") or user.replace("_", " ").title()


def _time_greeting() -> str:
    """Return a time-of-day greeting."""
    hour = datetime.now().hour
    if hour < 12:
        return "Good morning"
    elif hour < 17:
        return "Good afternoon"
    elif hour < 21:
        return "Good evening"
    else:
        return "Good night"


def _personalized_prefix(user: str, is_own_data: bool = True) -> str:
    """Build a personalized greeting prefix."""
    name = _display_name(user)
    greeting = _time_greeting()
    if is_own_data:
        return f"{greeting}, {name}."
    return ""


def _format_date_speakable(date_str: str) -> str:
    """Convert YYYY-MM-DD to a speakable date."""
    try:
        d = date.fromisoformat(date_str)
        today = date.today()
        diff = (d - today).days
        if diff == 0:
            return "today"
        elif diff == 1:
            return "tomorrow"
        elif diff == -1:
            return "yesterday"
        elif 2 <= diff <= 6:
            return d.strftime("%A")  # "Monday", "Tuesday", etc.
        else:
            return d.strftime("%B %d")  # "October 25"
    except (ValueError, TypeError):
        return date_str


# ---------------------------------------------------------------------------
# In-memory per-user storage for non-persistent data (timers, devices)
# ---------------------------------------------------------------------------

_timers: dict[str, list[dict]] = defaultdict(list)
_devices: dict[str, dict[str, str]] = defaultdict(
    lambda: {
        "living room light": "off",
        "bedroom light": "off",
        "thermostat": "68°F",
    }
)


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def get_time() -> str:
    """Return the current time."""
    now = datetime.now(timezone.utc)
    return f"It is {now.strftime('%I:%M %p')} UTC."


def greeting() -> str:
    """Return a personalized greeting for the authenticated user."""
    actor = _user()
    name = _display_name(actor)
    time_greet = _time_greeting()
    return f"{time_greet}, {name}! How can I help you?"


# --- Schedule tools ---

def get_schedule(day: str = "", owner: str = "") -> str:
    """Get schedule events for a day. day: YYYY-MM-DD or empty for today."""
    actor, resolved_owner = _check_access("calendar_schedule", owner or None)
    is_own = actor == resolved_owner

    if not day:
        day = date.today().isoformat()

    events = user_data.get_schedule(resolved_owner, day=day)
    day_label = _format_date_speakable(day)

    owner_label = ""
    if not is_own:
        owner_label = f"{_display_name(resolved_owner)}'s "

    if not events:
        if is_own:
            prefix = _personalized_prefix(actor)
            return f"{prefix} You have no events {day_label}."
        return f"{_display_name(resolved_owner)} has no events {day_label}."

    prefix = _personalized_prefix(actor) if is_own else ""
    count = len(events)
    event_word = "event" if count == 1 else "events"

    lines = []
    for e in sorted(events, key=lambda x: x.get("time", "")):
        t = e.get("time", "")
        title = e.get("title", "Untitled")
        if t:
            lines.append(f"{t} - {title}")
        else:
            lines.append(title)

    event_list = ". ".join(lines)
    if is_own:
        return f"{prefix} You have {count} {event_word} {day_label}: {event_list}."
    return f"{owner_label}schedule {day_label}: {count} {event_word}. {event_list}."


def get_next_event(owner: str = "") -> str:
    """Get the next upcoming event."""
    actor, resolved_owner = _check_access("calendar_schedule", owner or None)
    is_own = actor == resolved_owner

    event = user_data.next_event(resolved_owner)
    if not event:
        if is_own:
            return f"{_personalized_prefix(actor)} You have no upcoming events."
        return f"{_display_name(resolved_owner)} has no upcoming events."

    day_label = _format_date_speakable(event.get("date", ""))
    time_str = event.get("time", "")
    title = event.get("title", "Untitled")

    if is_own:
        prefix = _personalized_prefix(actor)
        return f"{prefix} Your next event is {title} {day_label} at {time_str}."
    owner_name = _display_name(resolved_owner)
    return f"{owner_name}'s next event is {title} {day_label} at {time_str}."


def add_event(title: str, when: str, time_str: str = "", owner: str = "") -> str:
    """Add a calendar event. title: event name. when: YYYY-MM-DD. time_str: HH:MM."""
    actor, resolved_owner = _check_access("calendar_schedule", owner or None, write=True)

    event = user_data.add_event(resolved_owner, title, when, time_str)
    day_label = _format_date_speakable(when)

    if actor == resolved_owner:
        return f"Event added to your schedule: {title} {day_label}" + (f" at {time_str}" if time_str else "") + "."
    owner_name = _display_name(resolved_owner)
    return f"Event added to {owner_name}'s schedule: {title} {day_label}" + (f" at {time_str}" if time_str else "") + "."


# --- Reminder tools ---

def add_reminder(text: str, owner: str = "") -> str:
    """Add a reminder."""
    actor, resolved_owner = _check_access("reminders", owner or None, write=True)
    user_data.add_reminder(resolved_owner, text)

    if actor == resolved_owner:
        return f"Reminder added: {text}"
    return f"Reminder added to {_display_name(resolved_owner)}'s list: {text}"


def list_reminders(owner: str = "") -> str:
    """List reminders."""
    actor, resolved_owner = _check_access("reminders", owner or None)
    is_own = actor == resolved_owner

    items = user_data.get_reminders(resolved_owner)
    if not items:
        if is_own:
            return f"{_personalized_prefix(actor)} You have no reminders."
        return f"{_display_name(resolved_owner)} has no reminders."

    numbered = " | ".join(f"{i+1}. {r}" for i, r in enumerate(items))
    if is_own:
        prefix = _personalized_prefix(actor)
        return f"{prefix} Your reminders: {numbered}"
    return f"{_display_name(resolved_owner)}'s reminders: {numbered}"


# --- Shopping list tools ---

def add_to_shopping_list(item: str, owner: str = "") -> str:
    """Add an item to the shopping list."""
    actor, resolved_owner = _check_access("shopping", owner or None, write=True)
    user_data.add_to_shopping_list(resolved_owner, item)

    if actor == resolved_owner:
        return f"Added {item} to your shopping list."
    return f"Added {item} to {_display_name(resolved_owner)}'s shopping list."


def read_shopping_list(owner: str = "") -> str:
    """Read the shopping list."""
    actor, resolved_owner = _check_access("shopping", owner or None)
    is_own = actor == resolved_owner

    items = user_data.get_shopping_list(resolved_owner)
    if not items:
        if is_own:
            return f"{_personalized_prefix(actor)} Your shopping list is empty."
        return f"{_display_name(resolved_owner)}'s shopping list is empty."

    if is_own:
        prefix = _personalized_prefix(actor)
        return f"{prefix} Your shopping list: {', '.join(items)}."
    return f"{_display_name(resolved_owner)}'s shopping list: {', '.join(items)}."


# --- Named list tools ---

def add_to_list(list_name: str, item: str, owner: str = "") -> str:
    """Add an item to a named list."""
    actor, resolved_owner = _check_access("shopping", owner or None, write=True)
    user_data.add_to_list(resolved_owner, list_name, item)

    if actor == resolved_owner:
        return f"Added {item} to your {list_name} list."
    return f"Added {item} to {_display_name(resolved_owner)}'s {list_name} list."


def read_list(list_name: str, owner: str = "") -> str:
    """Read items from a named list."""
    actor, resolved_owner = _check_access("shopping", owner or None)
    is_own = actor == resolved_owner

    # Check both named lists and shopping list
    if list_name.lower() == "shopping":
        items = user_data.get_shopping_list(resolved_owner)
    else:
        items = user_data.get_list(resolved_owner, list_name)

    if not items:
        if is_own:
            return f"Your {list_name} list is empty."
        return f"{_display_name(resolved_owner)}'s {list_name} list is empty."

    if is_own:
        return f"Your {list_name} list: {', '.join(items)}."
    return f"{_display_name(resolved_owner)}'s {list_name} list: {', '.join(items)}."


# --- Timer tools ---

def set_timer(minutes: float, label: str = "timer") -> str:
    """Set a timer."""
    import time as _time
    u = _user()
    due = _time.time() + minutes * 60
    _timers[u].append({"label": label, "due": due, "minutes": minutes})
    return f"Timer set: {label} for {minutes} minute{'s' if minutes != 1 else ''}."


# --- Device control ---

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


# --- Weather stub ---

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


# --- Sharing management tools ---

def get_my_sharing() -> str:
    """Get the current user's sharing settings."""
    from mcp_server.sharing import get_sharing_policy
    actor = _user()
    policy = get_sharing_policy(actor)
    if not policy:
        return f"{_personalized_prefix(actor)} You haven't shared any data categories with other users."

    lines = []
    for cat, settings in policy.items():
        level = settings.get("level", "off")
        audience = settings.get("audience", "all_enrolled")
        if isinstance(audience, list):
            audience_str = ", ".join(audience)
        else:
            audience_str = "all enrolled users"
        lines.append(f"{cat.replace('_', ' ').title()}: {level} with {audience_str}")

    return f"{_personalized_prefix(actor)} Your sharing settings: " + ". ".join(lines) + "."


def set_my_sharing(category: str, level: str, audience: str = "all_enrolled") -> str:
    """Update sharing for a category. level: off/read/read_write. audience: all_enrolled or comma-separated user list."""
    from mcp_server.sharing import set_sharing
    actor = _user()

    # Parse audience
    if audience and audience != "all_enrolled":
        audience_list = [u.strip().lower() for u in audience.split(",") if u.strip()]
        aud = audience_list
    else:
        aud = "all_enrolled"

    success, msg = set_sharing(actor, category, level, aud)
    if not success:
        return msg
    return f"Updated: your {category.replace('_', ' ')} is now set to {level}."
