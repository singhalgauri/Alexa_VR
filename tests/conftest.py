"""
tests/conftest.py – Fixtures and test utilities for Voice Gate test suite.
Runs 100% locally WITHOUT requiring torch, torchaudio, or SpeechBrain models.
"""

from __future__ import annotations

import io
import wave
import struct
import shutil
from pathlib import Path
import pytest
import numpy as np

from app.voice_gate import set_embedder, VoiceGate


def make_synthetic_wav(freq: float = 440.0, duration_sec: float = 1.2, sr: int = 16000) -> bytes:
    """Generate a minimal valid 16-bit PCM mono WAV file."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        n_samples = int(sr * duration_sec)
        t = np.linspace(0, duration_sec, n_samples, endpoint=False)
        # Sine wave with specified frequency
        audio = (np.sin(2 * np.pi * freq * t) * 20000).astype(np.int16)
        wf.writeframes(audio.tobytes())
    return buf.getvalue()


class FakeSpeakerEmbedder:
    """
    Deterministic mock embedder for tests.
    Generates unit-normalized 192-d vectors.
    Maps dominant frequency / tone to a stable speaker vector direction.
    """

    def __init__(self) -> None:
        self.call_count = 0
        # Pre-generate 3 orthogonal bases for distinct speaker types
        rng = np.random.RandomState(42)
        self._speaker_bases = {
            "speaker_a": rng.randn(192).astype(np.float32),
            "speaker_b": rng.randn(192).astype(np.float32),
            "speaker_c": rng.randn(192).astype(np.float32),
        }
        for k in self._speaker_bases:
            self._speaker_bases[k] /= np.linalg.norm(self._speaker_bases[k])

    def embed(self, wav_bytes: bytes) -> np.ndarray:
        self.call_count += 1
        if len(wav_bytes) < 44:
            raise ValueError("Invalid WAV audio data: header too short")

        try:
            buf = io.BytesIO(wav_bytes)
            with wave.open(buf, "rb") as wf:
                sr = wf.getframerate()
                n_frames = wf.getnframes()
                duration = n_frames / float(sr)
                if duration < 1.0:
                    raise ValueError(f"Audio clip is too short ({duration:.2f}s). Requires >= 1.0s")
                raw = wf.readframes(n_frames)
                samples = np.frombuffer(raw, dtype=np.int16)
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Invalid WAV format: {e}")

        # Distinguish speaker by average zero crossing rate / dominant pitch
        # Speaker A: 440 Hz (ZCR approx 0.055)
        # Speaker B: 880 Hz (ZCR approx 0.11)
        # Others: hash-based
        zero_crossings = np.sum(np.diff(samples > 0) != 0) / len(samples)
        if zero_crossings < 0.08:
            base = self._speaker_bases["speaker_a"]
        elif zero_crossings < 0.15:
            base = self._speaker_bases["speaker_b"]
        else:
            base = self._speaker_bases["speaker_c"]

        # Add tiny jitter (< 2%) so distinct clips have slight variance but high cosine similarity
        noise = np.sin(np.arange(192) * (zero_crossings * 100)) * 0.02
        vec = (base + noise).astype(np.float32)
        return vec / np.linalg.norm(vec)


@pytest.fixture(autouse=True)
def setup_fake_voiceprints_dir(tmp_path, monkeypatch):
    """Isolate voiceprints directory to a temporary directory per test."""
    test_dir = tmp_path / "voiceprints_test"
    test_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.voice_gate.VOICEPRINTS_DIR", test_dir)
    monkeypatch.setenv("VOICEGATE_DATA", str(test_dir))

    # Install FakeSpeakerEmbedder
    embedder = FakeSpeakerEmbedder()
    set_embedder(embedder)
    yield test_dir
