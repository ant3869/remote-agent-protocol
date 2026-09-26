"""Shared "what agents are available" status reporting for both modes.

``_handle_agent_rollcall`` and ``_handle_agent_diagnostic`` existed as
separate, drifting per-mode implementations before Task 8 (see
task-8-brief.md). Both modes now format their control-plane evidence through
this module so a roll call or diagnostic reflects identical evidence --
reachability and, once a self-check has run, exact-response confirmation --
in voice mode and Brain mode alike. Each mode still owns its own turn-state
side effects (``_agent_ack_turn``/``_control_turn``/``_direct_reply``) and
still calls its own ``AgentControlPlane`` instance; only the row-selection
and text-composition logic is unified here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta

from remote_agent_protocol.control_plane.models import (
    AgentSnapshot,
    ControlError,
    Health,
    JobHandle,
    ResponseState,
)

DIAGNOSTIC_LIMITATION = (
    "RAP reports only installation, bridge, and fixed-response evidence. "
    "It cannot inspect unsupported external sessions or accounts."
)

_RESPONSE_FAILURE_LABELS = {
    "quota": "Out of quota",
    "rate_limit": "Rate limit",
    "capacity": "Capacity",
    "auth": "Authentication",
    "authentication": "Authentication",
    "model_not_found": "Model not found",
    "response_timeout": "No response",
    "unexpected_response": "Unexpected response",
}


def _response_failure_label(issues: tuple[str, ...]) -> str:
    """Return the latest bounded self-check failure in user-facing terms."""
    failure_kind = issues[-1] if issues else ""
    return _RESPONSE_FAILURE_LABELS.get(failure_kind, "Error")


def control_summary(agent_id: str, result: AgentSnapshot | ControlError) -> str:
    """Turn a control-plane result into concise, evidence-bound narration input."""
    if isinstance(result, ControlError):
        return f"{agent_id} could not be verified ({result.code})"
    observation = result.observation
    freshness = "stale" if result.is_stale() else "current"
    busy = _busy_with(observation)
    if observation.response_state is ResponseState.RESPONDED:
        return (
            f"{observation.display_name}: Up ({_response_detail(observation)}; {freshness}){busy}"
        )
    if observation.response_state is ResponseState.FAILED:
        return (
            f"{observation.display_name}: Down ({_response_failure_label(observation.issues)}; "
            f"{freshness}){busy}"
        )
    if busy:
        return f"{observation.display_name}: Up{busy}"
    if observation.response_state is ResponseState.PENDING:
        return f"{observation.display_name}: Checking for a response now"
    work = f", {observation.current_work.summary}" if observation.current_work else ""
    response = {
        ResponseState.RESPONDED: "actual response confirmed",
        ResponseState.PENDING: "actual response check running now",
        ResponseState.FAILED: "actual response check failed",
        ResponseState.UNKNOWN: "no actual response evidence",
    }[observation.response_state]
    issues = f", issues: {', '.join(observation.issues)}" if observation.issues else ""
    if observation.health is Health.UNKNOWN and observation.response_state is ResponseState.UNKNOWN:
        return f"{observation.display_name}: no verified response yet ({freshness}{work}){issues}"
    return (
        f"{observation.display_name} on {observation.machine}: {observation.presence.value}, "
        f"{observation.activity.value}, {observation.health.value} ({freshness}{work}); "
        f"{response}{issues}"
    )


def _response_detail(observation) -> str:
    """Describe a confirmed response, e.g. "responded in 4.2s via OpenAI GPT-5.5"."""
    detail = "responded"
    if observation.response_secs is not None:
        detail += f" in {observation.response_secs:g}s"
    if observation.response_model:
        detail += f" via {observation.response_model}"
    return detail


def _busy_with(observation) -> str:
    """A ", working on a RAP task: <summary>" suffix while a real (non-self-check) job runs."""
    work = observation.current_work
    if work is None or work.summary.startswith("RAP self-check"):
        return ""
    return f", working on a RAP task: {work.summary}" if work.summary else ", working on a RAP task"


def _needs_response_check(
    result: AgentSnapshot | ControlError, *, fresh_for_secs: float, now: datetime
) -> bool:
    """Whether a new self-check would tell the user anything the record doesn't.

    A busy agent is already proving it runs (and would refuse a concurrent
    check), and a response confirmed moments ago -- by a check or a finished
    job -- still holds. Failures are always re-checked so a fix shows up
    immediately.
    """
    if not isinstance(result, AgentSnapshot):
        return True
    observation = result.observation
    if _busy_with(observation):
        return False
    observed_at = observation.response_observed_at
    return not (
        observation.response_state is ResponseState.RESPONDED
        and observed_at is not None
        and now - observed_at <= timedelta(seconds=fresh_for_secs)
    )


def explain_response_check(agent_id: str, result: AgentSnapshot | ControlError) -> str:
    """Answer a follow-up about one fixed-response check without inference."""
    if isinstance(result, ControlError):
        return f"RAP cannot read {agent_id}'s response-check state ({result.code})."
    observation = result.observation
    name = observation.display_name
    if observation.response_state is ResponseState.PENDING:
        return f"Yes. RAP's fixed response check for {name} is running now and awaiting its exact reply."
    if observation.response_state is ResponseState.RESPONDED:
        return f"No further check is running: {name} already returned RAP's exact fixed reply."
    if observation.response_state is ResponseState.FAILED:
        return f"No. No response check is running now; {name}'s last fixed response check failed."
    return f"No response check is running for {name}, and RAP has not received a verified reply."


def _select(
    results: Mapping[str, AgentSnapshot | ControlError],
    agent: str | None,
    *,
    excluded: frozenset[str] = frozenset(),
) -> dict[str, AgentSnapshot | ControlError]:
    return {
        backend: snapshot
        for backend, snapshot in results.items()
        if (agent is None or backend == agent or backend.endswith(f":{agent}"))
        and backend not in excluded
    }


async def _append_response_check_rows(
    rows: list[str],
    selected: Mapping[str, AgentSnapshot | ControlError],
    control_plane,
    request_response_check: Callable[[str], Awaitable[object]] | None,
    *,
    fresh_for_secs: float = 0.0,
) -> dict[str, AgentSnapshot | ControlError]:
    """Run bounded fixed-response checks and return their evidence snapshots.

    Agents that are working on a RAP task, or answered within ``fresh_for_secs``,
    are reported from that evidence instead of being pinged again.
    """
    checker = request_response_check or control_plane.request_response_check
    now = datetime.now(UTC)
    to_check = [
        backend
        for backend, result in selected.items()
        if _needs_response_check(result, fresh_for_secs=fresh_for_secs, now=now)
    ]
    checks = await asyncio.gather(*(checker(backend) for backend in to_check))
    resolved = dict(selected)
    for backend, check in zip(to_check, checks, strict=True):
        if isinstance(check, JobHandle):
            outcome = await control_plane.wait_for_response_check(check.job_id)
            # A non-live test/double handle has no waiter. Preserve the
            # established pending wording and do not replace the selected
            # snapshot with unrelated registry state.
            if outcome is None:
                rows.append(f"{backend}: fixed-response self-check is pinging")
                continue
            # The selected snapshot predates the new check. Re-read control
            # state once it settles so the user sees this result, never an old
            # stale failure. Test doubles may not retain the live handle.
            current = await control_plane.get_agent_status(backend, refresh=False)
            if isinstance(current, AgentSnapshot):
                resolved[backend] = current
            elif isinstance(outcome, ControlError):
                rows.append(f"{backend}: response check is still pending after its timeout")
        else:
            detail = check.error.detail if check.error else "self-check was not started"
            rows.append(f"{backend}: fixed-response self-check not started ({detail})")
    return resolved


async def collect_rollcall_rows(
    control_plane,
    agent: str | None,
    *,
    excluded: frozenset[str] = frozenset(),
    request_response_check: Callable[[str], Awaitable[object]] | None = None,
    fresh_for_secs: float = 0.0,
) -> tuple[list[str], str | None]:
    """Return ``(rows, missing_message)``; ``rows`` is empty exactly when nothing matched.

    Starts a real fixed-response self-check for every matched backend unless
    it is working on a RAP task or confirmed a response within
    ``fresh_for_secs`` -- "status" is the phrase people actually use for
    this, and installation discovery alone answers neither readiness nor
    account health.
    """
    # A named question must not refresh, display, or launch checks for every
    # configured harness.  ``list_agents`` is only appropriate when the user
    # explicitly asks about the group.
    results = await _fresh_results(control_plane, agent)
    selected = _select(results, agent, excluded=excluded)
    if not selected:
        missing = (
            f"there is no agent backend named '{agent}'"
            if agent
            else (
                "no alternative agent backends are configured"
                if excluded
                else "no agent backends are configured"
            )
        )
        return [], missing
    check_rows: list[str] = []
    selected = await _append_response_check_rows(
        check_rows,
        selected,
        control_plane,
        request_response_check,
        fresh_for_secs=fresh_for_secs,
    )
    rows = [control_summary(backend, snapshot) for backend, snapshot in selected.items()]
    rows.extend(check_rows)
    return rows, None


def format_rollcall(rows: list[str], missing: str | None) -> str:
    """Build the bracketed roll-call instruction RAP hands to its own model."""
    if missing is not None:
        return f"[Agent roll call: {missing}. Answer from this; do not start any new work.]"
    listed = "; ".join(rows)
    return (
        f"[Agent roll call: {listed}. Report only the response evidence, briefly, and do not "
        "start any new work.]"
    )


async def collect_diagnostic_rows(
    control_plane,
    agent: str | None,
    *,
    actual_response: bool = False,
    request_response_check: Callable[[str], Awaitable[object]] | None = None,
) -> tuple[list[str], str | None]:
    """Return ``(rows, missing_message)`` for an agent diagnostic report."""
    results = await _fresh_results(control_plane, agent)
    selected = _select(results, agent)
    if not selected:
        missing = (
            f"there is no agent backend named '{agent}'" if agent else "no agents are configured"
        )
        return [], missing
    check_rows: list[str] = []
    # Diagnostics are status requests too. A successful executable lookup is
    # never enough to answer whether an agent can perform useful work.
    selected = await _append_response_check_rows(
        check_rows, selected, control_plane, request_response_check
    )
    rows = [control_summary(backend, snapshot) for backend, snapshot in selected.items()]
    rows.extend(check_rows)
    return rows, None


async def _fresh_results(
    control_plane, agent: str | None
) -> dict[str, AgentSnapshot | ControlError]:
    """Refresh one named agent or the configured group the user requested."""
    if agent is None:
        return await control_plane.list_agents(refresh=True)
    return {agent: await control_plane.get_agent_status(agent, refresh=True)}


def format_diagnostic(rows: list[str], missing: str | None) -> str:
    """Build the bracketed diagnostic instruction RAP hands to its own model."""
    if missing is not None:
        return f"[Agent diagnostic: {missing}. Do not start any new work.]"
    listed = "; ".join(rows)
    return (
        f"[Agent diagnostic: {listed}. {DIAGNOSTIC_LIMITATION} "
        "Answer from this; do not start any new work.]"
    )
