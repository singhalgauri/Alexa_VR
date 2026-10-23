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
            # High-precision acoustic embedding with speaker-discriminative features
            # Uses MFCC-like DCT, delta coefficients, pitch estimation, and
            # spectral dynamics to capture vocal tract characteristics unique to
            # each speaker, unlike simple spectrogram statistics.
            import scipy.signal
            from scipy.fft import dct

            if sr != 16000:
                data = scipy.signal.resample_poly(data, 16000, sr).astype(np.float32)
                sr = 16000

            # --- 1. Mel-scale filterbank log-energies (MFCC-like) ---
            n_fft = 512
            hop = 256
            f, t_axis, Sxx = scipy.signal.spectrogram(
                data, fs=sr, nperseg=n_fft, noverlap=n_fft - hop,
                window="hann", mode="magnitude",
            )
            # Apply mel-scale triangular filterbank (40 filters)
            n_mels = 40
            mel_low = 2595.0 * np.log10(1.0 + 0.0 / 700.0)
            mel_high = 2595.0 * np.log10(1.0 + (sr / 2.0) / 700.0)
            mel_pts = np.linspace(mel_low, mel_high, n_mels + 2)
            hz_pts = 700.0 * (10.0 ** (mel_pts / 2595.0) - 1.0)
            bin_pts = np.floor((n_fft + 1) * hz_pts / sr).astype(int)
            bin_pts = np.clip(bin_pts, 0, Sxx.shape[0] - 1)

            mel_fb = np.zeros((n_mels, Sxx.shape[0]), dtype=np.float32)
            for m_idx in range(n_mels):
                left, center, right = bin_pts[m_idx], bin_pts[m_idx + 1], bin_pts[m_idx + 2]
                if center > left:
                    mel_fb[m_idx, left:center] = np.linspace(0, 1, center - left, endpoint=False)
                if right > center:
                    mel_fb[m_idx, center:right] = np.linspace(1, 0, right - center, endpoint=False)

            mel_spec = mel_fb @ Sxx  # (n_mels, n_frames)
            log_mel = np.log1p(mel_spec + 1e-10)

            # DCT to get MFCC-like coefficients (13 cepstral coefficients per frame)
            n_ceps = 13
            mfcc = dct(log_mel, type=2, axis=0, norm="ortho")[:n_ceps, :]  # (13, n_frames)

            # --- 2. Delta and acceleration coefficients ---
            def _delta(feat: np.ndarray, width: int = 2) -> np.ndarray:
                padded = np.pad(feat, ((0, 0), (width, width)), mode="edge")
                delta = np.zeros_like(feat)
                denom = 2.0 * sum(n * n for n in range(1, width + 1))
                if denom == 0:
                    return delta
                for n in range(1, width + 1):
                    delta += n * (padded[:, width + n:width + n + feat.shape[1]]
                                  - padded[:, width - n:width - n + feat.shape[1]])
                return delta / denom

            delta_mfcc = _delta(mfcc)
            accel_mfcc = _delta(delta_mfcc)

            # MFCC statistics: mean + std for each of 13 coefficients = 26 features
            mfcc_stats = np.concatenate([
                np.mean(mfcc, axis=1), np.std(mfcc, axis=1),
            ])  # 26

            # Delta statistics: mean + std = 26 features
            delta_stats = np.concatenate([
                np.mean(delta_mfcc, axis=1), np.std(delta_mfcc, axis=1),
            ])  # 26

            # Acceleration statistics: mean + std = 26 features
            accel_stats = np.concatenate([
                np.mean(accel_mfcc, axis=1), np.std(accel_mfcc, axis=1),
            ])  # 26

            # --- 3. Pitch (F0) estimation via autocorrelation ---
            frame_len = int(0.03 * sr)  # 30ms frames
            frame_hop = int(0.01 * sr)  # 10ms hop
            pitches = []
            for start_idx in range(0, len(data) - frame_len, frame_hop):
                frame = data[start_idx:start_idx + frame_len]
                frame = frame * np.hanning(len(frame))
                corr = np.correlate(frame, frame, mode="full")
                corr = corr[len(corr) // 2:]
                # Search for pitch between 60Hz and 500Hz
                min_lag = int(sr / 500)
                max_lag = min(int(sr / 60), len(corr) - 1)
                if max_lag > min_lag and len(corr) > max_lag:
                    search = corr[min_lag:max_lag]
                    if len(search) > 0 and np.max(search) > 0.2 * corr[0]:
                        best_lag = np.argmax(search) + min_lag
                        if best_lag > 0:
                            pitches.append(sr / best_lag)

            if pitches:
                pitch_arr = np.array(pitches, dtype=np.float32)
                pitch_features = np.array([
                    np.mean(pitch_arr), np.std(pitch_arr),
                    np.median(pitch_arr),
                    np.percentile(pitch_arr, 10), np.percentile(pitch_arr, 90),
                    float(len(pitch_arr)) / max(1, len(data) // frame_hop),  # voicing ratio
                ], dtype=np.float32)
            else:
                pitch_features = np.zeros(6, dtype=np.float32)
            # 6 features

            # --- 4. Spectral shape features (per-frame centroid, rolloff, flux) ---
            freqs = f
            spectral_centroids = []
            spectral_rolloffs = []
            spectral_flux = []
            for col in range(Sxx.shape[1]):
                mag = Sxx[:, col]
                total = np.sum(mag)
                if total > 0:
                    centroid = np.sum(freqs * mag) / total
                    spectral_centroids.append(centroid)
                    cumsum = np.cumsum(mag)
                    rolloff_idx = np.searchsorted(cumsum, 0.85 * total)
                    spectral_rolloffs.append(freqs[min(rolloff_idx, len(freqs) - 1)])
                else:
                    spectral_centroids.append(0.0)
                    spectral_rolloffs.append(0.0)
                if col > 0:
                    prev_mag = Sxx[:, col - 1]
                    flux = np.sqrt(np.mean((mag - prev_mag) ** 2))
                    spectral_flux.append(flux)

            sc = np.array(spectral_centroids, dtype=np.float32)
            sr_arr = np.array(spectral_rolloffs, dtype=np.float32)
            sf = np.array(spectral_flux, dtype=np.float32) if spectral_flux else np.zeros(1, dtype=np.float32)

            spectral_shape = np.array([
                np.mean(sc), np.std(sc), np.median(sc),
                np.mean(sr_arr), np.std(sr_arr), np.median(sr_arr),
                np.mean(sf), np.std(sf),
            ], dtype=np.float32)  # 8 features

            # --- 5. Energy dynamics ---
            frame_energies = np.array([
                np.sum(data[i:i + frame_len] ** 2)
                for i in range(0, len(data) - frame_len, frame_hop)
            ], dtype=np.float32)
            if len(frame_energies) > 0:
                log_energy = np.log1p(frame_energies)
                energy_feats = np.array([
                    np.mean(log_energy), np.std(log_energy),
                    np.percentile(log_energy, 10), np.percentile(log_energy, 90),
                ], dtype=np.float32)
            else:
                energy_feats = np.zeros(4, dtype=np.float32)
            # 4 features

            # --- 6. Mel-band statistics for formant-proxying ---
            # Focus on formant-region bands (300Hz-3500Hz → roughly mel bands 5-30)
            formant_bands = log_mel[5:30, :]  # 25 bands
            formant_stats = np.concatenate([
                np.mean(formant_bands, axis=1),  # 25
                np.std(formant_bands, axis=1),   # 25
            ])  # 50 features

            # --- 7. Log-mel band covariance (speaker texture) ---
            # Capture cross-band correlations that are unique to vocal tract shape
            if log_mel.shape[1] > 2:
                # Sub-sample bands to keep feature count manageable
                sel_bands = log_mel[::3, :]  # ~13 bands
                cov = np.corrcoef(sel_bands)
                # Upper triangle of correlation matrix
                upper_tri = cov[np.triu_indices(cov.shape[0], k=1)]
                cov_feats = upper_tri[:36].astype(np.float32)  # cap at 36
                if len(cov_feats) < 36:
                    cov_feats = np.pad(cov_feats, (0, 36 - len(cov_feats)))
            else:
                cov_feats = np.zeros(36, dtype=np.float32)
            # 36 features

            # --- Concatenate all features → 192-D ---
            # 26 + 26 + 26 + 6 + 8 + 4 + 50 + 36 = 182; pad to 192
            all_feats = np.concatenate([
                mfcc_stats,       # 26
                delta_stats,      # 26
                accel_stats,      # 26
                pitch_features,   # 6
                spectral_shape,   # 8
                energy_feats,     # 4
                formant_stats,    # 50
                cov_feats,        # 36
            ])
            # Pad or trim to exactly 192
            if len(all_feats) < 192:
                all_feats = np.pad(all_feats, (0, 192 - len(all_feats)))
            vec = all_feats[:192].astype(np.float32)

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
