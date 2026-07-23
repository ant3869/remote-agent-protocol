"""Download a negative-sample corpus for wake-word training.

We pull random speech clips from Mozilla Common Voice (English) and write
them as 16 kHz mono WAVs into data/negative/. Any clip that happens to
*contain* the wake phrase is discarded — Common Voice has transcripts.

Usage:
    python fetch_negatives.py --count 50000
"""
import argparse
import re
from pathlib import Path

import numpy as np
import soundfile as sf
from tqdm import tqdm

try:
    from datasets import load_dataset
    import librosa
except ImportError:
    print("[!] datasets/librosa missing — run: pip install -r requirements.txt")
    raise


SAMPLE_RATE = 16000
OUT_DIR = Path(__file__).parent / "data" / "negative"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phrase", default="hey nexus",
                    help="Phrase to *exclude* from negatives (so we don't accidentally train against ourselves)")
    ap.add_argument("--count", type=int, default=50000)
    ap.add_argument("--dataset", default="mozilla-foundation/common_voice_13_0")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    phrase_re = re.compile(re.escape(args.phrase), re.IGNORECASE)

    print(f"[+] Streaming {args.dataset} (en) — will save {args.count} clips that do NOT contain '{args.phrase}'")
    ds = load_dataset(args.dataset, "en", split="train", streaming=True)

    saved = 0
    pbar = tqdm(total=args.count)
    for row in ds:
        if saved >= args.count:
            break
        text = (row.get("sentence") or "").strip()
        if not text or phrase_re.search(text):
            continue

        audio = row["audio"]
        wav = audio["array"]
        sr = audio["sampling_rate"]
        if sr != SAMPLE_RATE:
            wav = librosa.resample(wav.astype(np.float32), orig_sr=sr, target_sr=SAMPLE_RATE)

        out_path = OUT_DIR / f"{saved:06d}.wav"
        sf.write(out_path, wav.astype(np.float32), SAMPLE_RATE, subtype="PCM_16")
        saved += 1
        pbar.update(1)
    pbar.close()
    print(f"[+] Wrote {saved} negatives to {OUT_DIR}")


if __name__ == "__main__":
    main()
