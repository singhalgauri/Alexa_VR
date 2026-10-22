"""
tests/test_voice_gate.py – Unit tests for VoiceGate speaker recognition logic.
Runs completely offline using FakeSpeakerEmbedder.
"""

import pytest
import numpy as np
from app.voice_gate import VoiceGate, VerifyResult
from tests.conftest import make_synthetic_wav


def test_enrollment_requires_at_least_three_clips():
    gate = VoiceGate()
    wav_a = make_synthetic_wav(freq=440.0)

    # 1 clip -> should fail
    with pytest.raises(ValueError, match="at least 3 clips"):
        gate.enroll("alice", [wav_a])

    # 2 clips -> should fail
    with pytest.raises(ValueError, match="at least 3 clips"):
        gate.enroll("alice", [wav_a, wav_a])

    # 3 clips -> passes
    gate.enroll("alice", [wav_a, wav_a, wav_a])
    assert "alice" in gate.list_users()


def test_invalid_names_rejected():
    gate = VoiceGate()
    wav = make_synthetic_wav(freq=440.0)
    clips = [wav, wav, wav]

    # Spaces not allowed
    with pytest.raises(ValueError, match="Invalid name"):
        gate.enroll("alice smith", clips)

    # Special characters not allowed
    with pytest.raises(ValueError, match="Invalid name"):
        gate.enroll("alice!@#", clips)

    # Empty name not allowed
    with pytest.raises(ValueError, match="Invalid name"):
        gate.enroll("", clips)

    # Over 32 characters
    with pytest.raises(ValueError, match="Invalid name"):
        gate.enroll("a" * 33, clips)


def test_same_speaker_passes_different_speaker_fails(monkeypatch):
    monkeypatch.setenv("VOICEGATE_THRESHOLD", "0.40")
    gate = VoiceGate()

    # Enroll Alice with 440 Hz speaker clips
    alice_clips = [make_synthetic_wav(freq=440.0) for _ in range(3)]
    gate.enroll("alice", alice_clips)

    # Same speaker probe clip
    same_speaker_probe = make_synthetic_wav(freq=440.0)
    res_same = gate.verify("alice", same_speaker_probe)
    assert res_same.granted is True
    assert res_same.user == "alice"
    assert res_same.score >= 0.40

    # Different speaker probe clip (880 Hz)
    diff_speaker_probe = make_synthetic_wav(freq=880.0)
    res_diff = gate.verify("alice", diff_speaker_probe)
    assert res_diff.granted is False
    assert res_diff.score < 0.40


def test_verify_any_selects_best_matching_user():
    gate = VoiceGate()

    # Enroll Alice (440 Hz) and Bob (880 Hz)
    gate.enroll("alice", [make_synthetic_wav(freq=440.0) for _ in range(3)])
    gate.enroll("bob", [make_synthetic_wav(freq=880.0) for _ in range(3)])

    # Probe with Alice's voice
    res_alice = gate.verify_any(make_synthetic_wav(freq=440.0))
    assert res_alice.granted is True
    assert res_alice.user == "alice"
    assert res_alice.score >= 0.40
    assert "alice" in res_alice.all_scores
    assert "bob" in res_alice.all_scores
    assert res_alice.all_scores["alice"] > res_alice.all_scores["bob"]

    # Probe with Bob's voice
    res_bob = gate.verify_any(make_synthetic_wav(freq=880.0))
    assert res_bob.granted is True
    assert res_bob.user == "bob"
    assert res_bob.all_scores["bob"] > res_bob.all_scores["alice"]

    # Probe with unknown third speaker (1300 Hz)
    res_charlie = gate.verify_any(make_synthetic_wav(freq=1300.0))
    assert res_charlie.granted is False
    assert res_charlie.user is None


def test_user_deletion_and_listing():
    gate = VoiceGate()
    clips = [make_synthetic_wav(freq=440.0) for _ in range(3)]

    assert gate.list_users() == []
    gate.enroll("user_one", clips)
    gate.enroll("user_two", clips)
    assert gate.list_users() == ["user_one", "user_two"]

    # Delete existing
    assert gate.delete_user("user_one") is True
    assert gate.list_users() == ["user_two"]

    # Delete non-existing
    assert gate.delete_user("non_existent") is False


def test_short_audio_clip_rejected():
    gate = VoiceGate()
    # 0.5s audio clip (< 1.0s minimum requirement)
    short_clip = make_synthetic_wav(freq=440.0, duration_sec=0.5)
    with pytest.raises(ValueError, match="too short"):
        gate.enroll("alice", [short_clip, short_clip, short_clip])
