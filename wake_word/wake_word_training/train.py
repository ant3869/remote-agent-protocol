"""Train a custom openWakeWord-compatible wake word model.

This is a thin wrapper around openwakeword's training utilities. It expects:
    data/positive/   — WAVs of the wake phrase (run generate_positives.py)
    data/negative/   — WAVs of non-wake speech (run fetch_negatives.py)

Output:  models/<phrase_slug>.onnx

Usage:
    python train.py --phrase "hey nexus" --epochs 50
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
POS_DIR = ROOT / "data" / "positive"
NEG_DIR = ROOT / "data" / "negative"
OUT_DIR = ROOT / "models"


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phrase", required=True)
    ap.add_argument("--epochs", type=int, default=50)
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--device", default="cuda")
    args = ap.parse_args()

    if not POS_DIR.exists() or not any(POS_DIR.iterdir()):
        print(f"[!] {POS_DIR} is empty — run generate_positives.py first.")
        sys.exit(1)
    if not NEG_DIR.exists() or not any(NEG_DIR.iterdir()):
        print(f"[!] {NEG_DIR} is empty — run fetch_negatives.py first.")
        sys.exit(1)

    try:
        from openwakeword.train import train_model
    except ImportError as e:
        print("[!] openwakeword[train] not installed:", e)
        print("    pip install 'openwakeword[train]'")
        sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUT_DIR / f"{slugify(args.phrase)}.onnx"

    print(f"[+] Training '{args.phrase}' for {args.epochs} epochs on {args.device}")
    print(f"    positives: {POS_DIR}")
    print(f"    negatives: {NEG_DIR}")
    print(f"    output:    {output_path}")

    # openwakeword.train.train_model API (signature is illustrative — see
    # https://github.com/dscripka/openWakeWord/tree/main/notebooks for the
    # current canonical training notebook):
    train_model(
        positive_dir=str(POS_DIR),
        negative_dir=str(NEG_DIR),
        output_path=str(output_path),
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.lr,
        device=args.device,
    )

    print(f"\n[+] Done. Copy {output_path} next to handsfree3.py and set:")
    print(f"    WAKE_WORD = \"{slugify(args.phrase)}\"")
    print(f"    owwModel = Model(wakeword_models=[\"{output_path.name}\"], ...)")


if __name__ == "__main__":
    main()
