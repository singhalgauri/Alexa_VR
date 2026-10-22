"""
Intent Classifier & Routine Resolver.
Maps an utterance to (category, action, tier, personal_data flag, target_user_id).
Resolves composite routines to the strictest underlying action tier.
Designed as a modular, swappable component (rule-based with regex, easily extensible to LLMs).
"""

import re
from typing import Dict, List, Optional, Tuple

from .models import IntentClassification, SensitivityTier
from .policy_store import CATEGORY_TIER_MAPPING, get_canonical_category

# Predefined routine definitions for testing and standard use
PREDEFINED_ROUTINES: Dict[str, List[str]] = {
    "morning_routine": [
        "turn on kitchen lights",     # Tier 2 (smart_home)
        "tell me the news",           # Tier 1 (general_info)
    ],
    "party_routine": [
        "play jazz music",            # Tier 1 (music_entertainment)
        "dim the living room lights", # Tier 2 (smart_home)
    ],
    "bedtime_routine": [
        "turn off the lights",        # Tier 2 (smart_home)
        "lock the front door",        # Tier 3 (home_security)
        "read my calendar",           # Tier 3/Personal Data
    ],
    "secure_home_routine": [
        "arm the security alarm",     # Tier 3 (home_security)
        "close the garage door",      # Tier 3 (home_security)
    ],
}


