"""
Policy Store and Permission Manager.
Handles category tier mappings, guest permission configurations, allow-lists, and JSON persistence.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .models import SensitivityTier

logger = logging.getLogger(__name__)

# Category to Tier mapping according to project specification
CATEGORY_TIER_MAPPING: Dict[str, SensitivityTier] = {
    # Tier 0: Emergency (Always allowed)
    "emergency": SensitivityTier.TIER_0_EMERGENCY,

    # Tier 1: Guest-allowed by default (enrolled users may disable)
    "music_entertainment": SensitivityTier.TIER_1_GUEST_ALLOWED,
    "general_info": SensitivityTier.TIER_1_GUEST_ALLOWED,

    # Tier 2: Guest-blocked by default (enrolled users may enable)
    "smart_home": SensitivityTier.TIER_2_GUEST_BLOCKED,
    "video_tv": SensitivityTier.TIER_2_GUEST_BLOCKED,
    "generic_timers": SensitivityTier.TIER_2_GUEST_BLOCKED,
    "third_party_skills": SensitivityTier.TIER_2_GUEST_BLOCKED,

    # Tier 3: Enrolled-only by default (may grant guest access ONLY after confirmation/PIN with risk warning)
    "communication": SensitivityTier.TIER_3_ENROLLED_ONLY,
    "home_security": SensitivityTier.TIER_3_ENROLLED_ONLY,
    "shopping": SensitivityTier.TIER_3_ENROLLED_ONLY,
    "routines": SensitivityTier.TIER_3_ENROLLED_ONLY,
    "kids_family": SensitivityTier.TIER_3_ENROLLED_ONLY,

    # Tier 4: Enrolled-only, NEVER grantable to guests (requires high confidence + 2FA)
    "payments_banking": SensitivityTier.TIER_4_RESTRICTED_ADMIN,
    "food_ride_ordering": SensitivityTier.TIER_4_RESTRICTED_ADMIN,
    "device_account_settings": SensitivityTier.TIER_4_RESTRICTED_ADMIN,
    "guest_permission_management": SensitivityTier.TIER_4_RESTRICTED_ADMIN,

    # Personal Data marker category (always enrolled-only, per-user isolated)
    "personal_data": SensitivityTier.TIER_4_RESTRICTED_ADMIN,
}

# User-friendly alias map for voice commands
CATEGORY_ALIASES: Dict[str, str] = {
    "emergency": "emergency",
    "emergency services": "emergency",
    "music": "music_entertainment",
    "entertainment": "music_entertainment",
    "music and entertainment": "music_entertainment",
    "general info": "general_info",
    "general information": "general_info",
    "weather": "general_info",
    "news": "general_info",
    "smart home": "smart_home",
    "smart home control": "smart_home",
    "lights": "smart_home",
    "video": "video_tv",
    "tv": "video_tv",
    "video and tv": "video_tv",
    "video and tv control": "video_tv",
    "timers": "generic_timers",
    "generic timers": "generic_timers",
    "third party skills": "third_party_skills",
    "skills": "third_party_skills",
    "communication": "communication",
    "intercom": "communication",
    "communication and intercom": "communication",
    "security": "home_security",
    "home security": "home_security",
    "home monitoring": "home_security",
    "home monitoring and security": "home_security",
    "locks": "home_security",
    "shopping": "shopping",
    "shopping assistance": "shopping",
    "routines": "routines",
    "routines and automations": "routines",
    "kids": "kids_family",
    "family": "kids_family",
    "kids and family": "kids_family",
    "kids and family controls": "kids_family",
    "payments": "payments_banking",
    "banking": "payments_banking",
    "payments and banking": "payments_banking",
    "food ordering": "food_ride_ordering",
    "ride ordering": "food_ride_ordering",
    "food and ride ordering": "food_ride_ordering",
    "settings": "device_account_settings",
    "device settings": "device_account_settings",
    "device and account settings": "device_account_settings",
    "guest permissions": "guest_permission_management",
    "permission management": "guest_permission_management",
    "guest permission management": "guest_permission_management",
}


def get_canonical_category(name: str) -> Optional[str]:
    """Resolves an utterance or alias to the canonical category key."""
    cleaned = name.strip().lower()
    if cleaned in CATEGORY_TIER_MAPPING:
        return cleaned
    return CATEGORY_ALIASES.get(cleaned)


class PolicyStore:
    """
    Manages persistent access control policies, guest enablement toggles,
    allow-lists, confidence thresholds, and user PINs.
    """

    def __init__(self, storage_path: Optional[str] = None):
        self.storage_path = Path(storage_path) if storage_path else Path("access_control_policy.json")
        self._guest_permissions: Dict[str, bool] = {}
        self._third_party_allowlist: Set[str] = set()
        self._user_pins: Dict[str, str] = {}  # user_id -> PIN (e.g. "1234")
        self._enrolled_users: Set[str] = set()

        # Configurable Confidence Thresholds
        self.threshold_normal: float = 0.40       # Tier 1-2 for enrolled users
        self.threshold_high: float = 0.65         # Tier 3
        self.threshold_very_high: float = 0.80    # Tier 4

        # Rate Limiting & Lockout configs
        self.max_failed_attempts: int = 3
        self.lockout_duration_seconds: int = 300  # 5 minutes

        self._load_or_initialize()

    def _get_default_guest_permissions(self) -> Dict[str, bool]:
        """Calculates standard default permissions per tier specification."""
        defaults = {}
        for category, tier in CATEGORY_TIER_MAPPING.items():
            if tier == SensitivityTier.TIER_0_EMERGENCY:
                defaults[category] = True
            elif tier == SensitivityTier.TIER_1_GUEST_ALLOWED:
                defaults[category] = True
            elif tier == SensitivityTier.TIER_2_GUEST_BLOCKED:
                defaults[category] = False
            elif tier == SensitivityTier.TIER_3_ENROLLED_ONLY:
                defaults[category] = False
            elif tier == SensitivityTier.TIER_4_RESTRICTED_ADMIN:
                defaults[category] = False
        return defaults

    def _load_or_initialize(self) -> None:
        """Loads state from JSON storage or initializes defaults."""
        if self.storage_path.exists():
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._guest_permissions = data.get("guest_permissions", self._get_default_guest_permissions())
                    self._third_party_allowlist = set(data.get("third_party_allowlist", ["trivia_time", "daily_quote"]))
                    self._user_pins = data.get("user_pins", {"gauravi": "1234", "john": "4321", "alice": "9999"})
                    self._enrolled_users = set(data.get("enrolled_users", ["gauravi", "john", "alice"]))
                    self.threshold_normal = data.get("threshold_normal", 0.40)
                    self.threshold_high = data.get("threshold_high", 0.65)
                    self.threshold_very_high = data.get("threshold_very_high", 0.80)
                    # Enforce immutable rules on load
                    self._enforce_immutable_rules()
                    return
            except Exception as e:
                logger.error(f"Error loading policy file {self.storage_path}: {e}. Falling back to defaults.")

        # Fallback to pristine defaults
        self.reset_to_defaults()

    def _enforce_immutable_rules(self) -> None:
        """Ensures Tier 0 is always enabled and Tier 4 is always disabled for guests."""
        for category, tier in CATEGORY_TIER_MAPPING.items():
            if tier == SensitivityTier.TIER_0_EMERGENCY:
                self._guest_permissions[category] = True
            elif tier == SensitivityTier.TIER_4_RESTRICTED_ADMIN:
                self._guest_permissions[category] = False

    def reset_to_defaults(self) -> None:
        """Resets all guest permissions and configurations to standard defaults."""
        self._guest_permissions = self._get_default_guest_permissions()
        self._third_party_allowlist = {"trivia_time", "daily_quote", "calm_meditation"}
        self._user_pins = {"gauravi": "1234", "john": "4321", "alice": "9999"}
        self._enrolled_users = {"gauravi", "john", "alice"}
        self.threshold_normal = 0.40
        self.threshold_high = 0.65
        self.threshold_very_high = 0.80
        self.save()

    def save(self) -> None:
        """Persists current state to JSON storage."""
        self._enforce_immutable_rules()
        data = {
            "guest_permissions": self._guest_permissions,
            "third_party_allowlist": list(self._third_party_allowlist),
            "user_pins": self._user_pins,
            "enrolled_users": list(self._enrolled_users),
            "threshold_normal": self.threshold_normal,
            "threshold_high": self.threshold_high,
            "threshold_very_high": self.threshold_very_high,
        }
        try:
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save policy file {self.storage_path}: {e}")

    # --- Query Methods ---

    def is_guest_allowed(self, category: str) -> bool:
        """Checks whether a category is currently accessible to guests."""
        canonical = get_canonical_category(category) or category
        tier = CATEGORY_TIER_MAPPING.get(canonical, SensitivityTier.TIER_4_RESTRICTED_ADMIN)
        if tier == SensitivityTier.TIER_0_EMERGENCY:
            return True
        if tier == SensitivityTier.TIER_4_RESTRICTED_ADMIN:
            return False
        return self._guest_permissions.get(canonical, False)

    def get_category_tier(self, category: str) -> SensitivityTier:
        canonical = get_canonical_category(category) or category
        return CATEGORY_TIER_MAPPING.get(canonical, SensitivityTier.TIER_4_RESTRICTED_ADMIN)

    def is_enrolled_user(self, user_id: Optional[str]) -> bool:
        if not user_id:
            return False
        return user_id in self._enrolled_users

    def verify_user_pin(self, user_id: str, pin: str) -> bool:
        stored = self._user_pins.get(user_id)
        if not stored:
            return False
        return str(stored).strip() == str(pin).strip()

    def is_third_party_skill_allowed(self, skill_name: str) -> bool:
        return skill_name.strip().lower() in {s.lower() for s in self._third_party_allowlist}

    def get_guest_permissions_summary(self) -> Dict[str, bool]:
        """Returns a snapshot of current guest permissions across all categories."""
        self._enforce_immutable_rules()
        return dict(self._guest_permissions)

    # --- Mutation Methods ---

    def set_guest_permission(
        self,
        category_name: str,
        enabled: bool,
        confirmed: bool = False,
    ) -> Tuple[bool, str]:
        """
        Updates guest access for a category with tier validation.
        
        Returns:
            (success: bool, verbal_message: str)
        """
        canonical = get_canonical_category(category_name)
        if not canonical:
            return False, f"I didn't recognize the category '{category_name}'."

        tier = CATEGORY_TIER_MAPPING[canonical]

        # Tier 0 check: Cannot be disabled
        if tier == SensitivityTier.TIER_0_EMERGENCY:
            if not enabled:
                return False, "Emergency services are always allowed for everyone and cannot be disabled."
            return True, "Emergency services are already enabled for all guests."

        # Tier 4 check: NEVER grantable to guests
        if tier == SensitivityTier.TIER_4_RESTRICTED_ADMIN:
            if enabled:
                return False, (
                    f"That category belongs to restricted system settings. "
                    f"It cannot be granted to guests under any circumstances."
                )
            return True, f"Guests are already blocked from {canonical}."

        # Tier 3 check: Requires confirmation + risk warning
        if tier == SensitivityTier.TIER_3_ENROLLED_ONLY and enabled and not confirmed:
            return False, (
                f"Granting guest access to {canonical.replace('_', ' ')} carries security risks. "
                f"Please confirm with your voice PIN to proceed."
            )

        # Apply update (Tier 1, Tier 2, or confirmed Tier 3)
        self._guest_permissions[canonical] = enabled
        self.save()

        action_word = "now use" if enabled else "no longer use"
        display_name = "Smart Home Control" if canonical == "smart_home" else canonical.replace("_", " ").title()
        return True, f"Okay, guests can {action_word} {display_name}."

    def add_allowed_skill(self, skill_name: str) -> None:
        self._third_party_allowlist.add(skill_name.strip().lower())
        self.save()

    def remove_allowed_skill(self, skill_name: str) -> None:
        self._third_party_allowlist.discard(skill_name.strip().lower())
        self.save()
