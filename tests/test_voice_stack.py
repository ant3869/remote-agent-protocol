"""One-command voice stack: discovery, ordering, and readiness gating."""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from remote_agent_protocol import config as cfg
from remote_agent_protocol import voice_stack


@pytest.fixture(autouse=True)
def _no_running_instance(monkeypatch):
    """Answer the launcher's single-instance guard for every test in this file.

    Whether this machine happens to be running the app is never what these
    tests are about, but it would otherwise decide their outcome.
    """
    monkeypatch.setattr(voice_stack.process_guard, "instance_is_running", lambda: False)


def test_frontend_is_found_next_to_this_repo(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "S2S_HOME", "")
    monkeypatch.setattr(voice_stack, "REPO_ROOT", tmp_path / "remote-agent-protocol")
    sibling = tmp_path / "speech-to-speech"
    sibling.mkdir()

    assert voice_stack.resolve_s2s_home() == sibling


def test_explicit_s2s_home_wins_over_the_sibling(monkeypatch, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setattr(cfg, "S2S_HOME", str(elsewhere))

    assert voice_stack.resolve_s2s_home() == elsewhere


def test_missing_frontend_reports_none_rather_than_a_bad_path(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "S2S_HOME", str(tmp_path / "nope"))

    assert voice_stack.resolve_s2s_home() is None


def test_incomplete_frontend_checkout_is_itemized(tmp_path):
    (tmp_path / "run-rap-brain.cmd").write_text("", encoding="utf-8")

    missing = voice_stack.missing_frontend_pieces(tmp_path)

    assert missing == ["run-realtime-client.cmd", ".venv"]


def test_complete_frontend_checkout_reports_nothing_missing(tmp_path):
    for name in ("run-rap-brain.cmd", "run-realtime-client.cmd"):
        (tmp_path / name).write_text("", encoding="utf-8")
    (tmp_path / ".venv").mkdir()

    assert voice_stack.missing_frontend_pieces(tmp_path) == []


def test_dynamic_stack_ports_are_distinct(monkeypatch):
    ports = iter([41001, 41001, 41002])
    monkeypatch.setattr(voice_stack, "free_loopback_port", lambda: next(ports))

    assert voice_stack.select_stack_ports() == (41001, 41002)


def test_explicit_stack_ports_reach_every_stage(tmp_path):
    stages = voice_stack.build_stages(tmp_path, bridge_port=41001, ws_port=41002)
    server_args = stages[1].args
    client_args = stages[2].args

    assert server_args[server_args.index("--ws_port") + 1] == "41002"
    assert (
        server_args[server_args.index("--responses_api_base_url") + 1]
        == "http://127.0.0.1:41001/v1"
    )
    assert client_args[client_args.index("--port") + 1] == "41002"
    assert (
        client_args[client_args.index("--avatar-envelope-url") + 1]
        == "http://127.0.0.1:41001/api/avatar-envelope"
    )


def test_child_environment_advertises_selected_dynamic_ports(monkeypatch):
    monkeypatch.setenv("S2S_BRIDGE_PORT", "old")
    monkeypatch.setenv("S2S_WS_PORT", "old")

    env = voice_stack.child_env(
        bridge_port=41001, ws_port=41002, bridge_api_key="per-launch-secret"
    )

    assert env["RAP_MODE"] == "brain"
    assert env["S2S_BRIDGE_PORT"] == "41001"
    assert env["S2S_WS_PORT"] == "41002"
    assert env["S2S_BRIDGE_API_KEY"] == "per-launch-secret"


def test_stages_start_brain_then_server_then_client(tmp_path):
    stages = voice_stack.build_stages(tmp_path)

    assert [stage.name for stage in stages] == [
        "RAP brain + GUI",
        "speech-to-speech server",
        "speech-to-speech client",
    ]
    # Every stage owns a real boundary: brain health, server listener, then
    # client audio streams plus a confirmed realtime WebSocket session.
    assert all(stage.ready is not None for stage in stages)


def test_client_readiness_uses_a_rap_owned_file(tmp_path):
    client = voice_stack.build_stages(tmp_path)[2]

    assert client.ready_file == (cfg.DATA_DIR / "s2s_client.ready").resolve()
    assert client.args[client.args.index("--ready-file") + 1] == str(client.ready_file)


def test_client_ready_file_requires_valid_ready_json(monkeypatch, tmp_path):
    monkeypatch.setattr(voice_stack, "process_is_running", lambda pid: pid == 123)
    path = tmp_path / "client.ready"
    probe = voice_stack.file_ready(path)

    assert probe() is False
    path.write_text("garbage", encoding="utf-8")
    assert probe() is False
    path.write_text('{"ready": false, "pid": 123}', encoding="utf-8")
    assert probe() is False
    path.write_text('{"ready": true, "pid": 123}', encoding="utf-8")
    assert probe() is True


def test_client_ready_file_rejects_a_dead_pid(monkeypatch, tmp_path):
    path = tmp_path / "client.ready"
    path.write_text('{"ready": true, "pid": 123}', encoding="utf-8")
    monkeypatch.setattr(voice_stack, "process_is_running", lambda _pid: False)

    assert voice_stack.file_ready(path)() is False


def test_spawn_removes_a_stale_ready_file(monkeypatch, tmp_path):
    ready_file = tmp_path / "client.ready"
    ready_file.write_text('{"ready": true, "pid": 1}', encoding="utf-8")
    stage = voice_stack.Stage(
        name="client",
        args=["client"],
        cwd=tmp_path,
        ready_file=ready_file,
    )
    observed = []

    def popen(*_args, **_kwargs):
        observed.append(ready_file.exists())
        return _FakeProcess()

    monkeypatch.setattr(voice_stack.subprocess, "Popen", popen)

    voice_stack._spawn(stage, {})

    assert observed == [False]


def test_server_start_allows_time_for_model_loading(tmp_path):
    stages = voice_stack.build_stages(tmp_path)

    assert stages[1].ready_timeout > stages[0].ready_timeout


def test_children_are_forced_into_brain_mode(monkeypatch):
    monkeypatch.setenv("RAP_MODE", "full")

    assert voice_stack.child_env()["RAP_MODE"] == "brain"


def test_port_probe_tracks_a_real_listener():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    probe = voice_stack.port_open("127.0.0.1", port)
    try:
        assert probe() is True
    finally:
        listener.close()

    assert probe() is False


def test_realtime_probe_waits_for_its_pipeline_slot_to_return_idle(monkeypatch):
    websocket_calls = []

    class Connection:
        def __enter__(self):
            websocket_calls.append("connect")
            return self

        def __exit__(self, *_args):
            return None

        def recv(self, timeout=None):
            assert timeout == 2.0
            return '{"type":"session.created","session":{"id":"session-1"}}'

    class Response:
        def __init__(self, state):
            self.state = state
            self.status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            in_use = 0 if self.state == "idle" else 1
            return json.dumps(
                {"in_use": in_use, "units": [{"index": 0, "state": self.state}]}
            ).encode()

    pool_states = iter(["draining", "idle"])
    monkeypatch.setattr(voice_stack, "websocket_connect", lambda *_a, **_k: Connection())
    monkeypatch.setattr(
        voice_stack.urllib.request,
        "urlopen",
        lambda *_a, **_k: Response(next(pool_states)),
    )
    probe = voice_stack.realtime_ready(
        "ws://127.0.0.1:8767/v1/realtime",
        "http://127.0.0.1:8767/v1/pool",
    )

    assert probe() is False
    assert probe() is True
    assert websocket_calls == ["connect"]


def test_http_probe_tracks_a_real_endpoint():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    probe = voice_stack.http_ok(f"http://127.0.0.1:{port}/health")
    try:
        assert probe() is True
    finally:
        server.shutdown()
        server.server_close()

    assert probe() is False


def test_run_stack_uses_dynamic_ports_instead_of_legacy_collisions(monkeypatch, tmp_path):
    home = _kokoro_frontend(tmp_path, launcher_text="--tts pocket")
    monkeypatch.setattr(voice_stack, "resolve_s2s_home", lambda: home)
    monkeypatch.setattr(voice_stack, "select_stack_ports", lambda: (41001, 41002))
    monkeypatch.setattr(
        voice_stack,
        "occupied_ports",
        lambda bridge_port=None, ws_port=None: [],
    )
    monkeypatch.setattr(voice_stack, "ensure_llm_backend", lambda: None)
    spawned = []

    def spawn(stage, env):
        spawned.append((stage, env))
        return _FakeProcess(exit_code=1)

    monkeypatch.setattr(voice_stack, "_spawn", spawn)

    assert voice_stack.run_stack() == 1
    assert spawned[0][1]["S2S_BRIDGE_PORT"] == "41001"
    assert spawned[0][1]["S2S_WS_PORT"] == "41002"


def _ollama_that_starts_on_demand(monkeypatch, *, tags_once_started):
    """Report Ollama as down until start_ollama() is called, then serving."""
    state = {"running": False, "started_on": None}

    def tags(*_args):
        return list(tags_once_started) if state["running"] else None

    def start(address, timeout=90.0):
        state["running"] = True
        state["started_on"] = address
        return True

    monkeypatch.setattr(voice_stack, "ollama_tags", tags)
    monkeypatch.setattr(voice_stack, "start_ollama", start)
    return state


def test_a_stopped_ollama_is_started_rather_than_reported(monkeypatch):
    state = _ollama_that_starts_on_demand(monkeypatch, tags_once_started=[f"{cfg.LLM_MODEL}:latest"])

    assert voice_stack.ensure_llm_backend() is None
    assert state["started_on"] == "localhost:11434"


def test_an_ollama_that_will_not_start_is_reported(monkeypatch):
    monkeypatch.setattr(voice_stack, "ollama_tags", lambda *_args: None)
    monkeypatch.setattr(voice_stack, "start_ollama", lambda *_args, **_kwargs: False)

    problem = voice_stack.ensure_llm_backend()

    assert problem is not None and "Could not start Ollama" in problem


def test_a_remote_ollama_is_never_started_locally(monkeypatch):
    monkeypatch.setattr(cfg, "OLLAMA_HOST", "http://192.168.1.50:11434")
    monkeypatch.setattr(voice_stack, "ollama_tags", lambda *_args: None)
    monkeypatch.setattr(
        voice_stack,
        "start_ollama",
        lambda *_args, **_kwargs: pytest.fail("started a server for another machine"),
    )

    assert "not this machine" in (voice_stack.ensure_llm_backend() or "")


def test_a_custom_local_port_is_where_ollama_gets_bound(monkeypatch):
    monkeypatch.setattr(cfg, "OLLAMA_HOST", "http://127.0.0.1:11500")

    assert voice_stack.local_ollama_address() == "127.0.0.1:11500"


def test_an_unregistered_chat_model_is_reported_before_launching(monkeypatch):
    monkeypatch.setattr(voice_stack, "ollama_tags", lambda *_args: ["something-else:latest"])

    problem = voice_stack.ensure_llm_backend()

    assert problem is not None and cfg.LLM_MODEL in problem


def test_a_serving_ollama_with_the_chat_model_is_left_alone(monkeypatch):
    monkeypatch.setattr(voice_stack, "ollama_tags", lambda *_args: [f"{cfg.LLM_MODEL}:latest"])
    monkeypatch.setattr(
        voice_stack,
        "start_ollama",
        lambda *_args, **_kwargs: pytest.fail("restarted an Ollama that was already serving"),
    )

    assert voice_stack.ensure_llm_backend() is None


_DEVICE_TABLE = {
    "default": [1, 6],
    "devices": [
        {"index": 1, "name": "Microphone (Lenovo Performance Audio)", "inputs": 2, "outputs": 0},
        {"index": 6, "name": "Speakers (Realtek(R) Audio)", "inputs": 0, "outputs": 8},
        {"index": 46, "name": "Microphone (Steam Streaming Microphone)", "inputs": 8, "outputs": 0},
    ],
}


def test_an_unconfigured_microphone_falls_back_to_the_system_default():
    assert voice_stack.resolve_audio_device(_DEVICE_TABLE, "", capture=True) == 1


def test_a_microphone_is_matched_by_name_not_by_remembered_index():
    index = voice_stack.resolve_audio_device(_DEVICE_TABLE, "steam streaming", capture=True)

    assert index == 46


def test_a_valid_literal_index_is_honoured():
    assert voice_stack.resolve_audio_device(_DEVICE_TABLE, "46", capture=True) == 46


def test_a_literal_index_must_exist_and_support_the_requested_direction():
    assert voice_stack.resolve_audio_device(_DEVICE_TABLE, "999", capture=True) is None
    assert voice_stack.resolve_audio_device(_DEVICE_TABLE, "6", capture=True) is None
    assert voice_stack.resolve_audio_device(_DEVICE_TABLE, "1", capture=False) is None


def test_a_named_device_that_is_gone_resolves_to_nothing():
    assert voice_stack.resolve_audio_device(_DEVICE_TABLE, "yeti", capture=True) is None


def test_a_microphone_is_never_matched_against_an_output_only_device():
    assert voice_stack.resolve_audio_device(_DEVICE_TABLE, "realtek", capture=True) is None


def test_the_client_is_told_which_devices_to_open(tmp_path):
    client = voice_stack.build_stages(tmp_path, input_device=1, output_device=6)[2].args

    assert client[client.index("--input-device") + 1] == "1"
    assert client[client.index("--output-device") + 1] == "6"


def test_unresolved_devices_leave_the_frontends_own_choice_alone(tmp_path):
    client = voice_stack.build_stages(tmp_path)[2].args

    assert "--input-device" not in client and "--output-device" not in client


def test_speakers_are_left_alone_unless_explicitly_configured(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "S2S_INPUT_DEVICE", "")
    monkeypatch.setattr(cfg, "S2S_OUTPUT_DEVICE", "")
    monkeypatch.setattr(voice_stack, "audio_devices", lambda _home: _DEVICE_TABLE)

    assert voice_stack.select_audio_devices(tmp_path) == (1, None)


def test_configured_speakers_are_resolved_too(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "S2S_INPUT_DEVICE", "lenovo")
    monkeypatch.setattr(cfg, "S2S_OUTPUT_DEVICE", "realtek")
    monkeypatch.setattr(voice_stack, "audio_devices", lambda _home: _DEVICE_TABLE)

    assert voice_stack.select_audio_devices(tmp_path) == (1, 6)


def test_an_unreadable_device_table_defers_to_the_frontend(monkeypatch, tmp_path):
    monkeypatch.setattr(voice_stack, "audio_devices", lambda _home: None)

    assert voice_stack.select_audio_devices(tmp_path) == (None, None)


def test_run_stack_refuses_to_launch_without_a_reachable_brain_backend(monkeypatch, tmp_path):
    home = _kokoro_frontend(tmp_path, launcher_text="--tts pocket")
    monkeypatch.setattr(voice_stack, "resolve_s2s_home", lambda: home)
    monkeypatch.setattr(voice_stack, "ensure_llm_backend", lambda: "Ollama is not answering")
    spawned = []
    monkeypatch.setattr(voice_stack, "_spawn", lambda *a: spawned.append(a))

    assert voice_stack.run_stack() == 2
    assert spawned == []


def test_a_failed_stage_reports_the_tail_of_its_own_log(monkeypatch, tmp_path):
    monkeypatch.setattr(voice_stack, "REPO_ROOT", tmp_path)
    log = tmp_path / "logs" / "speech-to-speech-server.log"
    log.parent.mkdir()
    log.write_text("loading models\nopenai.InternalServerError: 500\n", encoding="utf-8")

    excerpt = voice_stack.log_excerpt("speech-to-speech server")

    assert "openai.InternalServerError: 500" in excerpt


def test_a_missing_stage_log_still_points_at_where_it_should_be(monkeypatch, tmp_path):
    monkeypatch.setattr(voice_stack, "REPO_ROOT", tmp_path)

    assert "speech-to-speech-server.log" in voice_stack.log_excerpt("speech-to-speech server")


def test_run_stack_explains_a_missing_frontend(monkeypatch):
    monkeypatch.setattr(voice_stack, "resolve_s2s_home", lambda: None)
    spawned = []
    monkeypatch.setattr(voice_stack, "_spawn", lambda *a: spawned.append(a))

    assert voice_stack.run_stack() == 2
    assert spawned == []


class _FakeProcess:
    def __init__(self, exit_code=None):
        self.returncode = exit_code
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode


def test_a_stage_that_exits_while_publishing_ready_is_not_accepted(tmp_path):
    stage = voice_stack.Stage(
        name="client",
        args=[],
        cwd=tmp_path,
        ready=lambda: True,
    )

    class ExitsDuringProbe(_FakeProcess):
        def __init__(self):
            super().__init__()
            self.polls = iter([None, 1])

        def poll(self):
            self.returncode = next(self.polls)
            return self.returncode

    assert voice_stack._await_ready(stage, ExitsDuringProbe()) is False


def test_a_stage_that_dies_before_ready_stops_the_launch(monkeypatch, tmp_path):
    for name in ("run-rap-brain.cmd", "run-realtime-client.cmd"):
        (tmp_path / name).write_text("", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    monkeypatch.setattr(voice_stack, "resolve_s2s_home", lambda: tmp_path)
    # Independent of whether a real stack happens to be running on this machine.
    monkeypatch.setattr(voice_stack, "occupied_ports", lambda *_args: [])
    monkeypatch.setattr(voice_stack, "ensure_llm_backend", lambda: None)

    spawned = []

    def fake_spawn(stage, env):
        # The brain never comes up, so nothing downstream should be launched.
        process = _FakeProcess(exit_code=1)
        spawned.append(stage.name)
        return process

    monkeypatch.setattr(voice_stack, "_spawn", fake_spawn)

    assert voice_stack.run_stack() == 1
    assert spawned == ["RAP brain + GUI"]


def test_shutdown_uses_soft_stop_before_force(monkeypatch, tmp_path):
    calls = []
    stage = voice_stack.Stage(
        name="brain",
        args=[],
        cwd=tmp_path,
        stop=lambda: calls.append("soft-stop") or True,
    )
    process = _FakeProcess()

    def wait(timeout=None):
        calls.append(("wait", timeout))
        process.returncode = 0
        return 0

    process.wait = wait
    monkeypatch.setattr(
        voice_stack, "_force_process_tree", lambda _process: calls.append("force")
    )

    voice_stack._shutdown([(stage, process)])

    assert calls == ["soft-stop", ("wait", 10)]
    assert process.terminated is False


def test_windows_force_stop_targets_the_entire_process_tree(monkeypatch):
    calls = []

    class Process:
        pid = 4242

        def terminate(self):
            raise AssertionError("taskkill tree path was not used")

    class Result:
        returncode = 0

    monkeypatch.setattr(voice_stack.sys, "platform", "win32")
    monkeypatch.setattr(
        voice_stack.subprocess,
        "run",
        lambda args, **kwargs: calls.append((args, kwargs)) or Result(),
    )

    voice_stack._force_process_tree(Process())

    assert calls[0][0] == ["taskkill", "/PID", "4242", "/T", "/F"]
    assert calls[0][1]["timeout"] == 15


def test_shutdown_force_kills_tree_when_no_soft_stop_exists(monkeypatch, tmp_path):
    stage = voice_stack.Stage(name="server", args=[], cwd=tmp_path)
    process = _FakeProcess()
    forced = []
    monkeypatch.setattr(voice_stack, "_force_process_tree", forced.append)

    voice_stack._shutdown([(stage, process)])

    assert forced == [process]
    assert process.terminated is False


def test_shutdown_stops_children_in_reverse_order(monkeypatch, tmp_path):
    stages = [
        voice_stack.Stage(name=f"stage-{index}", args=[], cwd=tmp_path)
        for index in range(3)
    ]
    processes = [_FakeProcess() for _ in stages]
    forced = []
    monkeypatch.setattr(voice_stack, "_force_process_tree", forced.append)

    voice_stack._shutdown(list(zip(stages, processes)))

    assert forced == list(reversed(processes))


@pytest.mark.parametrize("already_exited", [0, 3])
def test_shutdown_leaves_already_exited_children_alone(tmp_path, already_exited):
    stage = voice_stack.build_stages(tmp_path)[0]
    process = _FakeProcess(exit_code=already_exited)

    voice_stack._shutdown([(stage, process)])

    assert process.terminated is False


def test_server_receives_authoritative_network_and_brain_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "S2S_WS_PORT", 9777)
    monkeypatch.setattr(cfg, "S2S_BRIDGE_PORT", 9888)
    monkeypatch.setattr(cfg, "S2S_BRIDGE_API_KEY", "secret")
    monkeypatch.setattr(cfg, "S2S_BRIDGE_MODEL", "rap-test-model")

    args = voice_stack.build_stages(tmp_path)[1].args

    assert args[args.index("--ws_host") + 1] == "127.0.0.1"
    assert args[args.index("--ws_port") + 1] == "9777"
    assert args[args.index("--responses_api_base_url") + 1] == "http://127.0.0.1:9888/v1"
    assert args[args.index("--responses_api_api_key") + 1] == "secret"
    assert args[args.index("--model_name") + 1] == "rap-test-model"


def test_server_streams_sentence_by_sentence_for_realtime_speech(tmp_path):
    # The upstream default batches 3 sentences before TTS starts speaking;
    # a voice assistant that waits three sentences is not a voice assistant.
    args = voice_stack.build_stages(tmp_path)[1].args

    assert args[args.index("--stream_batch_sentences") + 1] == "1"


def test_client_receives_a_timing_file_for_latency_forensics(tmp_path):
    args = voice_stack.build_stages(tmp_path)[2].args

    expected = str((cfg.DATA_DIR / "s2s_turn_timings.jsonl").resolve())
    assert args[args.index("--timing-file") + 1] == expected


def test_each_stage_logs_to_its_own_file():
    path = voice_stack.stage_log_path("speech-to-speech server")

    assert path.name == "speech-to-speech-server.log"
    assert path.parent.name == "logs"


def test_client_receives_the_announce_file_for_agent_summaries(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "S2S_ANNOUNCE_FILE", str(tmp_path / "announce.json"))

    args = voice_stack.build_stages(tmp_path)[2].args

    assert args[args.index("--announce-file") + 1] == str(tmp_path / "announce.json")


def test_client_receives_authoritative_paths_not_the_scripts_hardcoded_ones(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "S2S_MIC_MUTE_FILE", str(tmp_path / "mute.flag"))
    monkeypatch.setattr(cfg, "S2S_VOICE_FILE", str(tmp_path / "voice.txt"))
    monkeypatch.setattr(cfg, "S2S_BRIDGE_PORT", 9999)
    monkeypatch.setattr(cfg, "S2S_WS_PORT", 9777)
    monkeypatch.setattr(cfg, "S2S_BRIDGE_API_KEY", "secret")

    args = voice_stack.build_stages(tmp_path)[2].args

    assert args[args.index("--host") + 1] == "127.0.0.1"
    assert args[args.index("--port") + 1] == "9777"
    assert args[args.index("--external-voice-file") + 1] == str(tmp_path / "voice.txt")
    assert args[args.index("--external-mute-file") + 1] == str(tmp_path / "mute.flag")
    assert args[args.index("--avatar-envelope-url") + 1] == "http://127.0.0.1:9999/api/avatar-envelope"
    assert args[args.index("--avatar-envelope-api-key") + 1] == "secret"
    assert args[args.index("--input-state-url") + 1] == "http://127.0.0.1:9999/api/input-state"
    assert args[args.index("--input-state-api-key") + 1] == "secret"


def _kokoro_frontend(tmp_path, launcher_text="--tts kokoro"):
    (tmp_path / "run-rap-brain.cmd").write_text(launcher_text, encoding="utf-8")
    (tmp_path / "run-realtime-client.cmd").write_text("", encoding="utf-8")
    venv = tmp_path / ".venv/Scripts"
    venv.mkdir(parents=True)
    return tmp_path


def test_kokoro_requirements_are_skipped_for_other_tts_backends(tmp_path):
    _kokoro_frontend(tmp_path, launcher_text="--tts pocket")

    assert voice_stack.missing_kokoro_requirements(tmp_path) == []


def test_kokoro_requirements_are_skipped_when_no_venv_python(tmp_path):
    _kokoro_frontend(tmp_path)

    # No python.exe to probe with, so report nothing rather than a false alarm.
    assert voice_stack.missing_kokoro_requirements(tmp_path) == []


def test_kokoro_gaps_are_reported_from_the_frontend_venv(tmp_path, monkeypatch):
    home = _kokoro_frontend(tmp_path)
    (home / ".venv/Scripts/python.exe").write_text("", encoding="utf-8")

    class Result:
        stdout = "kokoro en_core_web_sm"

    monkeypatch.setattr(voice_stack.subprocess, "run", lambda *a, **k: Result())

    assert voice_stack.missing_kokoro_requirements(home) == ["kokoro", "en_core_web_sm"]


def test_a_satisfied_kokoro_venv_reports_no_gaps(tmp_path, monkeypatch):
    home = _kokoro_frontend(tmp_path)
    (home / ".venv/Scripts/python.exe").write_text("", encoding="utf-8")

    class Result:
        stdout = " "

    monkeypatch.setattr(voice_stack.subprocess, "run", lambda *a, **k: Result())

    assert voice_stack.missing_kokoro_requirements(home) == []


def test_missing_spacy_model_stops_the_launch_with_the_real_cause(tmp_path, monkeypatch, caplog):
    home = _kokoro_frontend(tmp_path)
    (home / ".venv/Scripts/python.exe").write_text("", encoding="utf-8")
    monkeypatch.setattr(voice_stack, "resolve_s2s_home", lambda: home)
    monkeypatch.setattr(voice_stack, "missing_kokoro_requirements", lambda h: ["en_core_web_sm"])
    spawned = []
    monkeypatch.setattr(voice_stack, "_spawn", lambda *a: spawned.append(a))

    assert voice_stack.run_stack() == 2
    assert spawned == []


def test_occupied_ports_names_whatever_is_already_listening(monkeypatch):
    monkeypatch.setattr(cfg, "S2S_BRIDGE_PORT", 8788)
    monkeypatch.setattr(cfg, "S2S_WS_PORT", 8767)
    monkeypatch.setattr(
        voice_stack,
        "port_open",
        lambda host, port: lambda: port == 8767,
    )

    assert voice_stack.occupied_ports() == [("speech-to-speech server", 8767)]


def test_a_free_machine_reports_no_occupied_ports(monkeypatch):
    monkeypatch.setattr(
        voice_stack,
        "port_open",
        lambda host, port: lambda: False,
    )

    assert voice_stack.occupied_ports() == []


def test_a_leftover_run_is_closed_instead_of_blocking_the_launch(monkeypatch, tmp_path):
    # The common case behind a held lock is a crashed run whose process is still
    # sitting there. The app has always reaped that on its own next launch, so
    # refusing here would turn a self-healing situation into a dead end.
    home = _kokoro_frontend(tmp_path, launcher_text="--tts pocket")
    monkeypatch.setattr(voice_stack, "resolve_s2s_home", lambda: home)
    monkeypatch.setattr(voice_stack, "ensure_llm_backend", lambda: None)
    monkeypatch.setattr(voice_stack.process_guard, "instance_is_running", lambda: True)
    reclaimed = []
    monkeypatch.setattr(
        voice_stack.process_guard,
        "reclaim_instance_slot",
        lambda: reclaimed.append(True) or True,
    )
    spawned = []
    monkeypatch.setattr(
        voice_stack, "_spawn", lambda stage, env: spawned.append(stage.name) or _FakeProcess(1)
    )

    voice_stack.run_stack()

    assert reclaimed == [True]
    assert spawned == ["RAP brain + GUI"], "the launch proceeds once the slot is free"


def test_a_second_launch_refuses_instead_of_duplicating(monkeypatch, tmp_path):
    # Each launch picks ephemeral ports, so a port probe can only ever report
    # "free"; the app's own single-instance lock is what knows it is up.
    home = _kokoro_frontend(tmp_path, launcher_text="--tts pocket")
    monkeypatch.setattr(voice_stack, "resolve_s2s_home", lambda: home)
    monkeypatch.setattr(voice_stack.process_guard, "instance_is_running", lambda: True)
    # A slot that cannot be reclaimed means a genuinely running app.
    monkeypatch.setattr(voice_stack.process_guard, "reclaim_instance_slot", lambda: False)
    spawned = []
    monkeypatch.setattr(voice_stack, "_spawn", lambda *a: spawned.append(a))

    assert voice_stack.run_stack() == 2
    assert spawned == []


def test_a_clear_machine_proceeds_past_the_instance_guard(monkeypatch, tmp_path):
    home = _kokoro_frontend(tmp_path, launcher_text="--tts pocket")
    monkeypatch.setattr(voice_stack, "resolve_s2s_home", lambda: home)
    monkeypatch.setattr(voice_stack, "ensure_llm_backend", lambda: None)
    spawned = []

    def fake_spawn(stage, env):
        spawned.append(stage.name)
        return _FakeProcess(exit_code=1)

    monkeypatch.setattr(voice_stack, "_spawn", fake_spawn)

    voice_stack.run_stack()
    assert spawned == ["RAP brain + GUI"]
