"""
Voice-Identity-Based Access Control Layer for Alexa-style Voice Assistants.
"""

from .gate import PolicyEnforcementGate, GUEST_REFUSAL_MESSAGE
from .models import (
    AccessDecision,
    AccessStatus,
    IntentClassification,
    Role,
    SensitivityTier,
    SpeakerIdentificationResult,
    AuditRecord,
)
from .policy_store import PolicyStore, CATEGORY_TIER_MAPPING, get_canonical_category
from .intent_classifier import IntentClassifier
from .speaker_wrapper import SpeakerIdentityModule
from .rate_limiter import RateLimiter
from .second_factor import SecondFactorVerifier
from .notifications import NotificationManager, SecurityNotification
from .audit import AccessAuditLogger
from .skills_mock import SkillDispatcher

__all__ = [
    "PolicyEnforcementGate",
    "GUEST_REFUSAL_MESSAGE",
    "AccessDecision",
    "AccessStatus",
    "IntentClassification",
    "Role",
    "SensitivityTier",
    "SpeakerIdentificationResult",
    "AuditRecord",
    "PolicyStore",
    "CATEGORY_TIER_MAPPING",
    "get_canonical_category",
    "IntentClassifier",
    "SpeakerIdentityModule",
    "RateLimiter",
    "SecondFactorVerifier",
    "NotificationManager",
    "SecurityNotification",
    "AccessAuditLogger",
    "SkillDispatcher",
]