class IntentClassifier:
    """
    Classifies natural language utterances into structured IntentClassification objects.
    Enforces strict routine inheritance (strictest tier wins) and personal data tagging.
    """

    def __init__(self, routines_registry: Optional[Dict[str, List[str]]] = None):
        self.routines = dict(routines_registry or PREDEFINED_ROUTINES)

    def classify(self, utterance: str) -> IntentClassification:
        """
        Classifies an utterance.
        """
        text = utterance.strip().lower()

        # 1. Tier 0: Emergency (Highest priority check, never gated)
        if self._is_emergency(text):
            return IntentClassification(
                category="emergency",
                action="call_emergency_services",
                tier=SensitivityTier.TIER_0_EMERGENCY,
                is_personal_data=False,
                details={"raw_utterance": utterance},
            )

        # 2. Permission Management (Tier 4)
        perm_intent = self._check_permission_management(text)
        if perm_intent:
            return perm_intent

        # 3. Routines check: Must resolve composite actions and inherit strictest tier
        routine_intent = self._check_routine(text)
        if routine_intent:
            return routine_intent

        # 4. Personal Data check across any domain
        personal_intent = self._check_personal_data(text)
        if personal_intent:
            return personal_intent

        # 5. Third-party skills
        skill_intent = self._check_third_party_skills(text)
        if skill_intent:
            return skill_intent

        # 6. Specific categories checks
        # Tier 4 checks
        t4_intent = self._check_tier_4(text)
        if t4_intent:
            return t4_intent

        # Tier 3 checks
        t3_intent = self._check_tier_3(text)
        if t3_intent:
            return t3_intent

        # Tier 2 checks
        t2_intent = self._check_tier_2(text)
        if t2_intent:
            return t2_intent

        # Tier 1 checks
        t1_intent = self._check_tier_1(text)
        if t1_intent:
            return t1_intent

        # Default fallback: Treat unclassified queries as general info (Tier 1)
        return IntentClassification(
            category="general_info",
            action="general_query",
            tier=SensitivityTier.TIER_1_GUEST_ALLOWED,
            is_personal_data=False,
            details={"raw_utterance": utterance},
        )

    # --- Matchers ---

    def _is_emergency(self, text: str) -> bool:
        patterns = [
            r"\bcall emergency\b",
            r"\bcall emergency services\b",
            r"\bcall 911\b",
            r"\bi need help\b",
            r"\bcall an? ambulance\b",
            r"\bcall the police\b",
            r"\bcall the fire department\b",
            r"\bemergency\b",
        ]
        return any(re.search(pat, text) for pat in patterns)

    def _check_permission_management(self, text: str) -> Optional[IntentClassification]:
        # "Allow guests to use smart home control", "Stop guests from using music",
        # "What can guests do right now?", "Reset guest permissions to default"
        if re.search(r"\b(what can guests do|list guest permissions|guest permissions right now)\b", text):
            return IntentClassification(
                category="guest_permission_management",
                action="get_permissions",
                tier=SensitivityTier.TIER_4_RESTRICTED_ADMIN,
                is_personal_data=False,
            )
        if re.search(r"\breset guest permissions\b", text):
            return IntentClassification(
                category="guest_permission_management",
                action="reset_permissions",
                tier=SensitivityTier.TIER_4_RESTRICTED_ADMIN,
                is_personal_data=False,
            )
        m_allow = re.search(r"\b(allow|enable|grant|let)\s+guests?\s+(?:to\s+use\s+|access\s+)?([a-z\s]+)", text)
        if m_allow:
            target_cat = m_allow.group(2).strip()
            return IntentClassification(
                category="guest_permission_management",
                action="set_permission",
                tier=SensitivityTier.TIER_4_RESTRICTED_ADMIN,
                is_personal_data=False,
                details={"target_category": target_cat, "enabled": True},
            )
        m_disallow = re.search(r"\b(stop|disable|block|prevent)\s+guests?\s+(?:from\s+using\s+|accessing\s+)?([a-z\s]+)", text)
        if m_disallow:
            target_cat = m_disallow.group(2).strip()
            return IntentClassification(
                category="guest_permission_management",
                action="set_permission",
                tier=SensitivityTier.TIER_4_RESTRICTED_ADMIN,
                is_personal_data=False,
                details={"target_category": target_cat, "enabled": False},
            )
        return None

    def _check_routine(self, text: str) -> Optional[IntentClassification]:
        m = re.search(r"\b(start|run|trigger|execute)\s+(the\s+)?([a-z_]+)\s+routine\b", text)
        routine_key = None
        if m:
            name = m.group(3).strip().replace(" ", "_")
            if f"{name}_routine" in self.routines:
                routine_key = f"{name}_routine"
            elif name in self.routines:
                routine_key = name

        if not routine_key:
            # Check direct keywords e.g. "bedtime routine"
            for k in self.routines:
                readable = k.replace("_", " ")
                if readable in text:
                    routine_key = k
                    break

        if routine_key:
            sub_actions = self.routines[routine_key]
            classified_subs = [self.classify(sub_text) for sub_text in sub_actions]

            # Inherent strictest tier
            strictest_tier = max((sub.tier for sub in classified_subs), default=SensitivityTier.TIER_3_ENROLLED_ONLY)
            # Inherent personal data if any sub-action accesses personal data
            has_personal = any(sub.is_personal_data for sub in classified_subs)

            return IntentClassification(
                category="routines",
                action=f"execute_{routine_key}",
                tier=strictest_tier,
                is_personal_data=has_personal,
                underlying_actions=classified_subs,
                details={"routine_name": routine_key, "steps": sub_actions},
            )
        return None

    def _check_personal_data(self, text: str) -> Optional[IntentClassification]:
        """
        Detects queries targeting personal data:
        Calendar/schedule, to-do, shopping list, personal reminders/alarms,
        notes, email, contacts, messages, purchase history, health/medication,
        location history. Also extracts targeted user (e.g. "John's schedule").
        """
        personal_patterns = [
            (r"\b(calendar|schedule|agenda|appointments?)\b", "calendar"),
            (r"\b(to-?do list|tasks?|my tasks)\b", "todo_list"),
            (r"\b(shopping list|grocery list)\b", "shopping_list"),
            (r"\b(personal reminder|my reminders?|remind me to)\b", "reminders"),
            (r"\b(my alarm|my alarms|set an alarm for me)\b", "personal_alarms"),
            (r"\b(notes?|my notes|read note)\b", "notes"),
            (r"\b(emails?|inbox|read email)\b", "email"),
            (r"\b(contacts?|address book|phone book)\b", "contacts"),
            (r"\b(messages?|texts?|read messages)\b", "messages"),
            (r"\b(purchase history|what did i buy|my orders)\b", "purchase_history"),
            (r"\b(health data|medication reminders?|my pills|prescriptions?)\b", "health_medication"),
            (r"\b(my audiobook|kindle library|my library)\b", "personal_library"),
            (r"\b(location history|where have i been)\b", "location_history"),
        ]

        for pat, data_type in personal_patterns:
            if re.search(pat, text):
                target_user = self._extract_target_user(text)
                return IntentClassification(
                    category="personal_data",
                    action=f"access_{data_type}",
                    tier=SensitivityTier.TIER_3_ENROLLED_ONLY,
                    is_personal_data=True,
                    target_user_id=target_user,
                    details={"data_type": data_type},
                )
        return None

    def _extract_target_user(self, text: str) -> Optional[str]:
        """Extracts named target user e.g. "What's on John's schedule?" -> "john"."""
        m = re.search(r"\b([a-z]+)'s\s+(schedule|calendar|email|notes|reminders|alarms|orders|location|messages)\b", text)
        if m:
            candidate = m.group(1).lower()
            if candidate not in {"my", "the", "today", "tomorrow"}:
                return candidate
        return None

    def _check_third_party_skills(self, text: str) -> Optional[IntentClassification]:
        m = re.search(r"\b(open|play|launch|start)\s+(trivia time|daily quote|calm meditation|the game)\b", text)
        if m:
            skill_name = m.group(2).replace(" ", "_")
            return IntentClassification(
                category="third_party_skills",
                action=f"launch_skill_{skill_name}",
                tier=SensitivityTier.TIER_2_GUEST_BLOCKED,
                is_personal_data=False,
                details={"skill_name": skill_name},
            )
        return None

    def _check_tier_4(self, text: str) -> Optional[IntentClassification]:
        # Payments & Banking
        if re.search(r"\b(pay\b.*\bbill|transfer money|bank balance|send \$|pay \d+|wire money)\b", text):
            return IntentClassification(
                category="payments_banking",
                action="execute_payment",
                tier=SensitivityTier.TIER_4_RESTRICTED_ADMIN,
                is_personal_data=False,
            )
        # Food & Ride Ordering
        if re.search(r"\b(order (uber|lyft|ride|pizza|food|groceries|dinner))\b", text):
            return IntentClassification(
                category="food_ride_ordering",
                action="order_food_or_ride",
                tier=SensitivityTier.TIER_4_RESTRICTED_ADMIN,
                is_personal_data=False,
            )
        # Device & Account Settings
        if re.search(r"\b(wifi password|bluetooth pairing|pair bluetooth|link account|enroll voice|delete voice profile)\b", text):
            return IntentClassification(
                category="device_account_settings",
                action="configure_device",
                tier=SensitivityTier.TIER_4_RESTRICTED_ADMIN,
                is_personal_data=False,
            )
        return None

    def _check_tier_3(self, text: str) -> Optional[IntentClassification]:
        # Home Monitoring and Security (cameras, locks, garage, alarm system)
        if re.search(r"\b(unlock|lock)\s+(the\s+)?(door|front door|back door)\b", text):
            return IntentClassification(
                category="home_security",
                action="door_lock_control",
                tier=SensitivityTier.TIER_3_ENROLLED_ONLY,
                is_personal_data=False,
            )
        if re.search(r"\b(open|close)\s+(the\s+)?garage\b", text):
            return IntentClassification(
                category="home_security",
                action="garage_control",
                tier=SensitivityTier.TIER_3_ENROLLED_ONLY,
                is_personal_data=False,
            )
        if re.search(r"\b(arm|disarm)\s+(the\s+)?(alarm|security system)\b", text):
            return IntentClassification(
                category="home_security",
                action="alarm_system_control",
                tier=SensitivityTier.TIER_3_ENROLLED_ONLY,
                is_personal_data=False,
            )
        if re.search(r"\b(show|view)\s+(security\s+)?camera\b", text):
            return IntentClassification(
                category="home_security",
                action="view_camera",
                tier=SensitivityTier.TIER_3_ENROLLED_ONLY,
                is_personal_data=False,
            )
        # Communication and Intercom
        if re.search(r"\b(call|drop in on|announce to the house|send a message to)\b", text):
            return IntentClassification(
                category="communication",
                action="intercom_communication",
                tier=SensitivityTier.TIER_3_ENROLLED_ONLY,
                is_personal_data=False,
            )
        # Shopping Assistance
        if re.search(r"\b(add to cart|order status|track my package|browse amazon)\b", text):
            return IntentClassification(
                category="shopping",
                action="shopping_assistance",
                tier=SensitivityTier.TIER_3_ENROLLED_ONLY,
                is_personal_data=False,
            )
        # Kids and Family controls
        if re.search(r"\b(parental controls|child profile|kid filter)\b", text):
            return IntentClassification(
                category="kids_family",
                action="family_control",
                tier=SensitivityTier.TIER_3_ENROLLED_ONLY,
                is_personal_data=False,
            )
        return None

    def _check_tier_2(self, text: str) -> Optional[IntentClassification]:
        # Smart home control (lights, thermostat, plugs - NOT locks or garage doors!)
        if re.search(r"\b(turn on|turn off|dim|brighten|set)\s+(the\s+)?(lights?|lamp|plug|thermostat|living room|kitchen)\b", text):
            return IntentClassification(
                category="smart_home",
                action="smart_home_control",
                tier=SensitivityTier.TIER_2_GUEST_BLOCKED,
                is_personal_data=False,
            )
        # Video and TV Control
        if re.search(r"\b(turn on tv|pause tv|play on fire tv|cast to tv|open netflix)\b", text):
            return IntentClassification(
                category="video_tv",
                action="video_tv_control",
                tier=SensitivityTier.TIER_2_GUEST_BLOCKED,
                is_personal_data=False,
            )
        # Generic Timers (guest's own anonymous timers)
        if re.search(r"\b(set a timer|cancel timer|timer for \d+|how much time is left on my timer)\b", text):
            return IntentClassification(
                category="generic_timers",
                action="manage_timer",
                tier=SensitivityTier.TIER_2_GUEST_BLOCKED,
                is_personal_data=False,
            )
        return None

    def _check_tier_1(self, text: str) -> Optional[IntentClassification]:
        # Music and Entertainment
        if re.search(r"\b(play music|play song|play jazz|play podcast|play radio|ambient sound)\b", text):
            return IntentClassification(
                category="music_entertainment",
                action="play_audio",
                tier=SensitivityTier.TIER_1_GUEST_ALLOWED,
                is_personal_data=False,
            )
        # General Information
        if re.search(r"\b(weather|news|forecast|recipe|traffic|convert|how far|trivia|capital of)\b", text):
            return IntentClassification(
                category="general_info",
                action="general_query",
                tier=SensitivityTier.TIER_1_GUEST_ALLOWED,
                is_personal_data=False,
            )
        return None
