# Custom Wake Word Training

Pipeline for training your own wake word model compatible with Remote Agent
Protocol's openWakeWord runtime (ONNX format).

## Why bother

The stock openWakeWord models ("alexa", "hey_mycroft", "hey_jarvis") are trained
on generic speakers in generic conditions. A model trained on **your own voice
in your own room** will trigger far more reliably with fewer false positives.

## Pipeline at a glance

1. **Pick a phrase.** 3–4 syllables, distinct phonemes, not common English. Good:
   "hey nexus", "okay frame", "bridge online". Bad: "hi", "yes", "computer".
2. **Generate ~2000 synthetic positives** via Piper TTS (many voices, augmented).
3. **Collect ~50000 negatives** — random speech that is *not* the wake phrase.
   Common Voice / LibriSpeech work. We provide a downloader.
4. **Train** a small model (~30 min on a decent GPU).
5. **Export to ONNX**, drop into the parent folder, point `handsfree3.py` at it.

## Quick start

```powershell
# 1. Create a venv specifically for training (keeps it separate from runtime)
python -m venv .venv-train
. .\.venv-train\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. Generate synthetic positive samples (~10 min on CPU)
python generate_positives.py --phrase "hey nexus" --count 2000

# 3. Download a negative dataset (one-time, ~5 GB)
python fetch_negatives.py

# 4. Train (needs CUDA GPU, ~30–60 min)
python train.py --phrase "hey nexus" --epochs 50

# 5. Output lands in models/hey_nexus.onnx
#    Copy it under wake_word/wake_models and map it in WAKE_WORD_PERSONAS_JSON
```

## Files

| File | Purpose |
|---|---|
| `generate_positives.py` | Uses Piper TTS to synthesize the wake phrase with many voices and augmentations |
| `fetch_negatives.py`    | Downloads / curates a negative-sample corpus |
| `train.py`              | Training loop — produces an ONNX model |
| `config.yaml`           | Hyperparameters in one place |
| `requirements.txt`      | Pinned training dependencies |

## Alternative: official Colab notebook

If you don't want to set up CUDA locally, openWakeWord ships a Colab notebook
that does the same thing on Google's GPUs. Look for
`automatic_model_training.ipynb` in the openwakeword repo. Trained model still
drops into `models/` here as ONNX.

## Tips

- More voices > more samples per voice. Piper has ~30 English voices — use them all.
- Record ~50 samples of yourself saying the phrase in different rooms. Mix these
  in with the synthetic positives; the model picks up your voice fingerprint fast.
- Test the trained model with `eval.py` before swapping it in. False-positive
  rate per-hour on a TV stream is the metric that matters.
