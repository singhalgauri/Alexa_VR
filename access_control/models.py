"""
Data models and enumerations for the Voice-Identity-Based Access Control Layer.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, IntEnum
from typing import Any, Dict, List, Optional


class SensitivityTier(IntEnum):
    TIER_0_EMERGENCY = 0           # Emergency: always allowed, never gated
    TIER_1_GUEST_ALLOWED = 1        # Music, General Info: guest allowed by default
    TIER_2_GUEST_BLOCKED = 2        # Smart Home, Video/TV, Timers: guest blocked by default
    TIER_3_ENROLLED_ONLY = 3        # Comm, Security, Shopping, Routines: enrolled only by default
    TIER_4_RESTRICTED_ADMIN = 4     # Payments, Device Settings, Permissions: enrolled-only, never grantable to guest


class Role(str, Enum):
    ENROLLED = "enrolled"
    FAMILY = "family"               # Optional child / family member profile
    GUEST = "guest"


class AccessStatus(str, Enum):
    ALLOWED = "ALLOWED"
    DENIED = "DENIED"
    CHALLENGE_PIN = "CHALLENGE_PIN"
    CHALLENGE_CONFIRM = "CHALLENGE_CONFIRM"
    LOCKED_OUT = "LOCKED_OUT"


@dataclass
class SpeakerIdentificationResult:
    """
    Output from speaker recognition and liveness analysis.
    """
    user_id: Optional[str]                      # None if guest
    confidence: float = 0.0                     # 0.0 to 1.0
    detected_speakers: List[str] = field(default_factory=list)  # In multi-speaker audio
    is_ambiguous: bool = False                  # True if multiple close centroid matches
    is_live: bool = True                        # Liveness / anti-spoof check flag


@dataclass
class IntentClassification:
    """
    Classified user intent, action, and sensitivity metadata.
    """
    category: str                               # e.g., "emergency", "smart_home", "personal_data"
    action: str                                 # e.g., "call_911", "turn_on_lights", "read_calendar"
    tier: SensitivityTier                       # Inferred sensitivity tier
    is_personal_data: bool = False              # True if personal data is accessed/queried
    target_user_id: Optional[str] = None        # User whose personal data is requested
    details: Dict[str, Any] = field(default_factory=dict)
    underlying_actions: List["IntentClassification"] = field(default_factory=list) # For routines


@dataclass
class AccessDecision:
    """
    Enforcement outcome returned by the Policy Enforcement Gate.
    """
    status: AccessStatus
    allowed: bool
    speaker_id: Optional[str]
    effective_role: Role
    category: str
    tier: SensitivityTier
    reason: str
    response_message: str
    requires_second_factor: bool = False
    requires_confirmation: bool = False
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AuditRecord:
    """
    Audit log entry adhering strictly to privacy requirements (no audio, no PINs).
    """
    timestamp: str
    speaker_id: Optional[str]
    effective_role: str
    category: str
    action: str
    tier: int
    allowed: bool
    status: str
    reason: str
    confidence: float
