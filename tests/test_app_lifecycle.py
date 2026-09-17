"""Desktop closure, startup rollback, and reopening without duplicate processes."""

import asyncio
import io
import json
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import AsyncMock, Mock

import pytest

from remote_agent_protocol import process_guard, ui_lifecycle, voice_stack, web_gui
from remote_agent_protocol.brain_adapter import BrainSessionAdapter
from remote_agent_protocol.personas import PERSONAS
from remote_agent_protocol.session import VoiceSession


@pytest.fixture(autouse=True)
def isolated_desktop(monkeypatch):
    monkeypatch.setattr(web_gui.cfg, "RAP_MODE", "full")


def test_last_tab_close_waits_for_reload_and_other_tabs(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(ui_lifecycle.time, "monotonic", lambda: now[0])
    tabs = ui_lifecycle.UITabs(grace_seconds=15)
    assert not tabs.should_stop()  # No browser ever opened (manual/headless access).
    tabs.update("one", 1, True)
    tabs.update("two", 1, True)
    tabs.update("one", 2, False)
    now[0] = 100
    assert not tabs.should_stop()
    tabs.update("two", 2, False)
    now[0] = 110
    assert not tabs.should_stop()
    tabs.update("reload", 1, True)
    now[0] = 1000
    assert not tabs.should_stop()  # Hidden/suspended browser is not a departure.
    tabs.update("reload", 2, False)
    now[0] = 1016
    assert tabs.should_stop()


def test_delayed_open_cannot_undo_a_tab_close(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(ui_lifecycle.time, "monotonic", lambda: now[0])
    tabs = ui_lifecycle.UITabs(grace_seconds=1)
    tabs.update("tab", 2, False)
    tabs.update("tab", 1, True)
    now[0] = 2
    assert tabs.should_stop()
    tabs.update("tab", 3, True)  # Back/forward restore.
    assert not tabs.should_stop()


@pytest.mark.parametrize("matches", [True, False])
def test_saved_endpoint_requires_matching_launch_identity(tmp_path, monkeypatch, matches):
    path = tmp_path / "endpoint.json"
    process_guard.write_endpoint(12345, "this-launch", path)
    record = json.loads(path.read_text())
    if not matches:
        record["instance_id"] = "different-launch"
    opener = Mock()
    opener.open.return_value = io.BytesIO(json.dumps(record).encode())
    monkeypatch.setattr(process_guard.urllib.request, "build_opener", lambda *_: opener)
    expected = "http://127.0.0.1:12345" if matches else None
    assert process_guard.existing_instance_url(path) == expected


def test_second_gui_launch_reopens_without_starting_services(monkeypatch):
    monkeypatch.setattr(process_guard, "acquire_single_instance_lock", lambda: False)
    monkeypatch.setattr(process_guard, "existing_instance_url", lambda: "http://127.0.0.1:12345")
    opened = Mock()
    app = Mock()
    monkeypatch.setattr(web_gui.webbrowser, "open", opened)
    monkeypatch.setattr(web_gui, "WebVoiceApp", app)
    web_gui.run()
    opened.assert_called_once_with("http://127.0.0.1:12345")
    app.assert_not_called()


def test_second_stack_launch_reopens_before_startup_checks(monkeypatch):
    monkeypatch.setattr(process_guard, "instance_is_running", lambda: True)
    monkeypatch.setattr(process_guard, "existing_instance_url", lambda: "http://127.0.0.1:12345")
    monkeypatch.setattr(voice_stack.webbrowser, "open", Mock())
    preflight = Mock(side_effect=AssertionError("must not start services"))
    monkeypatch.setattr(voice_stack, "resolve_s2s_home", preflight)
    assert voice_stack.run_stack() == 0
    preflight.assert_not_called()


def test_gui_startup_failure_still_closes_server_and_session(monkeypatch):
    app = web_gui.WebVoiceApp()
    app._stop_app = Mock()
    monkeypatch.setattr(web_gui.cfg, "RAP_MODE", "full")
    monkeypatch.setattr(process_guard, "install_close_handler", lambda *_: None)
    monkeypatch.setattr(app, "_start_session_thread", Mock(side_effect=RuntimeError("boot")))
    close = Mock()
    real_close = web_gui.ThreadingHTTPServer.server_close

    def closing(server):
        close()
        real_close(server)

    monkeypatch.setattr(web_gui.ThreadingHTTPServer, "server_close", closing)
    with pytest.raises(RuntimeError, match="boot"):
        app.run()
    app._stop_app.assert_called_once()
    close.assert_called_once()


def test_lifecycle_http_requires_token_and_quit_stops_server():
    app = web_gui.WebVoiceApp()
    server = ThreadingHTTPServer(("127.0.0.1", 0), app._handler_class())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/api/ui-lifecycle"

    def request(token, event):
        return urllib.request.Request(
            url,
            data=json.dumps({"tab": "test", "sequence": 1, "event": event}).encode(),
            headers={"Content-Type": "application/json", "X-Session-Token": token},
        )

    try:
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request("wrong", "quit"), timeout=2)
        assert error.value.code == 403
        assert thread.is_alive()
        with urllib.request.urlopen(request(app._csrf_token, "open"), timeout=2) as response:
            assert response.status == 200
        with urllib.request.urlopen(request(app._csrf_token, "quit"), timeout=2) as response:
            assert response.status == 200
        thread.join(timeout=3)
        assert not thread.is_alive()
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.asyncio
async def test_brain_startup_failure_closes_partial_services():
    adapter = BrainSessionAdapter(PERSONAS[0])
    adapter._discard_stale_announcements = Mock()
    adapter._brain.start = AsyncMock(side_effect=RuntimeError("startup failed"))
    adapter._brain.stop = AsyncMock()
    with pytest.raises(RuntimeError, match="startup failed"):
        await adapter.run()
    adapter._brain.stop.assert_awaited_once()
    assert adapter._loop is None


@pytest.mark.asyncio
async def test_shutdown_before_brain_thread_starts_is_remembered():
    adapter = BrainSessionAdapter(PERSONAS[0])
    adapter._brain.start = AsyncMock()
    adapter._brain.stop = AsyncMock()
    adapter.shutdown()
    await asyncio.wait_for(adapter.run(), timeout=1)
    adapter._brain.start.assert_not_awaited()
    adapter._brain.stop.assert_awaited_once()


def test_one_failed_stage_cleanup_does_not_skip_other_stages(monkeypatch, tmp_path):
    first = Mock()
    second = Mock()
    first.poll.return_value = second.poll.return_value = None
    stage = voice_stack.Stage("test", [], tmp_path)
    forced = Mock(side_effect=[OSError("already exiting"), None])
    monkeypatch.setattr(voice_stack, "_force_process_tree", forced)
    voice_stack._shutdown([(stage, first), (stage, second)])
    assert [call.args[0] for call in forced.call_args_list] == [second, first]
    first.wait.assert_called_once()


@pytest.mark.asyncio
async def test_voice_startup_failure_stops_other_resources(monkeypatch):
    session = VoiceSession(PERSONAS[0])
    session._worker = Mock()
    session._lifecycle_ws = Mock()
    session._lifecycle_ws.start = AsyncMock(side_effect=RuntimeError("port occupied"))
    session._lifecycle_ws.stop = AsyncMock(side_effect=RuntimeError("not started"))
    session._remotes.stop = AsyncMock()
    session._bridge.shutdown = AsyncMock()
    session._save_memory = Mock()
    monkeypatch.setattr("remote_agent_protocol.session.voicebox.stop_server", Mock())
    with pytest.raises(RuntimeError, match="port occupied"):
        await session.run()
    session._remotes.stop.assert_awaited_once()
    session._bridge.shutdown.assert_awaited_once()
    assert session._loop is None


@pytest.mark.asyncio
async def test_brain_cleanup_cancels_background_work_and_closes_http(monkeypatch):
    from remote_agent_protocol.brain import BrainSession

    brain = BrainSession(PERSONAS[0])
    task = asyncio.create_task(asyncio.Event().wait())
    brain._tasks.add(task)
    brain._lifecycle_ws = None
    brain._remotes.stop = AsyncMock(side_effect=RuntimeError("connection failed"))
    brain._bridge.shutdown = AsyncMock()
    http = Mock(close=AsyncMock())
    brain._http = http
    monkeypatch.setattr(web_gui.cfg, "MEMORY_ENABLED", False)
    await brain.stop()
    assert task.cancelled()
    brain._bridge.shutdown.assert_awaited_once()
    http.close.assert_awaited_once()
    assert brain._http is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows instance mutex")
def test_native_mutex_is_released_for_the_next_launch(tmp_path):
    code = """
import os
from pathlib import Path
from remote_agent_protocol import process_guard as guard
name = f"Local\\\\RapLifecycleTest-{os.getpid()}"
assert guard.acquire_single_instance_lock(name)
assert guard.instance_is_running(name)
guard.release_lock(Path("unused-test.pid"))
assert not guard.instance_is_running(name)
assert guard.acquire_single_instance_lock(name)
guard.release_lock(Path("unused-test.pid"))
"""
    # Import from the checkout, but isolate filesystem artifacts and the native handle.
    import os

    env = dict(os.environ, PYTHONPATH=str(web_gui._STATIC_DIR.parent.parent))
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
