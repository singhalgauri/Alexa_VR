"""
Speaker Identity Module.
Wraps the black-box speaker recognition model:
    identify_speaker(audio) -> { "user_id": str | None, "confidence": float }
Includes multi-speaker resolution, ambiguity checking, anti-spoofing/liveness hook,
and threshold evaluations.
"""

import logging
from typing import Any, Callable, Dict, List, Optional

from .models import Role, SensitivityTier, SpeakerIdentificationResult

logger = logging.getLogger(__name__)


def default_liveness_check(audio: Any) -> bool:
    """
    Hook for liveness and anti-spoofing checks (replay, synthetic voice, recorded-voice attacks).
    In this reference architecture, audio passes unless flagged by an external detector.
    
    Known Limitation:
    Without specialized acoustic replay detection (e.g. ASVspoof models) or ultrasonic
    challenge-response, recorded voices from authorized users can mimic legitimate speakers.
    High-risk actions (Tier 4) mitigate this by strictly requiring a second factor (voice PIN).
    """
    return True


class SpeakerIdentityModule:
    """
    Adapter around the speaker recognition model enforcing security policies:
    - Fails closed on low confidence, ambiguity, or spoofing.
    - Resolves multi-speaker audio by falling back to the lowest privilege (GUEST).
    - Maps child/family audio to the optional FAMILY role if specified.
    """

    def __init__(
        self,
        model_fn: Optional[Callable[[Any], Dict[str, Any]]] = None,
        liveness_fn: Optional[Callable[[Any], bool]] = None,
    ):
        self._model_fn = model_fn
        self._liveness_fn = liveness_fn or default_liveness_check

    def process_audio(
        self,
        audio: Any = None,
        simulated_result: Optional[SpeakerIdentificationResult] = None,
    ) -> SpeakerIdentificationResult:
        """
        Processes audio input through speaker recognition and security filters.
        Allows passing simulated_result directly for automated testing / CLI simulation.
        """
        if simulated_result is not None:
            result = simulated_result
        elif self._model_fn is not None:
            raw = self._model_fn(audio)
            result = SpeakerIdentificationResult(
                user_id=raw.get("user_id"),
                confidence=float(raw.get("confidence", 0.0)),
                detected_speakers=raw.get("detected_speakers", []),
                is_ambiguous=bool(raw.get("is_ambiguous", False)),
                is_live=self._liveness_fn(audio),
            )
        else:
            # Default fallback when no model is attached
            result = SpeakerIdentificationResult(user_id=None, confidence=0.0)

        # 1. Anti-spoofing / liveness check
        if not result.is_live:
            logger.warning("Liveness check failed! Demoting speaker to guest.")
            result.user_id = None

        return result

    def resolve_effective_role(
        self,
        speaker_result: SpeakerIdentificationResult,
        tier: SensitivityTier,
        threshold_normal: float = 0.40,
        threshold_high: float = 0.65,
        threshold_very_high: float = 0.80,
        family_members: Optional[List[str]] = None,
    ) -> Role:
        """
        Resolves the effective role based on detected speakers, ambiguity, and confidence thresholds.
        
        Security Rules:
        1. Multi-speaker audio: If more than one speaker is detected (e.g. enrolled + guest),
           always default to the lowest privilege present (GUEST).
        2. Ambiguity: If speaker match is ambiguous between multiple users, treat as GUEST.
        3. Confidence Thresholds:
           - Tier 1-2: Requires threshold_normal (>= 0.40).
           - Tier 3: Requires threshold_high (>= 0.65).
           - Tier 4: Requires threshold_very_high (>= 0.80).
           If confidence is below the required threshold for the tier, demote to GUEST.
        4. Emergency (Tier 0) ignores confidence drops at the gate level.
        """
        # If speaker is already guest
        if speaker_result.user_id is None:
            return Role.GUEST

        # Rule 1: Multi-speaker safety check
        if len(speaker_result.detected_speakers) > 1:
            # If any guest or conflicting speaker is in the audio, fallback to GUEST
            logger.info("Multi-speaker audio detected (%s). Falling back to lowest privilege (GUEST).",
                        speaker_result.detected_speakers)
            return Role.GUEST

        # Rule 2: Ambiguity check
        if speaker_result.is_ambiguous:
            logger.info("Ambiguous speaker match. Falling back to GUEST.")
            return Role.GUEST

        # Rule 3: Tier-specific confidence threshold checks
        req_threshold = threshold_normal
        if tier == SensitivityTier.TIER_3_ENROLLED_ONLY:
            req_threshold = threshold_high
        elif tier == SensitivityTier.TIER_4_RESTRICTED_ADMIN:
            req_threshold = threshold_very_high

        if speaker_result.confidence < req_threshold:
            logger.info(
                "Confidence %.2f below required threshold %.2f for Tier %s. Demoting to GUEST.",
                speaker_result.confidence, req_threshold, tier.name
            )
            return Role.GUEST

        # Check for optional FAMILY role
        if family_members and speaker_result.user_id in family_members:
            return Role.FAMILY

        return Role.ENROLLED
