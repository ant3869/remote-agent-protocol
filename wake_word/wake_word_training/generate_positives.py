"""Synthesize positive wake-word samples with Piper TTS.

For each Piper English voice, generate N renditions of the phrase, then apply
random audio augmentations so the trained model generalizes beyond TTS-perfect
audio. Outputs 16 kHz mono WAVs into data/positive/.

Usage:
    python generate_positives.py --phrase "hey nexus" --count 2000
"""
import argparse
import random
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from tqdm import tqdm

try:
    from audiomentations import (
        Compose, PitchShift, TimeStretch, AddGaussianSNR, RoomSimulator
    )
except ImportError:
    print("[!] audiomentations missing — run: pip install -r requirements.txt")
    sys.exit(1)

try:
    from piper import PiperVoice
except ImportError:
    print("[!] piper-tts missing — run: pip install -r requirements.txt")
    sys.exit(1)


SAMPLE_RATE = 16000
OUT_DIR = Path(__file__).parent / "data" / "positive"


def list_voices():
    """Return a list of available Piper voice model paths.

    Piper voices are downloaded on first use into ~/.local/share/piper/voices/.
    For training we want as many English voices as possible — the more vocal
    variety in positives, the better the model generalizes.
    """
    # Reasonable default set — Piper auto-downloads these on first reference.
    # You can extend this list from https://github.com/rhasspy/piper/blob/master/VOICES.md
    return [
        "en_US-lessac-medium",
        "en_US-amy-medium",
        "en_US-ryan-high",
        "en_US-libritts_r-medium",
        "en_US-hfc_female-medium",
        "en_US-hfc_male-medium",
        "en_GB-alan-medium",
        "en_GB-jenny_dioco-medium",
        "en_GB-northern_english_male-medium",
        "en_GB-southern_english_female-low",
    ]


def build_augmenter():
    return Compose([
        PitchShift(min_semitones=-2, max_semitones=2, p=0.7),
        TimeStretch(min_rate=0.9, max_rate=1.1, p=0.5),
        AddGaussianSNR(min_snr_db=5, max_snr_db=30, p=0.6),
        RoomSimulator(p=0.4),
    ])


def synthesize_one(voice_id: str, phrase: str) -> np.ndarray:
    """Render `phrase` with the given Piper voice; return 16kHz mono float32."""
    # Piper CLI is the most reliable cross-platform path
    proc = subprocess.run(
        ["piper", "--model", voice_id, "--output_raw"],
        input=phrase.encode("utf-8"),
        capture_output=True,
        check=True,
    )
    pcm = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    return pcm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phrase", required=True, help="Wake phrase to synthesize")
    ap.add_argument("--count", type=int, default=2000,
                    help="Total samples to generate")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    voices = list_voices()
    augment = build_augmenter()

    per_voice = max(1, args.count // len(voices))
    total = per_voice * len(voices)
    print(f"[+] Generating {total} samples ({per_voice} per voice × {len(voices)} voices)")

    idx = 0
    for voice in voices:
        for _ in tqdm(range(per_voice), desc=voice):
            try:
                clean = synthesize_one(voice, args.phrase)
                augmented = augment(samples=clean, sample_rate=SAMPLE_RATE)
                out_path = OUT_DIR / f"{idx:06d}.wav"
                sf.write(out_path, augmented, SAMPLE_RATE, subtype="PCM_16")
                idx += 1
            except subprocess.CalledProcessError as e:
                print(f"[!] Piper failed for voice {voice}: {e.stderr.decode()[:200]}")
                break

    print(f"[+] Wrote {idx} samples to {OUT_DIR}")


if __name__ == "__main__":
    main()
