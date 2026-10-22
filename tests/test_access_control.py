"""
Unit test suite for the Voice-Identity-Based Access Control Layer.

Verifies:
1. Guest default permissions per tier
2. Tier 0 emergency always allowed, even at low confidence
3. Enrolled user full access to own data
4. Enrolled user cannot read another enrolled user's data
5. Permission change by an enrolled user (success, with second factor)
6. Guest attempting a permission change (must fail)
7. Attempt to grant Tier 4 to guests (must fail)
8. Tier 3 grant requires confirmation
9. Guest asking for personal schedule/lists (must fail, with no data leak)
10. Guest triggering a routine that includes a blocked action (must fail)
11. Low-confidence fallback to guest
12. Multi-speaker audio falls to lowest privilege
13. Lockout after repeated failed sensitive attempts
"""

import os
import tempfile
import pytest

from access_control import (
    AccessDecision,
    AccessStatus,
    GUEST_REFUSAL_MESSAGE,
    PolicyEnforcementGate,
    PolicyStore,
    Role,
    SensitivityTier,
    SpeakerIdentificationResult,
)


@pytest.fixture
def test_gate():
    """Provides a fresh PolicyEnforcementGate with isolated temp storage."""
    temp_dir = tempfile.mkdtemp()
    policy_path = os.path.join(temp_dir, "test_policy.json")
    audit_path = os.path.join(temp_dir, "test_audit.jsonl")

    policy_store = PolicyStore(storage_path=policy_path)
    gate = PolicyEnforcementGate(policy_store=policy_store)
    gate.audit_logger.log_path = audit_path
    return gate


# 1. Guest default permissions per tier
def test_guest_default_permissions_per_tier(test_gate):
    # Tier 0 (Emergency) -> Allowed by default
    res0 = test_gate.process_request(
        "call 911",
        SpeakerIdentificationResult(user_id=None, confidence=0.0)
    )
    assert res0.allowed is True
    assert res0.tier == SensitivityTier.TIER_0_EMERGENCY

    # Tier 1 (Music & Info) -> Allowed by default
    res1_music = test_gate.process_request(
        "play jazz music",
        SpeakerIdentificationResult(user_id=None, confidence=0.0)
    )
    assert res1_music.allowed is True
    assert res1_music.tier == SensitivityTier.TIER_1_GUEST_ALLOWED

    res1_info = test_gate.process_request(
        "what is the weather",
        SpeakerIdentificationResult(user_id=None, confidence=0.0)
    )
    assert res1_info.allowed is True
    assert res1_info.tier == SensitivityTier.TIER_1_GUEST_ALLOWED

    # Tier 2 (Smart Home, TV, Timers) -> Blocked by default for guests
    res2 = test_gate.process_request(
        "turn on the lights",
        SpeakerIdentificationResult(user_id=None, confidence=0.0)
    )
    assert res2.allowed is False
    assert res2.tier == SensitivityTier.TIER_2_GUEST_BLOCKED
    assert res2.response_message == GUEST_REFUSAL_MESSAGE

    # Tier 3 (Home Security, Locks) -> Blocked by default for guests
    res3 = test_gate.process_request(
        "unlock the front door",
        SpeakerIdentificationResult(user_id=None, confidence=0.0)
    )
    assert res3.allowed is False
    assert res3.tier == SensitivityTier.TIER_3_ENROLLED_ONLY
    assert res3.response_message == GUEST_REFUSAL_MESSAGE

    # Tier 4 (Payments, Device Settings) -> Blocked for guests
    res4 = test_gate.process_request(
        "pay my electricity bill",
        SpeakerIdentificationResult(user_id=None, confidence=0.0)
    )
    assert res4.allowed is False
    assert res4.tier == SensitivityTier.TIER_4_RESTRICTED_ADMIN
    assert res4.response_message == GUEST_REFUSAL_MESSAGE


