"""The GUI talks to either session type, so both must expose the same surface.

web_gui holds a VoiceSession in full mode and a BrainSessionAdapter in brain
mode, then calls the same methods on whichever it has. When the orchestration
panel was added, only VoiceSession grew those methods, so in brain mode
``GET /api/orchestration`` raised AttributeError -> HTTP 500 and the panel sat
on "Loading..." forever. These tests lock that drift shut.
"""

import inspect
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from remote_agent_protocol import brain as brain_module
from remote_agent_protocol import config as cfg
from remote_agent_protocol import web_gui
from remote_agent_protocol.brain_adapter import BrainSessionAdapter
from remote_agent_protocol.personas import PERSONAS
from remote_agent_protocol.session import VoiceSession

# Every method web_gui invokes on self._session for the orchestration panel.
ORCHESTRATION_SURFACE = (
    "orchestration_status",
    "check_copilot_auth",
    "set_orchestration_mode",
    "set_orchestration_quota_strategy",
    "set_persona_orchestration_override",
)


def test_brain_adapter_exposes_the_orchestration_surface():
    for name in ORCHESTRATION_SURFACE:
        assert callable(getattr(BrainSessionAdapter, name, None)), (
            f"BrainSessionAdapter is missing {name}(); /api/orchestration will 500 in brain mode"
        )


def test_voice_session_exposes_the_orchestration_surface():
    for name in ORCHESTRATION_SURFACE:
        assert callable(getattr(VoiceSession, name, None))


def test_signatures_match_between_session_types():
    for name in ORCHESTRATION_SURFACE:
        voice = inspect.signature(getattr(VoiceSession, name))
        brain = inspect.signature(getattr(BrainSessionAdapter, name))
        assert list(voice.parameters) == list(brain.parameters), (
            f"{name}() differs between VoiceSession and BrainSessionAdapter"
        )


# Methods web_gui only ever calls behind a hasattr() check, because they exist
# to serve the external realtime frontend and have no full-mode equivalent.
BRAIN_ONLY_GUARDED = {"complete_text", "stream_text"}


def _session_methods_web_gui_calls() -> set[str]:
    source = inspect.getsource(web_gui)
    # _new_session() holds the fresh session in a local before storing it, so
    # the startup calls made there are only reachable through the bare name.
    return set(
        re.findall(r"(?:self\._session|app\._session|\bsession)\.([a-z_][a-z0-9_]*)\(", source)
    )


def test_web_gui_calls_no_session_method_the_adapter_lacks():
    """Catch the next method web_gui starts calling on _session."""
    adapter_gaps = sorted(
        name
        for name in _session_methods_web_gui_calls()
        if hasattr(VoiceSession, name) and not hasattr(BrainSessionAdapter, name)
    )
    assert not adapter_gaps, (
        f"BrainSessionAdapter lacks methods web_gui calls (brain mode would 500): {adapter_gaps}"
    )


def test_web_gui_calls_no_session_method_voice_session_lacks():
    """The same drift in the other direction: brain-only calls must stay guarded."""
    session_gaps = sorted(
        name
        for name in _session_methods_web_gui_calls()
        if hasattr(BrainSessionAdapter, name)
        and not hasattr(VoiceSession, name)
        and name not in BRAIN_ONLY_GUARDED
    )
    assert not session_gaps, (
        f"VoiceSession lacks methods web_gui calls (full mode would 500): {session_gaps}"
    )


def test_brain_only_methods_are_called_behind_a_hasattr_guard():
    source = inspect.getsource(web_gui)
    for name in BRAIN_ONLY_GUARDED:
        assert f'hasattr(app._session, "{name}")' in source, (
            f"{name}() is brain-only but web_gui no longer guards the call"
        )


def _takes_the_same_call(voice, brain) -> bool:
    """Whether a call web_gui writes for VoiceSession also binds to the adapter."""
    if any(p.kind is p.VAR_KEYWORD for p in brain.parameters.values()):
        # The adapter deliberately swallows settings brain mode has no use for.
        return True
    return list(voice.parameters) == list(brain.parameters)


