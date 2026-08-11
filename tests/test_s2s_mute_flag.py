import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from remote_agent_protocol import config as cfg
from remote_agent_protocol.web_gui import WebVoiceApp


class FakeSession:
    def __init__(self):
        self.muted = None

    def set_muted(self, muted):
        self.muted = muted


def test_brain_mode_cold_start_synchronizes_the_external_mute_flag(monkeypatch, tmp_path):
    flag = tmp_path / "s2s_mic_muted.flag"
    monkeypatch.setattr(cfg, "RAP_MODE", "brain")
    monkeypatch.setattr(cfg, "S2S_MIC_MUTE_FILE", str(flag))

    app = WebVoiceApp()

    assert app._muted is True
    assert flag.read_text(encoding="utf-8").strip() == "muted"


def test_brain_health_rejects_an_unsynchronized_startup_mute(monkeypatch):
    monkeypatch.setattr(cfg, "RAP_MODE", "brain")
    app = WebVoiceApp.__new__(WebVoiceApp)
    app._session_state = "ready"
    app._s2s_mute_ready = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), app._handler_class())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_address[1]}/health", timeout=5
            )
        assert caught.value.code == 503
        assert json.loads(caught.value.read())["muteReady"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_failed_external_mute_write_does_not_change_gui_or_session_state(monkeypatch):
    monkeypatch.setattr(cfg, "RAP_MODE", "brain")
    app = WebVoiceApp.__new__(WebVoiceApp)
    app._muted = False
    app._session = FakeSession()
    app._write_s2s_mute_flag = lambda _muted: False

    result = app._action_mute({"muted": True})

    assert result["ok"] is False
    assert app._muted is False
    assert app._session.muted is None


def test_gui_mute_action_writes_and_removes_s2s_mute_flag(monkeypatch, tmp_path):
    flag = tmp_path / "s2s_mic_muted.flag"
    app = WebVoiceApp.__new__(WebVoiceApp)
    app._muted = False
    app._session = FakeSession()

    monkeypatch.setattr(cfg, "S2S_MIC_MUTE_FILE", str(flag))

    app._action_mute({"muted": True})
    assert app._muted is True
    assert app._session.muted is True
    assert flag.read_text(encoding="utf-8").strip() == "muted"

    app._action_mute({"muted": False})
    assert app._muted is False
    assert app._session.muted is False
    assert not flag.exists()