# 2. Tier 0 emergency always allowed, even at low confidence
def test_tier_0_emergency_always_allowed_even_at_low_confidence(test_gate):
    # Guest speaker with 0 confidence
    res_guest = test_gate.process_request(
        "call emergency services",
        SpeakerIdentificationResult(user_id=None, confidence=0.0)
    )
    assert res_guest.allowed is True
    assert "emergency services" in res_guest.response_message.lower()

    # Enrolled speaker with degraded confidence
    res_enrolled_low = test_gate.process_request(
        "I need help, call an ambulance",
        SpeakerIdentificationResult(user_id="gauravi", confidence=0.10)
    )
    assert res_enrolled_low.allowed is True
    assert "emergency services" in res_enrolled_low.response_message.lower()


# 3. Enrolled user full access to own data
def test_enrolled_user_full_access_to_own_data(test_gate):
    res = test_gate.process_request(
        "what is on my calendar",
        SpeakerIdentificationResult(user_id="gauravi", confidence=0.92)
    )
    assert res.allowed is True
    assert res.effective_role == Role.ENROLLED
    assert "KremlinSpies demo" in res.response_message


# 4. Enrolled user cannot read another enrolled user's data
def test_enrolled_user_cannot_read_another_enrolled_users_data(test_gate):
    # Gauravi asks for John's calendar
    res = test_gate.process_request(
        "what is on John's schedule",
        SpeakerIdentificationResult(user_id="gauravi", confidence=0.92)
    )
    assert res.allowed is False
    assert "cannot access personal data for another user" in res.response_message.lower()


# 5. Permission change by an enrolled user (success, with second factor)
def test_permission_change_by_enrolled_user_with_pin(test_gate):
    # Initial state: smart_home is blocked for guests
    assert test_gate.policy_store.is_guest_allowed("smart_home") is False

    # Gauravi authorizes guest smart home access using voice PIN "1234"
    res = test_gate.process_request(
        "Allow guests to use smart home control",
        SpeakerIdentificationResult(user_id="gauravi", confidence=0.95),
        pin_attempt="1234",
    )
    assert res.allowed is True
    assert "guests can now use Smart Home Control" in res.response_message
    assert test_gate.policy_store.is_guest_allowed("smart_home") is True

    # Now verify guest can execute smart home commands!
    guest_cmd = test_gate.process_request(
        "turn on living room lights",
        SpeakerIdentificationResult(user_id=None, confidence=0.0)
    )
    assert guest_cmd.allowed is True
    assert "smart home device command executed" in guest_cmd.response_message.lower()


# 6. Guest attempting a permission change (must fail)
def test_guest_attempting_permission_change_fails(test_gate):
    res = test_gate.process_request(
        "Allow guests to use smart home control",
        SpeakerIdentificationResult(user_id=None, confidence=0.0),
        pin_attempt="1234"
    )
    assert res.allowed is False
    assert res.response_message == GUEST_REFUSAL_MESSAGE
    assert test_gate.policy_store.is_guest_allowed("smart_home") is False


# 7. Attempt to grant Tier 4 to guests (must fail)
def test_attempt_to_grant_tier_4_to_guests_must_fail(test_gate):
    res = test_gate.process_request(
        "Allow guests to use payments and banking",
        SpeakerIdentificationResult(user_id="gauravi", confidence=0.95),
        pin_attempt="1234",
    )
    assert res.allowed is False
    assert "cannot be granted to guests" in res.response_message.lower()
    assert test_gate.policy_store.is_guest_allowed("payments_banking") is False


# 8. Tier 3 grant requires confirmation
def test_tier_3_grant_requires_confirmation(test_gate):
    # Without verbal confirmation/flag
    res_unconfirmed = test_gate.process_request(
        "Allow guests to use home monitoring and security",
        SpeakerIdentificationResult(user_id="gauravi", confidence=0.95),
        pin_attempt="1234",
        confirmation=False,
    )
    assert res_unconfirmed.allowed is False
    assert "security risks" in res_unconfirmed.response_message.lower()
    assert test_gate.policy_store.is_guest_allowed("home_security") is False

    # With explicit confirmation
    res_confirmed = test_gate.process_request(
        "Allow guests to use home monitoring and security",
        SpeakerIdentificationResult(user_id="gauravi", confidence=0.95),
        pin_attempt="1234",
        confirmation=True,
    )
    assert res_confirmed.allowed is True
    assert test_gate.policy_store.is_guest_allowed("home_security") is True