def test_the_whole_shared_surface_takes_the_same_arguments():
    """One call site serves both modes, so one call has to bind to both."""
    mismatched = {}
    for name in sorted(_session_methods_web_gui_calls()):
        if not (hasattr(VoiceSession, name) and hasattr(BrainSessionAdapter, name)):
            continue
        voice = inspect.signature(getattr(VoiceSession, name))
        brain = inspect.signature(getattr(BrainSessionAdapter, name))
        if not _takes_the_same_call(voice, brain):
            mismatched[name] = (str(voice), str(brain))
    assert not mismatched, f"argument drift between session types: {mismatched}"


# -- the three call paths that used to raise AttributeError in brain mode ------


@pytest.fixture
def brain_adapter(monkeypatch, tmp_path):
    """A real adapter whose external-frontend handoff files live in tmp_path."""
    monkeypatch.setattr(cfg, "S2S_VOICE_FILE", str(tmp_path / "s2s_voice.txt"))
    monkeypatch.setattr(cfg, "S2S_ANNOUNCE_FILE", str(tmp_path / "s2s_announce.json"))
    return BrainSessionAdapter(PERSONAS[0])


def _gui(session, **attrs):
    """A WebVoiceApp with only the attributes the path under test reads."""
    app = web_gui.WebVoiceApp.__new__(web_gui.WebVoiceApp)
    app._session = session
    for name, value in attrs.items():
        setattr(app, name, value)
    return app


def test_diagnostics_export_works_in_brain_mode(monkeypatch, tmp_path, brain_adapter):
    monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(web_gui.diagnostics, "audio_devices", lambda: [])
    brain_adapter.set_voice("bm_george")
    brain_adapter._brain._messages.extend(  # noqa: SLF001
        [{"role": "user", "content": "status?"}, {"role": "assistant", "content": "all quiet"}]
    )
    published = []
    app = _gui(
        brain_adapter,
        _tts_provider="external",
        _health={"ok": True, "label": "Ollama ready"},
        _tts_health={"ok": True, "label": "TTS external"},
        _latency=web_gui.dashboard.LatencyState(),
        _publish=published.append,
    )

    app._export_diagnostics()

    assert len(published) == 1, published
    message = published[0]["text"]
    assert "Diagnostics export failed" not in message
    bundle = next(iter(tmp_path.glob("*diagnostics*.txt"))).read_text(encoding="utf-8")
    assert PERSONAS[0].name in bundle
    assert "bm_george" in bundle
    assert "all quiet" in bundle


def test_export_snapshot_keys_match_voice_session(brain_adapter):
    """The diagnostics bundle formats one set of keys for both modes."""
    voice_keys = set(re.findall(r'"([a-z_]+)":', inspect.getsource(VoiceSession.export_snapshot)))
    assert set(brain_adapter.export_snapshot()) == voice_keys


def test_saving_agent_prompts_pushes_the_scope_preamble_in_brain_mode(brain_adapter):
    app = _gui(
        brain_adapter,
        _app_state=SimpleNamespace(agent_prompts={}),
        _save_state=lambda: None,
        _agent_prompt_payload=dict,
        _status_payload=dict,
    )
    preamble = "Work in {cwd}; it is scratch, not the target."

    result = app._save_agent_prompts({"prompts": {"scopePreamble": preamble}})

    assert result["ok"] is True
    assert brain_adapter._brain._bridge._scope_preamble == preamble  # noqa: SLF001


def test_saving_tts_settings_publishes_the_voice_in_brain_mode(monkeypatch, brain_adapter):
    monkeypatch.setattr(cfg, "RAP_MODE", "brain")
    app = _gui(
        brain_adapter,
        _tts_provider="kokoro",
        _voice="bf_lily",
        _persona=PERSONAS[0],
        _coqui_model="",
        _coqui_speaker="",
        _coqui_language="",
        _coqui_device="",
        _bump_catalogs=lambda: None,
        _save_state=lambda: None,
    )

    app._action_tts({"provider": "kokoro", "voice": "bm_daniel"})

    assert Path(cfg.S2S_VOICE_FILE).read_text(encoding="utf-8").strip() == "bm_daniel"


