"""
app/profiles.py – Per-user profile store.

Profiles are persisted as voiceprints/<name>.profile.json next to each user's
voiceprint.  Contains:
  {
    "name":        str,           # enrolled key / username
    "display_name": str,          # friendly display name
    "avatar_color": str,          # hex color for avatar
    "preferences": {
        "wake_greeting": str,     # custom greeting ("Hey Alice!")
        "preferred_units": str,   # "metric" | "imperial"
        "timezone": str           # IANA tz, e.g. "Asia/Kolkata"
    },
    "schedule": [                 # list of schedule items
        { "time": "09:00", "title": "Stand-up", "days": ["Mon","Tue","Wed","Thu","Fri"] },
        ...
    ]
  }

Raw audio is never stored here.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

VOICEPRINTS_DIR: Path = Path(
    os.environ.get("VOICEGATE_DATA", os.environ.get("VOICEPRINTS_DIR", "voiceprints"))
)

_AVATAR_PALETTE = [
    "#6366f1", "#22d3ee", "#10b981", "#f59e0b",
    "#f43f5e", "#a855f7", "#ec4899", "#14b8a6",
    "#84cc16", "#fb923c",
]


def _profile_path(name: str) -> Path:
    VOICEPRINTS_DIR.mkdir(parents=True, exist_ok=True)
    return VOICEPRINTS_DIR / f"{name}.profile.json"


def _default_profile(name: str) -> dict[str, Any]:
    """Build a sensible default profile for *name*."""
    colour = _AVATAR_PALETTE[hash(name) % len(_AVATAR_PALETTE)]
    return {
        "name": name,
        "display_name": name.replace("_", " ").title(),
        "avatar_color": colour,
        "preferences": {
            "wake_greeting": f"Welcome back, {name.replace('_', ' ').title()}!",
            "preferred_units": "metric",
            "timezone": "UTC",
        },
        "schedule": [],
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_profile(name: str) -> dict[str, Any]:
    """Return the profile for *name*, creating a default if none exists."""
    p = _profile_path(name)
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Back-fill missing keys from default
            default = _default_profile(name)
            for k, v in default.items():
                data.setdefault(k, v)
            if "preferences" in default:
                for k, v in default["preferences"].items():
                    data["preferences"].setdefault(k, v)
            return data
        except Exception:
            pass
    return _default_profile(name)


def save_profile(name: str, profile: dict[str, Any]) -> None:
    """Persist *profile* to disk."""
    profile["name"] = name  # never let the payload override the key
    p = _profile_path(name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)


def update_profile(name: str, updates: dict[str, Any]) -> dict[str, Any]:
    """Merge *updates* into the existing profile and save."""
    profile = get_profile(name)
    for k, v in updates.items():
        if k == "preferences" and isinstance(v, dict):
            profile["preferences"].update(v)
        elif k == "schedule":
            profile["schedule"] = v  # full replace
        elif k != "name":            # name is immutable
            profile[k] = v
    save_profile(name, profile)
    return profile


def list_profiles() -> list[str]:
    """Return names of users that have a saved profile."""
    if not VOICEPRINTS_DIR.exists():
        return []
    return sorted(p.stem for p in VOICEPRINTS_DIR.glob("*.profile.json"))


def delete_profile(name: str) -> bool:
    """Delete the profile for *name*. Returns True if it existed."""
    p = _profile_path(name)
    if p.exists():
        p.unlink()
        return True
    return False
