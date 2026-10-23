"""
mcp_server/sharing.py – Cross-user data sharing policy and authorize() choke point.

Policy model:
    sharing_policies[owner][category] = {
        "level": "off" | "read" | "read_write",
        "audience": "all_enrolled" | ["user1", "user2"]
    }

Default: OFF for every category.  Tier 0 (Emergency) is not personal data and
stays open.  Tier 4 (Admin/Payments) is NEVER shareable.

authorize(actor, owner, category, write=False) -> (allowed: bool, reason: str)
    - allowed if actor == owner
    - allowed if owner has shared that category with actor at the needed level
    - denied otherwise
    - ALWAYS produces an audit entry for cross-user attempts

The audit log is append-only JSONL at data/_sharing_audit.jsonl (gitignored).
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DATA_DIR = Path(os.environ.get("VOICEGATE_USER_DATA_DIR", "data"))

_lock = threading.Lock()

# Categories that can NEVER be shared (Tier 4)
UNSHAREABLE_CATEGORIES = frozenset({
    "payments_banking",
    "food_ride_ordering",
    "device_account_settings",
    "guest_permission_management",
    "personal_data",  # generic personal data marker
})

# Category -> tier mapping (will be filled lazily from access_control)
_TIER_CACHE: dict[str, int] = {}


def _get_tier(category: str) -> int:
    """Get the tier for a category.  Returns 99 for unknown."""
    if not _TIER_CACHE:
        try:
            from access_control.policy_store import CATEGORY_TIER_MAPPING
            for cat, tier in CATEGORY_TIER_MAPPING.items():
                _TIER_CACHE[cat] = int(tier)
        except Exception:
            pass
    return _TIER_CACHE.get(category, 99)


# ---------------------------------------------------------------------------
# Sharing policies persistence
# ---------------------------------------------------------------------------

def _policies_path() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / "_sharing_policies.json"


def _load_policies() -> dict[str, dict[str, dict[str, Any]]]:
    p = _policies_path()
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_policies(policies: dict) -> None:
    p = _policies_path()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(policies, f, indent=2)


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

_audit_path = DATA_DIR / "_sharing_audit.jsonl"
_audit_ring: list[dict] = []
_MAX_RING = 200


def _audit_entry(
    *,
    actor: str,
    owner: str,
    category: str,
    action: str,
    allowed: bool,
    reason: str,
    write: bool = False,
) -> dict:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "actor": actor,
        "owner": owner,
        "category": category,
        "action": action,
        "write": write,
        "allowed": allowed,
        "reason": reason,
    }
    _audit_ring.append(entry)
    if len(_audit_ring) > _MAX_RING:
        _audit_ring.pop(0)
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(_audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass
    return entry


def recent_sharing_audit(n: int = 20) -> list[dict]:
    """Return the last n sharing audit entries (newest last)."""
    return list(_audit_ring[-n:])


# ---------------------------------------------------------------------------
# Core: authorize()
# ---------------------------------------------------------------------------

def authorize(
    actor: str,
    owner: str,
    category: str,
    write: bool = False,
) -> tuple[bool, str]:
    """
    Single choke point for cross-user data access.

    Returns (allowed, reason).
    Always logs an audit entry for cross-user attempts.
    """
    # Self-access is always allowed
    if actor.lower() == owner.lower():
        return True, "Self-access"

    # Tier 0: Emergency – not personal data, open
    if _get_tier(category) == 0:
        _audit_entry(
            actor=actor, owner=owner, category=category,
            action="access", allowed=True, reason="Tier 0 Emergency: always open",
            write=write,
        )
        return True, "Tier 0 Emergency: always open"

    # Tier 4 and unshareable: NEVER shared
    if category in UNSHAREABLE_CATEGORIES or _get_tier(category) >= 4:
        _audit_entry(
            actor=actor, owner=owner, category=category,
            action="access", allowed=False,
            reason=f"Category '{category}' cannot be shared between users",
            write=write,
        )
        return False, f"Category '{category}' cannot be shared between users"

    # Check owner's sharing policy
    with _lock:
        policies = _load_policies()

    owner_policies = policies.get(owner.lower(), {})
    cat_policy = owner_policies.get(category)

    if not cat_policy or cat_policy.get("level", "off") == "off":
        _audit_entry(
            actor=actor, owner=owner, category=category,
            action="access", allowed=False,
            reason=f"{owner} has not shared {category}",
            write=write,
        )
        return False, f"{owner} has not shared {category}"

    level = cat_policy["level"]  # "read" or "read_write"
    audience = cat_policy.get("audience", "all_enrolled")

    # Check if actor is in the audience
    actor_allowed = False
    if audience == "all_enrolled":
        actor_allowed = True
    elif isinstance(audience, list):
        actor_allowed = actor.lower() in [u.lower() for u in audience]

    if not actor_allowed:
        _audit_entry(
            actor=actor, owner=owner, category=category,
            action="access", allowed=False,
            reason=f"{owner} has not shared {category} with {actor}",
            write=write,
        )
        return False, f"{owner} has not shared {category} with {actor}"

    # Check write permission
    if write and level != "read_write":
        _audit_entry(
            actor=actor, owner=owner, category=category,
            action="write", allowed=False,
            reason=f"{owner} shared {category} as read-only",
            write=True,
        )
        return False, f"{owner} shared {category} as read-only"

    # Allowed
    action_type = "write" if write else "read"
    _audit_entry(
        actor=actor, owner=owner, category=category,
        action=action_type, allowed=True,
        reason=f"{owner} shared {category} ({level}) with {actor}",
        write=write,
    )
    return True, f"Access granted: {owner} shared {category} ({level})"


# ---------------------------------------------------------------------------
# Sharing policy management
# ---------------------------------------------------------------------------

def get_sharing_policy(owner: str) -> dict[str, dict[str, Any]]:
    """Return the owner's full sharing policy."""
    with _lock:
        policies = _load_policies()
    return policies.get(owner.lower(), {})


def set_sharing(
    owner: str,
    category: str,
    level: str = "off",
    audience: str | list[str] = "all_enrolled",
) -> tuple[bool, str]:
    """
    Set sharing policy for owner/category.
    level: "off" | "read" | "read_write"
    audience: "all_enrolled" | list of user IDs
    """
    # Validate level
    if level not in ("off", "read", "read_write"):
        return False, f"Invalid sharing level: {level}"

    # Tier 4 categories cannot be shared
    if category in UNSHAREABLE_CATEGORIES or _get_tier(category) >= 4:
        return False, f"Category '{category}' cannot be shared between users"

    with _lock:
        policies = _load_policies()
        owner_key = owner.lower()
        if owner_key not in policies:
            policies[owner_key] = {}

        if level == "off":
            policies[owner_key].pop(category, None)
        else:
            policies[owner_key][category] = {
                "level": level,
                "audience": audience,
            }
        _save_policies(policies)

    return True, f"Sharing for {category} set to {level}"


def revoke_sharing(owner: str, category: str) -> tuple[bool, str]:
    """Revoke sharing for a category (set to off)."""
    return set_sharing(owner, category, "off")


def get_all_sharing_policies() -> dict:
    """Return all sharing policies (for admin/debug)."""
    with _lock:
        return _load_policies()
