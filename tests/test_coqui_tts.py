import asyncio
import json
from pathlib import Path

import numpy as np

from remote_agent_protocol import config as cfg
from remote_agent_protocol import coqui_tts


class FakeSynthesizer:
    output_sample_rate = 22050


class FakeTTS:
    def __init__(self, model_name="", progress_bar=True, gpu=False):
        self.model_name = model_name
        self.progress_bar = progress_bar
        self.gpu = gpu
        self.speakers = ["speaker-a"]
        self.languages = ["en"]
        self.synthesizer = FakeSynthesizer()
        self.device = "cpu"

    def to(self, device):
        self.device = device

    def tts(self, **kwargs):
        self.last_request = kwargs
        return np.array([0.0, 0.5, -0.5], dtype=np.float32)


def test_coqui_availability_detection(monkeypatch):
    monkeypatch.setattr(coqui_tts, "_import_tts_class", lambda: FakeTTS)

    status = coqui_tts.availability()

    assert status.available is True
    assert status.label == "Coqui installed"


def test_coqui_model_listing_marks_downloaded(monkeypatch, tmp_path):
    models = tmp_path / "TTS" / ".models.json"
    models.parent.mkdir()
    models.write_text(
        json.dumps({"tts_models": {"en": {"ljspeech": {"vits": {}}}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "COQUI_TTS_SOURCE_DIR", str(tmp_path))
    monkeypatch.setattr(coqui_tts, "_MODELS_CACHE", None)
    monkeypatch.setenv("TTS_HOME", str(tmp_path / "cache"))
    (Path(str(tmp_path / "cache")) / "tts" / "tts_models--en--ljspeech--vits").mkdir(parents=True)

    rows = coqui_tts.list_models(refresh=True)

    assert rows[0].id == "tts_models/en/ljspeech/vits"
    assert rows[0].installed is True


def test_coqui_synthesis_request_and_pcm(monkeypatch):
    monkeypatch.setattr(coqui_tts, "_import_tts_class", lambda: FakeTTS)
    provider = coqui_tts.CoquiTTSProvider()

    result = asyncio.run(
        provider.synthesize(
            "hello",
            {
                "model": "tts_models/en/ljspeech/vits",
                "speaker": "speaker-a",
                "language": "en",
                "device": "cpu",
            },
        )
    )

    assert result.sample_rate == 22050
    assert len(result.pcm) == 6
    assert provider.speakers == ["speaker-a"]
    assert provider.languages == ["en"]


def test_coqui_synthesis_failure_records_error(monkeypatch):
    class BrokenTTS(FakeTTS):
        def tts(self, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(coqui_tts, "_import_tts_class", lambda: BrokenTTS)
    provider = coqui_tts.CoquiTTSProvider()

    try:
        asyncio.run(provider.synthesize("hello", {"model": "tts_models/en/ljspeech/vits"}))
    except RuntimeError:
        pass

    assert "boom" in coqui_tts.status_payload()["error"]
