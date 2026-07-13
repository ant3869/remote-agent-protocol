from unittest import mock

from remote_agent_protocol import terminal


def _patch_guard(monkeypatch, calls, *, lock_ok=True):
    monkeypatch.setattr(
        terminal.process_guard,
        "acquire_single_instance_lock",
        lambda: lock_ok,
    )
    monkeypatch.setattr(
        terminal.process_guard, "close_previous_instance", lambda: calls.append("close")
    )
    monkeypatch.setattr(terminal.process_guard, "write_lock", lambda: calls.append("write"))
    monkeypatch.setattr(terminal.process_guard, "release_lock", lambda: calls.append("release"))
    monkeypatch.setattr(
        terminal.process_guard, "install_close_handler", lambda cb: calls.append("install_handler")
    )


def test_run_follows_process_guard_then_builds_and_runs_the_session(monkeypatch):
    calls = []
    _patch_guard(monkeypatch, calls)
    monkeypatch.setattr(
        terminal, "_build_default_session", lambda: calls.append("build") or mock.Mock()
    )
    monkeypatch.setattr(
        terminal.dashboard, "stop_loaded_models", lambda host: calls.append("unload") or 0
    )

    async def fake_main(session):
        calls.append("main")

    monkeypatch.setattr(terminal, "main", fake_main)

    terminal.run()

    assert calls == ["close", "write", "build", "install_handler", "main", "unload", "release"]


def test_run_releases_lock_and_unloads_models_after_failure(monkeypatch):
    calls = []
    _patch_guard(monkeypatch, calls)
    monkeypatch.setattr(terminal, "_build_default_session", lambda: mock.Mock())
    monkeypatch.setattr(
        terminal.dashboard, "stop_loaded_models", lambda host: calls.append("unload") or 0
    )

    async def failing_main(session):
        calls.append("main")
        raise RuntimeError("boom")

    monkeypatch.setattr(terminal, "main", failing_main)

    try:
        terminal.run()
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError to propagate")

    assert calls == ["close", "write", "install_handler", "main", "unload", "release"]


def test_run_refuses_a_second_instance(monkeypatch):
    calls = []
    _patch_guard(monkeypatch, calls, lock_ok=False)
    monkeypatch.setattr(terminal, "_build_default_session", lambda: mock.Mock())

    async def fake_main(session):
        calls.append("main")

    monkeypatch.setattr(terminal, "main", fake_main)

    try:
        terminal.run()
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("expected SystemExit")

    # A live sibling's PID must be left alone -- nothing past the lock check runs.
    assert calls == []


def test_close_handler_shuts_down_the_session_and_does_not_hang(monkeypatch):
    # Regression: closing the console window must shut down the *real* voice
    # session, not just make the process exit -- otherwise delegated agent
    # subprocesses and the Voicebox server are left running (see
    # process_guard.install_close_handler's docstring for the full incident).
    guard_calls: list = []
    _patch_guard(monkeypatch, guard_calls)
    calls = []
    fake_session = mock.Mock()
    fake_session.shutdown = mock.Mock(side_effect=lambda: calls.append("shutdown"))
    monkeypatch.setattr(terminal, "_build_default_session", lambda: fake_session)
    monkeypatch.setattr(terminal.dashboard, "stop_loaded_models", lambda host: 0)

    captured: list = []
    monkeypatch.setattr(
        terminal.process_guard, "install_close_handler", lambda cb: captured.append(cb)
    )

    async def fake_main(session):
        calls.append("main")

    monkeypatch.setattr(terminal, "main", fake_main)

    terminal.run()
    assert len(captured) == 1

    # run() has already completed, so its finally block already set
    # cleanup_done -- invoking the close callback now must return immediately
    # (not block for its 10s safety cap) while still calling shutdown().
    captured[0]()
    assert calls == ["main", "shutdown"]
