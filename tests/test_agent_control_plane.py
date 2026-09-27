"""Coordinator refresh and safety-policy contracts."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from remote_agent_protocol import agent_status_reporting
from remote_agent_protocol.control_plane.adapters.base import AgentTask
from remote_agent_protocol.control_plane.adapters.cli import BridgeCliAdapter
from remote_agent_protocol.control_plane.adapters.fake import FakeAgentAdapter
from remote_agent_protocol.control_plane.models import (
    Activity,
    AgentObservation,
    AgentSnapshot,
    ControlError,
    ControlResult,
    Evidence,
    Health,
    JobHandle,
    ObservedWork,
    Presence,
    ResponseState,
    WorkOwnership,
)
from remote_agent_protocol.control_plane.service import (
    SELF_CHECK_PROMPT,
    SELF_CHECK_SENTINEL,
    AgentControlPlane,
)


def make_observation(agent_id: str) -> AgentObservation:
    """Build a successful response for a fake adapter."""
    now = datetime.now(UTC)
    return AgentObservation(
        agent_id=agent_id,
        display_name=agent_id.title(),
        harness=agent_id,
        machine="Main PC",
        presence=Presence.REACHABLE,
        activity=Activity.IDLE,
        health=Health.HEALTHY,
        capabilities=frozenset(),
        evidence=(Evidence("fake", now, "Responded."),),
        observed_at=now,
        expires_at=now + timedelta(seconds=30),
    )


@pytest.mark.parametrize(
    ("failure_kind", "expected"),
    [
        ("rate_limit", "Codex: Down (Rate limit; current)"),
        ("response_timeout", "Codex: Down (No response; current)"),
        ("unexpected_response", "Codex: Down (Unexpected response; current)"),
    ],
)
def test_response_check_failure_summary_is_plain_and_specific(
    failure_kind: str, expected: str
) -> None:
    observation = replace(
        make_observation("codex"),
        health=Health.FAILED,
        response_state=ResponseState.FAILED,
        issues=(failure_kind,),
    )

    assert agent_status_reporting.control_summary("codex", AgentSnapshot(observation)) == expected


@pytest.mark.asyncio
async def test_refresh_returns_healthy_results_when_one_adapter_hangs() -> None:
    """One timeout must not hide evidence returned by another adapter."""
    healthy = FakeAgentAdapter("codex", probe=make_observation("codex"))

    async def hang():
        await asyncio.sleep(60)
        return make_observation("hermes")

    hung = FakeAgentAdapter("hermes", probe=hang, discover=make_observation("hermes"))
    plane = AgentControlPlane(
        {"codex": healthy, "hermes": hung}, probe_timeout_secs=0.01, overall_timeout_secs=0.1
    )

    results = await plane.list_agents(refresh=True)

    assert results["codex"].observation.presence == Presence.REACHABLE
    assert results["hermes"].code == "timeout"
    assert healthy.calls == ["probe"]


@pytest.mark.asyncio
async def test_concurrent_refresh_coalesces_one_adapter_probe() -> None:
    """Duplicate refresh callers share one in-flight probe."""
    observation = make_observation("codex")

    async def delayed():
        await asyncio.sleep(0.01)
        return observation

    adapter = FakeAgentAdapter("codex", probe=delayed, discover=observation)
    plane = AgentControlPlane({"codex": adapter})

    first, second = await asyncio.gather(
        plane.get_agent_status("codex", refresh=True),
        plane.get_agent_status("codex", refresh=True),
    )

    assert first.observation == second.observation
    assert adapter.calls == ["probe"]


@pytest.mark.asyncio
async def test_rap_job_can_be_cancelled_only_through_its_own_adapter() -> None:
    """Bridge lifecycle evidence establishes the safe cancellation target."""
    adapter = FakeAgentAdapter(
        "codex",
        probe=make_observation("codex"),
        discover=make_observation("codex"),
        cancel=ControlResult(True, "codex", "job-1"),
    )
    plane = AgentControlPlane({"codex": adapter})
    await plane.ingest_bridge_event(
        {
            "type": "agent_job",
            "event": "started",
            "job_id": "job-1",
            "agent": "codex",
            "machine": "Main PC",
            "status": "running",
            "task": "Check one file",
        }
    )

    result = await plane.cancel_job("job-1")

    assert result.ok
    assert adapter.calls == ["cancel"]


@pytest.mark.asyncio
async def test_redirect_cancels_before_dispatching_to_new_agent() -> None:
    """Redirection is an ordered replacement, never a second parallel task."""
    source = FakeAgentAdapter(
        "codex",
        probe=make_observation("codex"),
        discover=make_observation("codex"),
        cancel=ControlResult(True, "codex", "job-1"),
    )
    target = FakeAgentAdapter(
        "hermes",
        probe=make_observation("hermes"),
        discover=make_observation("hermes"),
        dispatch=JobHandle("job-2", "hermes"),
    )
    plane = AgentControlPlane({"codex": source, "hermes": target})
    await plane.ingest_bridge_event(
        {
            "type": "agent_job",
            "event": "started",
            "job_id": "job-1",
            "agent": "codex",
            "status": "running",
            "task": "Check one file",
        }
    )

    result = await plane.redirect_job("job-1", "hermes", task=AgentTask("Continue the check"))

    assert result.job_id == "job-2"
    assert source.calls == ["cancel"]
    assert target.calls == ["dispatch"]


@pytest.mark.asyncio
async def test_response_check_tracks_only_the_fixed_sentinel_as_actual_evidence() -> None:
    """A CLI version probe must not be mistaken for a harness response."""
    adapter = FakeAgentAdapter(
        "codex",
        probe=make_observation("codex"),
        dispatch=JobHandle("self-check-1", "codex"),
    )
    plane = AgentControlPlane({"codex": adapter})

    started = await plane.request_response_check("codex")

    assert started == JobHandle("self-check-1", "codex")
    assert adapter.tasks == [
        AgentTask(
            SELF_CHECK_PROMPT,
            announce_start=False,
            clean_session=True,
            internal=True,
        )
    ]
    pending = await plane.get_agent_status("codex")
    assert pending.observation.response_state is ResponseState.PENDING
    waiting = asyncio.create_task(plane.wait_for_response_check("self-check-1"))
    await plane.ingest_bridge_event(
        {
            "type": "agent_job",
            "event": "finished",
            "job_id": "self-check-1",
            "agent": "codex",
            "status": "done",
            "result": SELF_CHECK_SENTINEL,
        }
    )

    confirmed = await plane.get_agent_status("codex")
    assert confirmed.observation.response_state is ResponseState.RESPONDED
    assert confirmed.observation.evidence[0].detail == (
        "RAP received the exact fixed response from this harness."
    )
    assert await waiting is ResponseState.RESPONDED


@pytest.mark.asyncio
async def test_response_check_reports_rate_limit_without_persisting_agent_output() -> None:
    adapter = FakeAgentAdapter(
        "codex",
        probe=make_observation("codex"),
        dispatch=JobHandle("self-check-2", "codex"),
    )
    plane = AgentControlPlane({"codex": adapter})
    await plane.request_response_check("codex")

    await plane.ingest_bridge_event(
        {
            "type": "agent_job",
            "event": "finished",
            "job_id": "self-check-2",
            "agent": "codex",
            "status": "failed",
            "result": "provider prose that must not become evidence",
            "failure_kind": "rate_limit",
            "failure_detail": "429 provider response",
        }
    )

    result = await plane.get_agent_status("codex")
    assert result.observation.response_state is ResponseState.FAILED
    assert result.observation.health is Health.DEGRADED
    assert "rate_limit" in result.observation.evidence[0].detail
    assert "provider prose" not in result.observation.evidence[0].detail


@pytest.mark.asyncio
async def test_rollcall_reports_the_result_of_its_new_response_check() -> None:
    adapter = FakeAgentAdapter(
        "codex",
        probe=make_observation("codex"),
        dispatch=JobHandle("self-check-rollcall", "codex"),
    )
    plane = AgentControlPlane({"codex": adapter})
    rows_task = asyncio.create_task(agent_status_reporting.collect_rollcall_rows(plane, "codex"))
    while not adapter.tasks:
        await asyncio.sleep(0)
    await plane.ingest_bridge_event(
        {
            "type": "agent_job",
            "event": "finished",
            "job_id": "self-check-rollcall",
            "agent": "codex",
            "status": "done",
            "result": SELF_CHECK_SENTINEL,
        }
    )

    rows, missing = await rows_task

    assert missing is None
    assert rows == ["Codex: Up (responded; current)"]


@pytest.mark.asyncio
async def test_a_slow_response_check_is_reported_as_running_without_blocking_the_rollcall() -> None:
    adapter = FakeAgentAdapter(
        "codex",
        probe=make_observation("codex"),
        dispatch=JobHandle("self-check-slow", "codex"),
    )
    plane = AgentControlPlane({"codex": adapter})

    rows, missing = await asyncio.wait_for(
        agent_status_reporting.collect_rollcall_rows(plane, "codex", wait_secs=0.05), 2
    )

    assert missing is None
    assert rows == ["Codex: Checking for a response now"]
    # The late answer still lands in the record for the next question.
    await plane.ingest_bridge_event(
        {
            "type": "agent_job",
            "event": "finished",
            "job_id": "self-check-slow",
            "agent": "codex",
            "status": "done",
            "result": SELF_CHECK_SENTINEL,
        }
    )
    current = await plane.get_agent_status("codex", refresh=False)
    assert current.observation.response_state is ResponseState.RESPONDED


@pytest.mark.asyncio
async def test_response_check_does_not_compete_with_a_busy_rap_job() -> None:
    busy = replace(
        make_observation("codex"),
        activity=Activity.WORKING,
        current_work=ObservedWork(
            job_id="real-job",
            ownership=WorkOwnership.RAP,
            summary="real user task",
            state=Activity.WORKING,
        ),
    )
    adapter = FakeAgentAdapter("codex", probe=busy)
    plane = AgentControlPlane({"codex": adapter})

    result = await plane.request_response_check("codex")

    assert not result.ok
    assert result.error.code == "busy"
    assert adapter.tasks == []


@pytest.mark.asyncio
async def test_concurrent_response_checks_reserve_before_probe_and_dispatch_once() -> None:
    """Two simultaneous callers cannot both launch a self-check for one agent."""
    release_probe = asyncio.Event()

    async def delayed_probe():
        await release_probe.wait()
        return make_observation("codex")

    adapter = FakeAgentAdapter(
        "codex",
        probe=delayed_probe,
        dispatch=JobHandle("self-check-race", "codex"),
    )
    plane = AgentControlPlane({"codex": adapter})

    first_task = asyncio.create_task(plane.request_response_check("codex"))
    await asyncio.sleep(0)
    second = await plane.request_response_check("codex")
    release_probe.set()
    first = await first_task

    assert first == JobHandle("self-check-race", "codex")
    assert isinstance(second, ControlResult)
    assert second.error.code == "self_check_active"
    assert adapter.calls == ["probe", "dispatch"]


@pytest.mark.asyncio
async def test_response_check_reservation_clears_after_dispatch_failure_and_terminal_event() -> (
    None
):
    failure = ControlResult(
        False,
        "codex",
        error=ControlError("dispatch_failed", "could not start", "codex"),
    )
    adapter = FakeAgentAdapter(
        "codex",
        probe=make_observation("codex"),
        dispatch=failure,
    )
    plane = AgentControlPlane({"codex": adapter})

    failed = await plane.request_response_check("codex")
    assert failed is failure
    adapter.outcomes["dispatch"] = JobHandle("self-check-next", "codex")
    started = await plane.request_response_check("codex")
    assert started == JobHandle("self-check-next", "codex")
    await plane.ingest_bridge_event(
        {
            "type": "agent_job",
            "event": "finished",
            "job_id": "self-check-next",
            "agent": "codex",
            "status": "done",
            "result": SELF_CHECK_SENTINEL,
            "internal": True,
        }
    )
    adapter.outcomes["dispatch"] = JobHandle("self-check-final", "codex")
    restarted = await plane.request_response_check("codex")

    assert restarted == JobHandle("self-check-final", "codex")
    assert adapter.calls.count("dispatch") == 3


@pytest.mark.asyncio
async def test_response_check_reservation_clears_after_dispatch_exception() -> None:
    adapter = FakeAgentAdapter(
        "codex",
        probe=make_observation("codex"),
        dispatch=RuntimeError("launch exploded"),
    )
    plane = AgentControlPlane({"codex": adapter})

    failed = await plane.request_response_check("codex")
    adapter.outcomes["dispatch"] = JobHandle("self-check-retry", "codex")
    retried = await plane.request_response_check("codex")

    assert isinstance(failed, ControlResult)
    assert failed.error.code == "dispatch_failed"
    assert retried == JobHandle("self-check-retry", "codex")


@pytest.mark.asyncio
async def test_bridge_cli_dispatch_forwards_internal_clean_session_flags() -> None:
    bridge = SimpleNamespace(
        start=AsyncMock(return_value="check-1"),
        get=lambda _job_id: None,
    )
    adapter = BridgeCliAdapter(
        "hermes",
        bridge,
        display_name="Hermes",
        machine="test",
    )

    result = await adapter.dispatch(AgentTask(SELF_CHECK_PROMPT, clean_session=True, internal=True))

    assert result == JobHandle("check-1", "hermes")
    bridge.start.assert_awaited_once_with(
        "hermes",
        SELF_CHECK_PROMPT,
        cwd=None,
        announce_start=False,
        clean_session=True,
        internal=True,
    )


@pytest.mark.asyncio
async def test_cli_probe_reports_only_discovery_without_a_version_or_health_claim(
    monkeypatch,
) -> None:
    bridge = SimpleNamespace(active_jobs=lambda _agent_id: ())
    adapter = BridgeCliAdapter("hermes", bridge, display_name="Hermes", machine="Main PC")
    adapter.executable = "hermes"
    monkeypatch.setattr(
        "remote_agent_protocol.control_plane.adapters.cli.shutil.which",
        lambda _name: "C:/hermes.exe",
    )

    observation = await adapter.probe()

    assert observation.presence is Presence.UNKNOWN
    assert observation.activity is Activity.UNKNOWN
    assert observation.health is Health.UNKNOWN
    assert "version" not in observation.evidence[0].detail.lower()
    assert "unverified" in observation.evidence[0].detail


@pytest.mark.asyncio
async def test_response_check_can_verify_an_installed_but_unverified_harness() -> None:
    unverified = replace(
        make_observation("codex"),
        presence=Presence.UNKNOWN,
        activity=Activity.UNKNOWN,
        health=Health.UNKNOWN,
    )
    adapter = FakeAgentAdapter(
        "codex", probe=unverified, dispatch=JobHandle("self-check-unverified", "codex")
    )
    plane = AgentControlPlane({"codex": adapter})

    result = await plane.request_response_check("codex")

    assert result == JobHandle("self-check-unverified", "codex")
    assert adapter.calls == ["probe", "dispatch"]


def test_unverified_discovery_never_formats_as_healthy() -> None:
    unverified = replace(
        make_observation("codex"),
        presence=Presence.UNKNOWN,
        activity=Activity.UNKNOWN,
        health=Health.UNKNOWN,
    )

    summary = agent_status_reporting.control_summary("codex", AgentSnapshot(unverified))

    assert "no verified response" in summary
    assert "healthy" not in summary


def _job_event(status: str, **extra) -> dict:
    return {
        "type": "agent_job",
        "event": "finished" if status in {"done", "failed"} else "started",
        "job_id": "real-job",
        "agent": "hermes",
        "status": status,
        "task": "Search the email",
        "action": "Searching the email",
        **extra,
    }


def _plane_with(adapter_id: str = "hermes") -> tuple[AgentControlPlane, FakeAgentAdapter]:
    adapter = FakeAgentAdapter(
        adapter_id,
        probe=make_observation(adapter_id),
        discover=make_observation(adapter_id),
        dispatch=JobHandle("self-check", adapter_id),
    )
    return AgentControlPlane({adapter_id: adapter}), adapter


@pytest.mark.asyncio
async def test_a_finished_real_job_confirms_the_harness_responds() -> None:
    plane, _ = _plane_with()
    await plane.ingest_bridge_event(
        _job_event("done", elapsed_secs=3.46, model_label="OpenAI GPT-5.5", result="Found 2.")
    )

    snapshot = await plane.get_agent_status("hermes")

    assert snapshot.observation.response_state is ResponseState.RESPONDED
    assert agent_status_reporting.control_summary("hermes", snapshot) == (
        "hermes: Up (responded in 3.5s via OpenAI GPT-5.5; current)"
    )


@pytest.mark.asyncio
async def test_a_quota_failure_on_a_real_job_marks_the_harness_down() -> None:
    plane, _ = _plane_with()
    await plane.ingest_bridge_event(_job_event("done", elapsed_secs=2.0))
    await plane.ingest_bridge_event(
        _job_event("failed", failure_kind="quota", failure_detail="insufficient_quota")
    )

    snapshot = await plane.get_agent_status("hermes")

    assert agent_status_reporting.control_summary("hermes", snapshot) == (
        "hermes: Down (Out of quota; current)"
    )


@pytest.mark.asyncio
async def test_a_task_level_failure_leaves_response_evidence_alone() -> None:
    plane, _ = _plane_with()
    await plane.ingest_bridge_event(_job_event("done", elapsed_secs=2.0))
    await plane.ingest_bridge_event(_job_event("failed", failure_kind="interactive_prompt"))

    snapshot = await plane.get_agent_status("hermes")

    assert snapshot.observation.response_state is ResponseState.RESPONDED


@pytest.mark.asyncio
async def test_a_roll_call_reports_a_busy_agent_as_up_without_pinging_it() -> None:
    plane, adapter = _plane_with()
    await plane.ingest_bridge_event(_job_event("running"))

    rows, missing = await agent_status_reporting.collect_rollcall_rows(
        plane, None, fresh_for_secs=120
    )

    assert missing is None
    assert adapter.tasks == []
    assert rows == ["hermes: Up, working on a RAP task: Searching the email"]


@pytest.mark.asyncio
async def test_a_recent_confirmed_response_is_reused_by_the_roll_call() -> None:
    plane, adapter = _plane_with()
    await plane.ingest_bridge_event(_job_event("done", elapsed_secs=1.0, model_label="M"))

    rows, _ = await agent_status_reporting.collect_rollcall_rows(plane, None, fresh_for_secs=120)

    assert adapter.tasks == []
    assert rows == ["hermes: Up (responded in 1s via M; current)"]


@pytest.mark.asyncio
async def test_an_old_or_failed_response_is_checked_again() -> None:
    plane, adapter = _plane_with()
    await plane.ingest_bridge_event(_job_event("failed", failure_kind="auth"))

    rows_task = asyncio.create_task(
        agent_status_reporting.collect_rollcall_rows(plane, None, fresh_for_secs=120)
    )
    while not adapter.tasks:
        await asyncio.sleep(0)
    await plane.ingest_bridge_event(
        {
            "type": "agent_job",
            "event": "finished",
            "job_id": "self-check",
            "agent": "hermes",
            "status": "done",
            "result": SELF_CHECK_SENTINEL,
            "elapsed_secs": 4.2,
        }
    )
    rows, _ = await rows_task

    assert rows == ["hermes: Up (responded in 4.2s; current)"]


def test_response_timing_survives_a_registry_round_trip() -> None:
    observation = replace(
        make_observation("codex"),
        response_state=ResponseState.RESPONDED,
        response_secs=2.5,
        response_model="GPT",
    )

    restored = AgentObservation.from_dict(observation.to_dict())

    assert (restored.response_secs, restored.response_model) == (2.5, "GPT")
    legacy = observation.to_dict()
    del legacy["response_secs"], legacy["response_model"]
    assert AgentObservation.from_dict(legacy).response_secs is None


@pytest.mark.asyncio
async def test_a_locked_registry_file_is_retried_and_never_loses_the_observation(
    tmp_path, monkeypatch
):
    """Windows: 'Access is denied' replacing agent_registry.json killed a check (09-27)."""
    import os

    from remote_agent_protocol.control_plane import store as store_module
    from remote_agent_protocol.control_plane.registry import AgentRegistry

    real_replace = os.replace
    refusals = {"left": 2}

    def flaky_replace(src, dst):
        if refusals["left"]:
            refusals["left"] -= 1
            raise PermissionError(13, "Access is denied")
        return real_replace(src, dst)

    monkeypatch.setattr(store_module.os, "replace", flaky_replace)
    monkeypatch.setattr(store_module.time, "sleep", lambda _s: None)
    registry = AgentRegistry(tmp_path / "agent_registry.json")

    await registry.observe(make_observation("codex"))
    assert (tmp_path / "agent_registry.json").exists(), "saved after the brief lock cleared"

    def always_locked(src, dst):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(store_module.os, "replace", always_locked)
    snapshot = await registry.observe(make_observation("hermes"))

    assert snapshot.observation.agent_id == "hermes"
    assert (await registry.get("hermes")) is snapshot


@pytest.mark.asyncio
async def test_slow_self_checks_are_followed_up_after_the_roll_call_returns() -> None:
    adapter = FakeAgentAdapter(
        "codex", probe=make_observation("codex"), dispatch=JobHandle("self-check-late", "codex")
    )
    plane = AgentControlPlane({"codex": adapter})
    follow_ups: list[asyncio.Task] = []

    rows, _ = await agent_status_reporting.collect_rollcall_rows(
        plane, "codex", wait_secs=0.05, follow_ups=follow_ups
    )
    assert rows == ["Codex: Checking for a response now"]
    assert len(follow_ups) == 1 and not follow_ups[0].done()

    await plane.ingest_bridge_event(
        {
            "type": "agent_job",
            "event": "finished",
            "job_id": "self-check-late",
            "agent": "codex",
            "status": "done",
            "result": SELF_CHECK_SENTINEL,
        }
    )
    late = await asyncio.wait_for(follow_ups[0], 2)

    assert agent_status_reporting.control_summary("codex", late["codex"]).startswith("Codex: Up")


@pytest.mark.asyncio
async def test_a_second_roll_call_reuses_the_answer_the_first_one_got() -> None:
    """09-27 00:21-00:23: each question re-pinged every agent that had just answered.

    The installation probe that starts every roll call was recorded as a fresh
    observation with no response evidence, wiping the answer out.
    """

    async def fresh_probe():
        # Like the CLI adapter: every probe is a new observation stamped now.
        return make_observation("codex")

    adapter = FakeAgentAdapter(
        "codex", probe=fresh_probe, dispatch=JobHandle("self-check-a", "codex")
    )
    plane = AgentControlPlane({"codex": adapter})
    first = asyncio.create_task(
        agent_status_reporting.collect_rollcall_rows(plane, "codex", fresh_for_secs=600)
    )
    while not adapter.tasks:
        await asyncio.sleep(0)
    await plane.ingest_bridge_event(
        {
            "type": "agent_job",
            "event": "finished",
            "job_id": "self-check-a",
            "agent": "codex",
            "status": "done",
            "result": SELF_CHECK_SENTINEL,
        }
    )
    await first

    rows, _ = await agent_status_reporting.collect_rollcall_rows(plane, "codex", fresh_for_secs=600)

    assert len(adapter.tasks) == 1, "no second self-check"
    assert rows == ["Codex: Up (responded; current)"]


@pytest.mark.parametrize(
    ("reply", "answered"),
    [
        ("RAP_SELF_CHECK_OK", True),
        ("RAP_SELF_CHECK_OK.", True),
        ("`RAP_SELF_CHECK_OK`", True),
        ("Done.\nRAP_SELF_CHECK_OK\n\ntokens used: 1,204", True),
        ("**RAP_SELF_CHECK_OK**", True),
        ("Self check OK", False),
        ("RAP_SELF_CHECK_OKAY", False),
        (SELF_CHECK_PROMPT, False),
        ("You asked me to reply with exactly RAP_SELF_CHECK_OK.", False),
    ],
)
def test_a_self_check_answer_is_accepted_however_the_harness_packages_it(reply, answered):
    """09-27 01:01: Codex, Hermes and Code Puppy answered but read as 'unexpected response'."""
    from remote_agent_protocol.control_plane.service import _is_self_check_reply

    assert _is_self_check_reply(reply) is answered
