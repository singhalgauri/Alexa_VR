"""
Policy Enforcement Gate.
The central mediator between voice recognition and assistant execution.
Coordinates speaker verification, intent classification, tier policy evaluation,
second-factor validation, rate limiting, audit logging, and skill dispatch.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional

from .audit import AccessAuditLogger
from .intent_classifier import IntentClassifier
from .models import (
    AccessDecision,
    AccessStatus,
    IntentClassification,
    Role,
    SensitivityTier,
    SpeakerIdentificationResult,
)
from .notifications import NotificationManager
from .policy_store import PolicyStore, get_canonical_category
from .rate_limiter import RateLimiter
from .second_factor import SecondFactorVerifier
from .skills_mock import SkillDispatcher

logger = logging.getLogger(__name__)

# Standard privacy-preserving refusal message
GUEST_REFUSAL_MESSAGE = "I can't help with that for guests."


class PolicyEnforcementGate:
    """
    Enforces privacy, security, and tier-based access controls across all voice interactions.
    """

    def __init__(
        self,
        policy_store: Optional[PolicyStore] = None,
        intent_classifier: Optional[IntentClassifier] = None,
        rate_limiter: Optional[RateLimiter] = None,
        audit_logger: Optional[AccessAuditLogger] = None,
        notification_manager: Optional[NotificationManager] = None,
        skill_dispatcher: Optional[SkillDispatcher] = None,
    ):
        self.policy_store = policy_store or PolicyStore()
        self.intent_classifier = intent_classifier or IntentClassifier()
        self.rate_limiter = rate_limiter or RateLimiter(
            max_failures=self.policy_store.max_failed_attempts,
            lockout_seconds=self.policy_store.lockout_duration_seconds,
        )
        self.audit_logger = audit_logger or AccessAuditLogger()
        self.notification_manager = notification_manager or NotificationManager()
        self.skill_dispatcher = skill_dispatcher or SkillDispatcher()
        self.second_factor_verifier = SecondFactorVerifier(self.policy_store)

    def process_request(
        self,
        utterance: str,
        speaker_result: SpeakerIdentificationResult,
        pin_attempt: Optional[str] = None,
        confirmation: bool = False,
    ) -> AccessDecision:
        """
        Main entry point for evaluating and executing a voice command.
        """
        timestamp = datetime.now(timezone.utc).isoformat()

        # 1. Classify the user's utterance
        intent = self.intent_classifier.classify(utterance)

        # 2. Tier 0: Emergency - Unconditional override (MUST NEVER BE GATED OR REFUSED)
        if intent.tier == SensitivityTier.TIER_0_EMERGENCY:
            skill_output = self.skill_dispatcher.dispatch(intent, speaker_result.user_id, "emergency")
            decision = AccessDecision(
                status=AccessStatus.ALLOWED,
                allowed=True,
                speaker_id=speaker_result.user_id,
                effective_role=Role.ENROLLED if speaker_result.user_id else Role.GUEST,
                category=intent.category,
                tier=SensitivityTier.TIER_0_EMERGENCY,
                reason="Tier 0 Emergency: Always allowed unconditionally without gating.",
                response_message=skill_output,
            )
            self._log_and_audit(timestamp, decision, intent, speaker_result.confidence)
            return decision

        # 3. Check for active security lockout
        lockout_key = speaker_result.user_id or "guest"
        is_locked, remaining_sec = self.rate_limiter.is_locked_out(lockout_key)
        if is_locked:
            decision = AccessDecision(
                status=AccessStatus.LOCKED_OUT,
                allowed=False,
                speaker_id=speaker_result.user_id,
                effective_role=Role.GUEST,
                category=intent.category,
                tier=intent.tier,
                reason=f"Account locked out due to repeated security failures. {remaining_sec}s remaining.",
                response_message="This device is temporarily locked for security. Please try again later.",
            )
            self._log_and_audit(timestamp, decision, intent, speaker_result.confidence)
            return decision

        # 4. Resolve the speaker's effective role (handling confidence thresholds, multi-speaker, and ambiguity)
        effective_role = self._resolve_role(speaker_result, intent.tier)

        # 5. Handle Personal Data Access (Strict Per-User Isolation)
        if intent.is_personal_data:
            decision = self._evaluate_personal_data(
                intent, speaker_result, effective_role, timestamp
            )
            self._log_and_audit(timestamp, decision, intent, speaker_result.confidence)
            return decision

        # 6. Handle Guest Permission Management (Tier 4 Administrative Task)
        if intent.category == "guest_permission_management":
            decision = self._evaluate_permission_management(
                intent, speaker_result, effective_role, pin_attempt, confirmation
            )
            self._log_and_audit(timestamp, decision, intent, speaker_result.confidence)
            return decision

        # 7. Evaluate General Category / Tier Access
        if effective_role == Role.GUEST:
            decision = self._evaluate_guest_access(intent, speaker_result, lockout_key)
        else:
            decision = self._evaluate_enrolled_access(
                intent, speaker_result, effective_role, pin_attempt, confirmation
            )

        self._log_and_audit(timestamp, decision, intent, speaker_result.confidence)
        return decision

    # --- Role Resolution ---

    def _resolve_role(self, speaker_result: SpeakerIdentificationResult, tier: SensitivityTier) -> Role:
        """Applies multi-speaker checks, ambiguity checks, and tier-specific confidence thresholds."""
        if not speaker_result.user_id:
            return Role.GUEST

        # Multi-speaker check: Default to lowest privilege (GUEST)
        if len(speaker_result.detected_speakers) > 1:
            logger.info("Multiple speakers detected (%s). Downgrading to GUEST.", speaker_result.detected_speakers)
            return Role.GUEST

        # Ambiguity check: Default to GUEST
        if speaker_result.is_ambiguous:
            logger.info("Ambiguous speaker profile. Downgrading to GUEST.")
            return Role.GUEST

        # Tier confidence requirements
        req_thresh = self.policy_store.threshold_normal
        if tier == SensitivityTier.TIER_3_ENROLLED_ONLY:
            req_thresh = self.policy_store.threshold_high
        elif tier == SensitivityTier.TIER_4_RESTRICTED_ADMIN:
            req_thresh = self.policy_store.threshold_very_high

        if speaker_result.confidence < req_thresh:
            logger.info("Confidence %.2f below required %.2f for %s. Treating as GUEST.",
                        speaker_result.confidence, req_thresh, tier.name)
            return Role.GUEST

        # Verify user exists in enrolled database
        if not self.policy_store.is_enrolled_user(speaker_result.user_id):
            return Role.GUEST

        return Role.ENROLLED

    # --- Evaluators ---

    def _evaluate_personal_data(
        self,
        intent: IntentClassification,
        speaker_result: SpeakerIdentificationResult,
        effective_role: Role,
        timestamp: str,
    ) -> AccessDecision:
        """Personal data is strictly per-user isolated and never accessible to guests."""
        # Rule 1: Guests cannot access personal data under any circumstances
        if effective_role == Role.GUEST:
            # Polite refusal without revealing whether data exists
            return AccessDecision(
                status=AccessStatus.DENIED,
                allowed=False,
                speaker_id=None,
                effective_role=Role.GUEST,
                category=intent.category,
                tier=intent.tier,
                reason="Guests are strictly prohibited from accessing personal data.",
                response_message=GUEST_REFUSAL_MESSAGE,
            )

        # Rule 2: Enrolled user accessing another user's personal data
        caller = speaker_result.user_id
        target = intent.target_user_id
        if target and target.lower() != caller.lower():
            # Enrolled user trying to access another user's personal data
            return AccessDecision(
                status=AccessStatus.DENIED,
                allowed=False,
                speaker_id=caller,
                effective_role=effective_role,
                category=intent.category,
                tier=intent.tier,
                reason=f"Per-user isolation: {caller} cannot access personal data belonging to {target}.",
                response_message="I cannot access personal data for another user.",
            )

        # Authorized access to own personal data
        output = self.skill_dispatcher.dispatch(intent, caller, effective_role.value)
        return AccessDecision(
            status=AccessStatus.ALLOWED,
            allowed=True,
            speaker_id=caller,
            effective_role=effective_role,
            category=intent.category,
            tier=intent.tier,
            reason="Authorized access to own personal data.",
            response_message=output,
        )

    def _evaluate_permission_management(
        self,
        intent: IntentClassification,
        speaker_result: SpeakerIdentificationResult,
        effective_role: Role,
        pin_attempt: Optional[str],
        confirmation: bool,
    ) -> AccessDecision:
        """Tier 4 Admin: Changing or reading guest permissions."""
        caller = speaker_result.user_id

        # Guests can NEVER manage permissions
        if effective_role == Role.GUEST:
            return AccessDecision(
                status=AccessStatus.DENIED,
                allowed=False,
                speaker_id=None,
                effective_role=Role.GUEST,
                category=intent.category,
                tier=SensitivityTier.TIER_4_RESTRICTED_ADMIN,
                reason="Guests are prohibited from managing system permissions.",
                response_message=GUEST_REFUSAL_MESSAGE,
            )

        # Enrolled user: Read permissions (no PIN needed) vs. Modify (Requires Voice PIN)
        if intent.action == "get_permissions":
            summary = self.policy_store.get_guest_permissions_summary()
            allowed_cats = [c.replace("_", " ") for c, allowed in summary.items() if allowed]
            allowed_str = ", ".join(allowed_cats) if allowed_cats else "none"
            return AccessDecision(
                status=AccessStatus.ALLOWED,
                allowed=True,
                speaker_id=caller,
                effective_role=effective_role,
                category=intent.category,
                tier=SensitivityTier.TIER_4_RESTRICTED_ADMIN,
                reason="Summary of guest permissions retrieved.",
                response_message=f"Right now, guests can access: {allowed_str}.",
            )

        # Reset or Set permissions requires Tier 4 Second Factor (Voice PIN)
        if not pin_attempt:
            return AccessDecision(
                status=AccessStatus.CHALLENGE_PIN,
                allowed=False,
                speaker_id=caller,
                effective_role=effective_role,
                category=intent.category,
                tier=SensitivityTier.TIER_4_RESTRICTED_ADMIN,
                reason="Voice PIN required for Tier 4 permission changes.",
                response_message="To change guest permissions, please speak your voice PIN.",
                requires_second_factor=True,
            )

        # Validate PIN
        if not self.second_factor_verifier.verify_pin(caller, pin_attempt):
            triggered_lockout = self.rate_limiter.record_failure(caller)
            if triggered_lockout:
                self.notification_manager.alert_enrolled_user(
                    target_enrolled_user=caller,
                    event_type="SECURITY_LOCKOUT",
                    details=f"Repeated incorrect PIN attempts on {caller}'s profile.",
                    severity="CRITICAL",
                )
            return AccessDecision(
                status=AccessStatus.DENIED,
                allowed=False,
                speaker_id=caller,
                effective_role=effective_role,
                category=intent.category,
                tier=SensitivityTier.TIER_4_RESTRICTED_ADMIN,
                reason="Incorrect voice PIN.",
                response_message="That voice PIN was incorrect.",
            )

        # PIN verified!
        self.rate_limiter.record_success(caller)

        if intent.action == "reset_permissions":
            self.policy_store.reset_to_defaults()
            return AccessDecision(
                status=AccessStatus.ALLOWED,
                allowed=True,
                speaker_id=caller,
                effective_role=effective_role,
                category=intent.category,
                tier=SensitivityTier.TIER_4_RESTRICTED_ADMIN,
                reason="Guest permissions reset to defaults by enrolled user.",
                response_message="Guest permissions have been reset to default.",
            )

        # Set permission for specific category
        target_cat = intent.details.get("target_category", "")
        enabled = intent.details.get("enabled", True)
        success, msg = self.policy_store.set_guest_permission(target_cat, enabled, confirmed=confirmation)

        return AccessDecision(
            status=AccessStatus.ALLOWED if success else AccessStatus.DENIED,
            allowed=success,
            speaker_id=caller,
            effective_role=effective_role,
            category=intent.category,
            tier=SensitivityTier.TIER_4_RESTRICTED_ADMIN,
            reason=f"Set guest permission for {target_cat}: {success}.",
            response_message=msg,
        )

    def _evaluate_guest_access(
        self,
        intent: IntentClassification,
        speaker_result: SpeakerIdentificationResult,
        lockout_key: str,
    ) -> AccessDecision:
        """Evaluates whether an unauthenticated or demoted guest can run the requested category."""
        category = intent.category

        # Check Third-Party Skills specifically
        if category == "third_party_skills":
            skill_name = intent.details.get("skill_name", "")
            is_skill_allowed = self.policy_store.is_third_party_skill_allowed(skill_name)
            is_cat_allowed = self.policy_store.is_guest_allowed("third_party_skills")
            if is_cat_allowed and is_skill_allowed:
                output = self.skill_dispatcher.dispatch(intent, None, "guest")
                return AccessDecision(
                    status=AccessStatus.ALLOWED,
                    allowed=True,
                    speaker_id=None,
                    effective_role=Role.GUEST,
                    category=category,
                    tier=intent.tier,
                    reason=f"Skill '{skill_name}' is in the third-party allow list.",
                    response_message=output,
                )
            else:
                return AccessDecision(
                    status=AccessStatus.DENIED,
                    allowed=False,
                    speaker_id=None,
                    effective_role=Role.GUEST,
                    category=category,
                    tier=intent.tier,
                    reason=f"Skill '{skill_name}' is blocked for guests.",
                    response_message=GUEST_REFUSAL_MESSAGE,
                )

        # Check Policy Store for guest enablement
        if self.policy_store.is_guest_allowed(category):
            output = self.skill_dispatcher.dispatch(intent, None, "guest")
            return AccessDecision(
                status=AccessStatus.ALLOWED,
                allowed=True,
                speaker_id=None,
                effective_role=Role.GUEST,
                category=category,
                tier=intent.tier,
                reason=f"Category '{category}' is permitted for guests.",
                response_message=output,
            )

        # Denied guest access
        # If this was a sensitive attempt (Tier 3 or Tier 4), track it for rate-limiting
        if intent.tier >= SensitivityTier.TIER_3_ENROLLED_ONLY:
            triggered = self.rate_limiter.record_failure(lockout_key)
            if triggered:
                # Notify any enrolled user of repeated unauthorized attempts
                primary_user = next(iter(self.policy_store._enrolled_users), "enrolled_user")
                self.notification_manager.alert_enrolled_user(
                    target_enrolled_user=primary_user,
                    event_type="UNAUTHORIZED_SENSITIVE_ATTEMPTS",
                    details=f"Multiple blocked attempts on {category} from unverified voice.",
                    severity="WARNING",
                )

        return AccessDecision(
            status=AccessStatus.DENIED,
            allowed=False,
            speaker_id=None,
            effective_role=Role.GUEST,
            category=category,
            tier=intent.tier,
            reason=f"Category '{category}' is blocked for guests.",
            response_message=GUEST_REFUSAL_MESSAGE,
        )

    def _evaluate_enrolled_access(
        self,
        intent: IntentClassification,
        speaker_result: SpeakerIdentificationResult,
        effective_role: Role,
        pin_attempt: Optional[str],
        confirmation: bool,
    ) -> AccessDecision:
        """Evaluates requests by verified enrolled users."""
        caller = speaker_result.user_id
        tier = intent.tier

        # Tier 4: Requires high confidence + Second Factor (Voice PIN)
        if tier == SensitivityTier.TIER_4_RESTRICTED_ADMIN:
            if not pin_attempt:
                return AccessDecision(
                    status=AccessStatus.CHALLENGE_PIN,
                    allowed=False,
                    speaker_id=caller,
                    effective_role=effective_role,
                    category=intent.category,
                    tier=tier,
                    reason="Tier 4 action requires voice PIN second factor.",
                    response_message="This action requires second-factor authorization. Please speak your voice PIN.",
                    requires_second_factor=True,
                )

            if not self.second_factor_verifier.verify_pin(caller, pin_attempt):
                triggered = self.rate_limiter.record_failure(caller)
                if triggered:
                    self.notification_manager.alert_enrolled_user(
                        target_enrolled_user=caller,
                        event_type="SECURITY_LOCKOUT",
                        details=f"Security lockout triggered on Tier 4 request by {caller}.",
                        severity="CRITICAL",
                    )
                return AccessDecision(
                    status=AccessStatus.DENIED,
                    allowed=False,
                    speaker_id=caller,
                    effective_role=effective_role,
                    category=intent.category,
                    tier=tier,
                    reason="Invalid voice PIN for Tier 4 action.",
                    response_message="The voice PIN provided was incorrect.",
                )

        # Successful authorization
        self.rate_limiter.record_success(caller)
        output = self.skill_dispatcher.dispatch(intent, caller, effective_role.value)
        return AccessDecision(
            status=AccessStatus.ALLOWED,
            allowed=True,
            speaker_id=caller,
            effective_role=effective_role,
            category=intent.category,
            tier=tier,
            reason=f"Authorized {tier.name} request for enrolled user {caller}.",
            response_message=output,
        )

    # --- Audit Logger Integration ---

    def _log_and_audit(
        self,
        timestamp: str,
        decision: AccessDecision,
        intent: IntentClassification,
        confidence: float,
    ) -> None:
        self.audit_logger.log_decision(
            timestamp=timestamp,
            speaker_id=decision.speaker_id,
            effective_role=decision.effective_role.value,
            category=decision.category,
            action=intent.action,
            tier=decision.tier.value,
            allowed=decision.allowed,
            status=decision.status.value,
            reason=decision.reason,
            confidence=confidence,
        )
