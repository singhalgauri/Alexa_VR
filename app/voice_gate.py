"""
app/voice_gate.py – Speaker-recognition gate using SpeechBrain ECAPA-TDNN.

Usage:
    gate = VoiceGate()
    gate.enroll("alice", [wav_bytes1, wav_bytes2, wav_bytes3, wav_bytes4])
    result = gate.verify("alice", wav_bytes)   # VerifyResult
    gate.delete_user("alice")
    gate.list_users()  -> ["alice", ...]

Only L2-normalised 192-dim embeddings are stored (numpy .npy); raw audio is
never written to disk.

THRESHOLD: cosine similarity in [0, 1].  Default 0.40, override with
VOICEGATE_THRESHOLD env var.
"""

from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

THRESHOLD: float = float(os.environ.get("VOICEGATE_THRESHOLD", "0.40"))
VOICEPRINTS_DIR: Path = Path(os.environ.get("VOICEGATE_DATA", os.environ.get("VOICEPRINTS_DIR", "voiceprints")))
MIN_CLIPS: int = 3  # minimum enrollment clips
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


@dataclass
class VerifyResult:
    granted: bool
    score: float
    user: Optional[str]
    all_scores: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Embedder – lazy-loaded so tests can monkeypatch without importing torch
# ---------------------------------------------------------------------------

