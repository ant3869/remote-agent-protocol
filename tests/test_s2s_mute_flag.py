import json
import threading
import time

from remote_agent_protocol import config as cfg
from remote_agent_protocol.web_gui import WebVoiceApp


class FakeSession:
    def __init__(self):
        self.muted = None

    def set_muted(self, muted):
        self.muted = muted


def make_app(muted=False):
    app = WebVoiceApp.__new__(WebVoiceApp)
    app._muted = muted
    app._session = FakeSession()
    app._s2s_mute_ready = True
    app._s2s_mute_generation = 0
    app._s2s_mute_transition_lock = threading.Lock()
    return app


def test_mute_command_is_atomic_and_generation_tagged(monkeypatch, tmp_path):
    command = tmp_path / "mute.json"
    monkeypatch.setattr(cfg, "S2S_MIC_MUTE_FILE", str(command))
    app = make_app()

    assert app._write_s2s_mute_command(True) == 1
    assert json.loads(command.read_text(encoding="utf-8")) == {"generation": 1, "muted": True}
    assert list(tmp_path.glob("*.tmp")) == []


def test_startup_ack_reconciles_readiness_without_changing_confirmed_state(
    monkeypatch, tmp_path
):
    status = tmp_path / "mute-status.json"
    status.write_text(
        json.dumps({"generation": 42, "muted": True, "state": "ready"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "S2S_MIC_MUTE_STATUS_FILE", str(status))
    app = make_app(muted=True)
    app._s2s_mute_generation = 42
    app._s2s_mute_ready = False

    app._refresh_s2s_mute_ready()

    assert app._s2s_mute_ready is True
    assert app._muted is True
    assert app._session.muted is None


def test_matching_ack_commits_confirmed_mute(monkeypatch, tmp_path):
    command = tmp_path / "mute.json"
    status = tmp_path / "mute-status.json"
    monkeypatch.setattr(cfg, "RAP_MODE", "brain")
    monkeypatch.setattr(cfg, "S2S_MIC_MUTE_FILE", str(command))
    monkeypatch.setattr(cfg, "S2S_MIC_MUTE_STATUS_FILE", str(status))
    monkeypatch.setattr(cfg, "S2S_MIC_MUTE_ACK_TIMEOUT", 1.0)
    app = make_app()

    def acknowledge():
        while not command.exists():
            time.sleep(0.005)
        payload = json.loads(command.read_text(encoding="utf-8"))
        status.write_text(json.dumps({**payload, "state": "ready"}), encoding="utf-8")

    thread = threading.Thread(target=acknowledge)
    thread.start()
    result = app._action_mute({"muted": True})
    thread.join()

    assert result is None
    assert app._muted is True
    assert app._session.muted is True
    assert app._s2s_mute_ready is True


def test_stale_ack_times_out_and_retains_last_confirmed_state(monkeypatch, tmp_path):
    command = tmp_path / "mute.json"
    status = tmp_path / "mute-status.json"
    status.write_text(json.dumps({"generation": 0, "muted": True, "state": "ready"}))
    monkeypatch.setattr(cfg, "RAP_MODE", "brain")
    monkeypatch.setattr(cfg, "S2S_MIC_MUTE_FILE", str(command))
    monkeypatch.setattr(cfg, "S2S_MIC_MUTE_STATUS_FILE", str(status))
    monkeypatch.setattr(cfg, "S2S_MIC_MUTE_ACK_TIMEOUT", 0.08)
    app = make_app(muted=False)

    result = app._action_mute({"muted": True})

    assert result["ok"] is False
    assert app._muted is False
    assert app._session.muted is None
    assert app._s2s_mute_ready is False


def test_matching_rejection_retains_last_confirmed_state(monkeypatch, tmp_path):
    status = tmp_path / "mute-status.json"
    monkeypatch.setattr(cfg, "RAP_MODE", "brain")
    monkeypatch.setattr(cfg, "S2S_MIC_MUTE_FILE", str(tmp_path / "mute.json"))
    monkeypatch.setattr(cfg, "S2S_MIC_MUTE_STATUS_FILE", str(status))
    monkeypatch.setattr(cfg, "S2S_MIC_MUTE_ACK_TIMEOUT", 0.2)
    app = make_app(muted=True)
    app._write_s2s_mute_command = lambda _muted: 7
    status.write_text(json.dumps({"generation": 7, "muted": False, "state": "error"}))

    result = app._action_mute({"muted": False})

    assert result["ok"] is False
    assert app._muted is True
    assert app._session.muted is None
    assert app._s2s_mute_ready is False
