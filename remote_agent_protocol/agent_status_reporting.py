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

from remote_agent_protocol.control_plane.models import (
    AgentSnapshot,
    ControlError,
    JobHandle,
    ResponseState,
    UpdateState,
)

DIAGNOSTIC_LIMITATION = (
    "Version probes prove only the installed CLI answered locally; they are not agent replies. "
    "RAP cannot inspect unsupported external sessions."
)


def control_summary(agent_id: str, result: AgentSnapshot | ControlError) -> str:
    """Turn a control-plane result into concise, evidence-bound narration input."""
    if isinstance(result, ControlError):
        return f"{agent_id} could not be verified ({result.code})"
    observation = result.observation
    freshness = "stale" if result.is_stale() else "current"
    work = f", {observation.current_work.summary}" if observation.current_work else ""
    response = {
        ResponseState.RESPONDED: "actual response confirmed",
        ResponseState.PENDING: "actual response check pinging",
        ResponseState.FAILED: "actual response check failed",
        ResponseState.UNKNOWN: "no actual response evidence",
    }[observation.response_state]
    update = (
        "CLI update available"
        if observation.update_state is UpdateState.UPDATE_AVAILABLE
        else "no CLI update evidence"
    )
    issues = f", issues: {', '.join(observation.issues)}" if observation.issues else ""
    return (
        f"{observation.display_name} on {observation.machine}: {observation.presence.value}, "
        f"{observation.activity.value}, {observation.health.value} ({freshness}{work}); "
        f"CLI probe is installation/reachability only; {response}; {update}{issues}"
    )


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
) -> None:
    """Start a fixed-response self-check for every selected backend and report it.

    ``request_response_check`` starts an async job; its terminal result
    (RESPONDED/FAILED) is reported on a later query, once the harness's
    lifecycle event lands -- this call can only ever report that the check
    is pinging, was refused (already busy or unreachable -- itself real
    evidence), or could not be started. There is no way to make this
    synchronous without misrepresenting a probe as an actual reply.
    """
    checker = request_response_check or control_plane.request_response_check
    checks = await asyncio.gather(*(checker(backend) for backend in selected))
    for backend, check in zip(selected, checks, strict=True):
        if isinstance(check, JobHandle):
            rows.append(f"{backend}: fixed-response self-check is pinging")
        else:
            detail = check.error.detail if check.error else "self-check was not started"
            rows.append(f"{backend}: fixed-response self-check not started ({detail})")


async def collect_rollcall_rows(
    control_plane,
    agent: str | None,
    *,
    excluded: frozenset[str] = frozenset(),
    request_response_check: Callable[[str], Awaitable[object]] | None = None,
) -> tuple[list[str], str | None]:
    """Return ``(rows, missing_message)``; ``rows`` is empty exactly when nothing matched.

    Always starts a real fixed-response self-check alongside the
    reachability probe for every matched backend -- "status" is the phrase
    people actually use for this, and a version probe alone answers "is it
    installed," never "is it actually responding, busy, or rate-limited."
    """
    results = await control_plane.list_agents(refresh=True)
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
    rows = [control_summary(backend, snapshot) for backend, snapshot in selected.items()]
    await _append_response_check_rows(rows, selected, control_plane, request_response_check)
    return rows, None


def format_rollcall(rows: list[str], missing: str | None) -> str:
    """Build the bracketed roll-call instruction RAP hands to its own model."""
    if missing is not None:
        return f"[Agent roll call: {missing}. Answer from this; do not start any new work.]"
    listed = "; ".join(rows)
    return (
        f"[Agent roll call: {listed}. This is Remote Agent Protocol's own check of each "
        "backend -- whether it can be started here and whether its machine is answering -- "
        "not a reply from the agents themselves. Report it as such, briefly, and do not "
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
    results = await control_plane.list_agents(refresh=True)
    selected = _select(results, agent)
    if not selected:
        missing = (
            f"there is no agent backend named '{agent}'" if agent else "no agents are configured"
        )
        return [], missing
    rows = [control_summary(backend, snapshot) for backend, snapshot in selected.items()]
    if actual_response:
        await _append_response_check_rows(rows, selected, control_plane, request_response_check)
    return rows, None


def format_diagnostic(rows: list[str], missing: str | None) -> str:
    """Build the bracketed diagnostic instruction RAP hands to its own model."""
    if missing is not None:
        return f"[Agent diagnostic: {missing}. Do not start any new work.]"
    listed = "; ".join(rows)
    return (
        f"[Agent diagnostic: {listed}. {DIAGNOSTIC_LIMITATION} "
        "Answer from this; do not start any new work.]"
    )