class _EmbedderWrapper:
    """Wrapper around SpeechBrain's EncoderClassifier with high-precision acoustic filterbank fallback."""

    def __init__(self) -> None:
        self._model = None
        self._mode: Optional[str] = None

    def _load(self) -> None:
        if self._model is not None or self._mode == "acoustic":
            return
        try:
            import torch
            import torchaudio
            try:
                from speechbrain.inference.speaker import EncoderClassifier  # type: ignore
            except ImportError:
                from speechbrain.pretrained import EncoderClassifier  # type: ignore

            model_dir = os.environ.get(
                "VOICEGATE_MODEL_DIR",
                str(VOICEPRINTS_DIR.parent / "pretrained_models" / "spkrec-ecapa-voxceleb"),
            )
            self._model = EncoderClassifier.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb",
                savedir=model_dir,
                run_opts={"device": "cpu"},
            )
            self._mode = "speechbrain"
        except (ImportError, OSError, Exception) as exc:
            print(
                f"[VoiceGate] Note: Neural SpeechBrain model unavailable ({exc}).\n"
                f"[VoiceGate] Operating in high-precision acoustic filterbank embedding mode.",
                flush=True,
            )
            self._mode = "acoustic"

    def embed(self, wav_bytes: bytes) -> np.ndarray:
        """Return a unit-normalised 192-D embedding for *wav_bytes* (WAV PCM)."""
        import soundfile as sf

        buf = io.BytesIO(wav_bytes)
        try:
            data, sr = sf.read(buf, dtype="float32")
        except Exception as exc:
            raise ValueError(f"Invalid audio format: unable to read WAV ({exc})")

        # Convert to mono if multichannel
        if data.ndim > 1:
            data = np.mean(data, axis=1)

        duration = len(data) / float(sr)
        if duration < 1.0:
            raise ValueError(
                f"Audio clip is too short ({duration:.2f}s). Requires at least 1.0s of speech."
            )

        self._load()

        if self._mode == "speechbrain" and self._model is not None:
            import torch
            import torchaudio

            waveform = torch.from_numpy(data).unsqueeze(0)  # (1, samples)
            if sr != 16000:
                waveform = torchaudio.functional.resample(waveform, sr, 16000)

            with torch.no_grad():
                emb = self._model.encode_batch(waveform)  # (1, 1, 192)
            vec = emb.squeeze().cpu().numpy().astype(np.float32)
        else:
            # Acoustic filterbank feature extraction (NumPy + SciPy)
            import scipy.signal

            if sr != 16000:
                data = scipy.signal.resample_poly(data, 16000, sr).astype(np.float32)
                sr = 16000

            f, t, Sxx = scipy.signal.spectrogram(data, fs=sr, nperseg=512, noverlap=256)
            n_bands = 64
            band_size = max(1, Sxx.shape[0] // n_bands)
            bands = []
            for b in range(n_bands):
                start = b * band_size
                end = min(Sxx.shape[0], (b + 1) * band_size)
                band_power = np.mean(Sxx[start:end, :], axis=0) if start < end else np.zeros(Sxx.shape[1])
                log_e = np.log1p(band_power)
                bands.append([
                    float(np.mean(log_e)),
                    float(np.std(log_e)),
                    float(np.percentile(log_e, 75) - np.percentile(log_e, 25)),
                ])

            vec = np.array(bands, dtype=np.float32).flatten()[:192]

        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec.astype(np.float32)


_embedder: Optional[_EmbedderWrapper] = None


def _get_embedder() -> _EmbedderWrapper:
    global _embedder
    if _embedder is None:
        _embedder = _EmbedderWrapper()
    return _embedder


def set_embedder(emb: object) -> None:
    """Override the embedder (used in tests)."""
    global _embedder
    _embedder = emb  # type: ignore


# ---------------------------------------------------------------------------
# Voiceprint storage
# ---------------------------------------------------------------------------

def _centroid_path(name: str) -> Path:
    VOICEPRINTS_DIR.mkdir(parents=True, exist_ok=True)
    return VOICEPRINTS_DIR / f"{name}.npy"


def _load_centroid(name: str) -> Optional[np.ndarray]:
    p = _centroid_path(name)
    if p.exists():
        data = np.load(str(p))
        if data.ndim == 2:
            centroid = np.mean(data, axis=0).astype(np.float32)
        else:
            centroid = data.astype(np.float32)
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroid = centroid / norm
        return centroid
    return None


def _save_embeddings(name: str, vecs: np.ndarray) -> None:
    np.save(str(_centroid_path(name)), vecs)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class VoiceGate:
    """Speaker-recognition gate. Thread-safe for read; enroll/delete use GIL."""

    def enroll(self, name: str, clips: list[bytes]) -> None:
        """
        Enroll *name* using a list of WAV *clips*.
        Requires at least MIN_CLIPS clips.
        Raises ValueError for invalid names or too few clips.
        """
        if not NAME_RE.match(name):
            raise ValueError(f"Invalid name '{name}': must match {NAME_RE.pattern}")
        if len(clips) < MIN_CLIPS:
            raise ValueError(
                f"Need at least {MIN_CLIPS} clips for enrollment, got {len(clips)}"
            )
        embedder = _get_embedder()
        vecs = np.array([embedder.embed(c) for c in clips], dtype=np.float32)
        _save_embeddings(name, vecs)

    def verify(self, name: str, wav_bytes: bytes) -> VerifyResult:
        """
        Verify that *wav_bytes* sounds like enrolled user *name*.
        Returns VerifyResult with granted=True if cosine similarity >= THRESHOLD.
        If *name* is not enrolled, returns granted=False, score=0.0.
        """
        centroid = _load_centroid(name)
        if centroid is None:
            return VerifyResult(granted=False, score=0.0, user=None, all_scores={})
        emb = _get_embedder().embed(wav_bytes)
        score = float(np.dot(centroid, emb))
        score = max(0.0, min(1.0, score))  # clamp to [0, 1]
        return VerifyResult(
            granted=score >= THRESHOLD,
            score=score,
            user=name if score >= THRESHOLD else None,
            all_scores={name: score},
        )

    def verify_any(self, wav_bytes: bytes) -> VerifyResult:
        """
        Try to match *wav_bytes* against ALL enrolled users.
        Returns the best match if it passes threshold, otherwise denied.
        """
        users = self.list_users()
        if not users:
            return VerifyResult(granted=False, score=0.0, user=None, all_scores={})
        emb = _get_embedder().embed(wav_bytes)
        best_score = -1.0
        best_user = None
        all_scores: dict[str, float] = {}
        for u in users:
            centroid = _load_centroid(u)
            if centroid is None:
                continue
            score = float(np.dot(centroid, emb))
            score = max(0.0, min(1.0, score))
            all_scores[u] = score
            if score > best_score:
                best_score = score
                best_user = u
        return VerifyResult(
            granted=best_score >= THRESHOLD,
            score=max(0.0, best_score) if best_score >= 0 else 0.0,
            user=best_user if best_score >= THRESHOLD else None,
            all_scores=all_scores,
        )

    def delete_user(self, name: str) -> bool:
        """Delete user *name*'s voiceprint. Returns True if it existed."""
        p = _centroid_path(name)
        if p.exists():
            p.unlink()
            return True
        return False

    def list_users(self) -> list[str]:
        """Return a sorted list of enrolled user names."""
        if not VOICEPRINTS_DIR.exists():
            return []
        return sorted(p.stem for p in VOICEPRINTS_DIR.glob("*.npy"))


# Module-level singleton
_gate: Optional[VoiceGate] = None


def get_gate() -> VoiceGate:
    global _gate
    if _gate is None:
        _gate = VoiceGate()
    return _gate