# 9. Guest asking for personal schedule/lists (must fail, with no data leak)
def test_guest_asking_personal_schedule_no_data_leak(test_gate):
    res = test_gate.process_request(
        "What's on John's schedule?",
        SpeakerIdentificationResult(user_id=None, confidence=0.0)
    )
    assert res.allowed is False
    # Strictly zero info leakage: no names, counts, or existence confirmation
    assert res.response_message == GUEST_REFUSAL_MESSAGE
    assert "john" not in res.response_message.lower()
    assert "schedule" not in res.response_message.lower()


# 10. Guest triggering a routine that includes a blocked action (must fail)
def test_guest_triggering_routine_with_blocked_action_must_fail(test_gate):
    # Morning routine includes: ["turn on kitchen lights" (Tier 2), "tell me the news" (Tier 1)]
    # Guest has Tier 1 allowed, but Tier 2 blocked by default.
    res = test_gate.process_request(
        "run morning routine",
        SpeakerIdentificationResult(user_id=None, confidence=0.0)
    )
    assert res.allowed is False
    assert res.tier >= SensitivityTier.TIER_2_GUEST_BLOCKED
    assert res.response_message == GUEST_REFUSAL_MESSAGE


# 11. Low-confidence fallback to guest
def test_low_confidence_fallback_to_guest(test_gate):
    # Normal threshold for Tier 2 is 0.40. Speaker is gauravi with confidence 0.25 (< 0.40).
    res = test_gate.process_request(
        "turn on the lights",
        SpeakerIdentificationResult(user_id="gauravi", confidence=0.25)
    )
    assert res.effective_role == Role.GUEST
    assert res.allowed is False  # Tier 2 blocked for guest by default
    assert res.response_message == GUEST_REFUSAL_MESSAGE


# 12. Multi-speaker audio falls to lowest privilege
def test_multi_speaker_audio_falls_to_lowest_privilege(test_gate):
    # Audio with both enrolled user and guest
    res = test_gate.process_request(
        "unlock the front door",
        SpeakerIdentificationResult(
            user_id="gauravi",
            confidence=0.95,
            detected_speakers=["gauravi", "guest"],
        )
    )
    # Must fallback to lowest privilege (Role.GUEST)
    assert res.effective_role == Role.GUEST
    assert res.allowed is False
    assert res.response_message == GUEST_REFUSAL_MESSAGE


# 13. Lockout after repeated failed sensitive attempts
def test_lockout_after_repeated_failed_sensitive_attempts(test_gate):
    # Attempt 1: Failed PIN on Tier 4
    res1 = test_gate.process_request(
        "Allow guests to use smart home control",
        SpeakerIdentificationResult(user_id="gauravi", confidence=0.95),
        pin_attempt="wrong_pin",
    )
    assert res1.allowed is False

    # Attempt 2: Failed PIN on Tier 4
    res2 = test_gate.process_request(
        "Allow guests to use smart home control",
        SpeakerIdentificationResult(user_id="gauravi", confidence=0.95),
        pin_attempt="wrong_pin_2",
    )
    assert res2.allowed is False

    # Attempt 3: 3rd failed attempt triggers lockout
    res3 = test_gate.process_request(
        "Allow guests to use smart home control",
        SpeakerIdentificationResult(user_id="gauravi", confidence=0.95),
        pin_attempt="wrong_pin_3",
    )
    assert res3.allowed is False

    # Next attempt must be completely locked out!
    res4 = test_gate.process_request(
        "turn on lights",
        SpeakerIdentificationResult(user_id="gauravi", confidence=0.95),
    )
    assert res4.status == AccessStatus.LOCKED_OUT
    assert res4.allowed is False
    assert "temporarily locked" in res4.response_message.lower()

    # But Tier 0 emergency is NEVER locked out!
    res_emergency = test_gate.process_request(
        "call 911",
        SpeakerIdentificationResult(user_id="gauravi", confidence=0.95),
    )
    assert res_emergency.allowed is True
