#!/usr/bin/env python3
"""
scripts/eval_threshold.py – Evaluate speaker verification threshold metrics.

Given a directory of speaker folders containing WAV files:
  data_dir/
    alice/
      clip1.wav, clip2.wav, ...
    bob/
      clip1.wav, clip2.wav, ...

This script computes:
  - Genuine similarity distribution (same speaker)
  - Impostor similarity distribution (different speakers)
  - False Acceptance Rate (FAR) and False Rejection Rate (FRR) across thresholds
  - Equal Error Rate (EER) and recommended threshold.

Usage:
    python scripts/eval_threshold.py --data-dir path/to/dataset
    python scripts/eval_threshold.py --synthetic  # Run with synthetic test data
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
import numpy as np

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.voice_gate import get_gate, _get_embedder, set_embedder


def generate_synthetic_dataset(temp_dir: Path, num_speakers: int = 5, clips_per_speaker: int = 4) -> Path:
    """Generate temporary synthetic dataset for demonstration and benchmarking."""
    import wave
    from tests.conftest import FakeSpeakerEmbedder, make_synthetic_wav

    set_embedder(FakeSpeakerEmbedder())
    dataset_path = temp_dir / "synthetic_dataset"
    dataset_path.mkdir(parents=True, exist_ok=True)

    base_freqs = [440.0, 880.0, 1320.0, 1760.0, 2200.0]
    for i in range(num_speakers):
        spk_name = f"speaker_{i+1:02d}"
        spk_dir = dataset_path / spk_name
        spk_dir.mkdir(parents=True, exist_ok=True)
        freq = base_freqs[i % len(base_freqs)]
        for c in range(clips_per_speaker):
            wav_bytes = make_synthetic_wav(freq=freq + c * 2, duration_sec=1.2)
            (spk_dir / f"clip_{c+1:02d}.wav").write_bytes(wav_bytes)

    return dataset_path


def load_and_embed_dataset(data_dir: Path):
    """Load WAV files from speaker subfolders and compute embeddings."""
    embedder = _get_embedder()
    speaker_embeddings: dict[str, list[np.ndarray]] = {}

    speaker_dirs = [d for d in data_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
    if not speaker_dirs:
        raise ValueError(f"No speaker directories found in {data_dir}")

    print(f"[*] Found {len(speaker_dirs)} speaker folders in {data_dir}")
    for sdir in speaker_dirs:
        speaker_name = sdir.name
        wav_files = sorted(list(sdir.glob("*.wav")) + list(sdir.glob("*.WAV")))
        if len(wav_files) < 2:
            print(f"[-] Skipping {speaker_name}: needs at least 2 WAV files (has {len(wav_files)})")
            continue

        embeddings = []
        for wf in wav_files:
            try:
                emb = embedder.embed(wf.read_bytes())
                embeddings.append(emb)
            except Exception as exc:
                print(f"[-] Error embedding {wf}: {exc}")

        if len(embeddings) >= 2:
            speaker_embeddings[speaker_name] = embeddings
            print(f"    [+] {speaker_name}: embedded {len(embeddings)} clips")

    return speaker_embeddings


def evaluate_metrics(speaker_embeddings: dict[str, list[np.ndarray]]):
    """Compute genuine and impostor cosine similarities and error rates."""
    if len(speaker_embeddings) < 2:
        raise ValueError("Need at least 2 speakers with embedded clips to evaluate.")

    genuine_scores = []
    impostor_scores = []

    speakers = list(speaker_embeddings.keys())
    centroids: dict[str, np.ndarray] = {}

    # Compute enrollment centroid from first 3 (or half) clips
    eval_probes: dict[str, list[np.ndarray]] = {}
    for spk, embs in speaker_embeddings.items():
        n_enroll = max(1, len(embs) // 2)
        enroll_embs = embs[:n_enroll]
        eval_probes[spk] = embs[n_enroll:]

        centroid = np.mean(enroll_embs, axis=0).astype(np.float32)
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroid = centroid / norm
        centroids[spk] = centroid

    # Compute genuine scores (probe vs own centroid)
    for spk, probes in eval_probes.items():
        centroid = centroids[spk]
        for p in probes:
            score = float(np.dot(centroid, p))
            genuine_scores.append(score)

    # Compute impostor scores (probe vs other speakers' centroids)
    for spk, probes in eval_probes.items():
        for other_spk, other_centroid in centroids.items():
            if other_spk == spk:
                continue
            for p in probes:
                score = float(np.dot(other_centroid, p))
                impostor_scores.append(score)

    genuine = np.array(genuine_scores)
    impostor = np.array(impostor_scores)

    print("\n" + "=" * 65)
    print("SIMILARITY SCORE DISTRIBUTION SUMMARY")
    print("=" * 65)
    print(f"Genuine pairs  : {len(genuine):5d} | Mean: {np.mean(genuine):.4f} | Std: {np.std(genuine):.4f} | Min: {np.min(genuine):.4f} | Max: {np.max(genuine):.4f}")
    print(f"Impostor pairs : {len(impostor):5d} | Mean: {np.mean(impostor):.4f} | Std: {np.std(impostor):.4f} | Min: {np.min(impostor):.4f} | Max: {np.max(impostor):.4f}")
    print("-" * 65)

    thresholds = np.arange(0.10, 0.95, 0.05)
    print(f"{'Threshold':>10} | {'FAR (False Accept)':>18} | {'FRR (False Reject)':>18} | {'Diff':>10}")
    print("-" * 65)

    best_thresh = 0.40
    min_diff = float("inf")

    for t in thresholds:
        far = np.mean(impostor >= t) * 100.0  # Impostors incorrectly accepted
        frr = np.mean(genuine < t) * 100.0    # Genuine users incorrectly rejected
        diff = abs(far - frr)
        if diff < min_diff:
            min_diff = diff
            best_thresh = t
        marker = " <== (EER Approx)" if abs(far - frr) == min_diff else ""
        print(f"{t:>10.2f} | {far:>17.2f}% | {frr:>17.2f}% | {diff:>9.2f}%{marker}")

    print("=" * 65)
    print(f"Recommended Threshold : {best_thresh:.2f}")
    print(f"Default In Code       : 0.40")
    print(f"Recommendation Note   : Set VOICEGATE_THRESHOLD={best_thresh:.2f} in production or .env")
    print("=" * 65 + "\n")

    return best_thresh


def main():
    parser = argparse.ArgumentParser(description="Evaluate speaker verification threshold metrics.")
    parser.add_argument("--data-dir", type=str, help="Path to directory containing speaker folders.")
    parser.add_argument("--synthetic", action="store_true", help="Generate and evaluate on synthetic test data.")
    args = parser.parse_args()

    if not args.data_dir and not args.synthetic:
        print("Please specify either --data-dir <path> or --synthetic.")
        parser.print_help()
        sys.exit(1)

    if args.synthetic:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            data_dir = generate_synthetic_dataset(Path(tmp_dir))
            embeddings = load_and_embed_dataset(data_dir)
            evaluate_metrics(embeddings)
    else:
        data_dir = Path(args.data_dir)
        if not data_dir.exists():
            print(f"Error: directory {data_dir} does not exist.")
            sys.exit(1)
        embeddings = load_and_embed_dataset(data_dir)
        evaluate_metrics(embeddings)


if __name__ == "__main__":
    main()