def test_local_only_tts_settings_do_not_break_the_published_voice(brain_adapter):
    """A Coqui/Cartesia pick names no Kokoro voice, so the frontend keeps the old one."""
    brain_adapter.set_voice("bf_lily")

    brain_adapter.set_tts(
        voice="p225",
        voice_backend="coqui",
        model="tts_models/en/vctk/vits",
        tts_options={"speaker": "p225"},
    )

    assert Path(cfg.S2S_VOICE_FILE).read_text(encoding="utf-8").strip() == "bf_lily"


# A finished job is spoken whenever it lands, which is routinely a turn or two
# after it was asked for. On 2026-09-15 that produced "What did what what gave
# up?" and "I don't understand" -- the relay never said what it was about.


def test_a_relayed_result_says_which_request_it_answers(monkeypatch, tmp_path, brain_adapter):
    monkeypatch.setattr(cfg, "S2S_ANNOUNCE_FILE", str(tmp_path / "announce.json"))

    brain_adapter._publish_announcement(  # noqa: SLF001
        {
            "type": "agent_job_summary",
            "agent": "openclaw",
            "job_id": "job-7",
            "status": "failed",
            "result": "",
            "summary": "openclaw exited without explaining why",
            "task": "Check the status of OpenClaw and ensure it is functioning properly",
        }
    )

    queued = sorted((tmp_path / "announce.json.queue").glob("*.json"))
    assert queued, "a finished job must still be announced"
    text = json.loads(queued[0].read_text(encoding="utf-8"))["text"]
    assert "Check the status of OpenClaw" in text, "the relay has to name the job"
    assert "openclaw exited without explaining why" in text


def test_an_announcement_without_a_task_still_goes_out(monkeypatch, tmp_path, brain_adapter):
    """Manual GUI dispatches carry no task; they must not lose their relay."""
    monkeypatch.setattr(cfg, "S2S_ANNOUNCE_FILE", str(tmp_path / "announce.json"))

    brain_adapter._publish_announcement(  # noqa: SLF001
        {"agent": "hermes", "job_id": "job-8", "status": "done", "summary": "all clear"}
    )

    queued = sorted((tmp_path / "announce.json.queue").glob("*.json"))
    assert queued
    assert "all clear" in json.loads(queued[0].read_text(encoding="utf-8"))["text"]


def test_failed_relay_preserves_cause_over_partial_result(monkeypatch, tmp_path, brain_adapter):
    monkeypatch.setattr(cfg, "S2S_ANNOUNCE_FILE", str(tmp_path / "announce.json"))
    brain_adapter._publish_announcement(
        {
            "agent": "hermes",
            "job_id": "job-q",
            "status": "failed",
            "result": "Checking settings",
            "failure_detail": "HTTP 429: quota exceeded",
        }
    )
    [queued] = (tmp_path / "announce.json.queue").glob("*.json")
    text = json.loads(queued.read_text(encoding="utf-8"))["text"]
    assert "Outcome: HTTP 429: quota exceeded" in text


def test_empty_completion_is_not_presented_as_verified_success(
    monkeypatch, tmp_path, brain_adapter
):
    monkeypatch.setattr(cfg, "S2S_ANNOUNCE_FILE", str(tmp_path / "announce.json"))
    brain_adapter._publish_announcement(
        {"agent": "hermes", "job_id": "job-empty", "status": "done"}
    )
    [queued] = (tmp_path / "announce.json.queue").glob("*.json")
    text = json.loads(queued.read_text(encoding="utf-8"))["text"]
    assert "no substantive answer" in text
    assert "unverified" in text


def test_the_brain_puts_the_task_on_the_summary_event():
    """The adapter can only name the job if the event carries it."""
    source = inspect.getsource(brain_module.BrainSession._announce_agent_job)
    assert '"task": job.task' in source, "agent_job_summary must carry the task"
