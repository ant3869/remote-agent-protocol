"""Hub-dispatch plumbing shared by full voice mode and Brain mode.

``VoiceSession`` and ``BrainSession`` both drive one ``AgentConversationHub``
through an identical shape: build a ``ConversationTurnRequest``, hand it to
``hub.handle_turn``, and either register the resulting job for later
narration or relay the hub's own no-dispatch explanation back to the user.
Task 8 originally duplicated this near-verbatim between ``brain.py`` and
``session.py``; keeping it in one place means a fix here (see task-8 review
round 1, #1 and #4) never has to be applied twice.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from datetime import UTC, datetime
from uuid import uuid4

from loguru import logger

from remote_agent_protocol import config as cfg
from remote_agent_protocol.conversation_hub.service import (
    AgentConversationHub,
    ConversationTurnRequest,
    TurnDisposition,
)

NoDispatchHandler = Callable[[TurnDisposition], Awaitable[None]]


async def dispatch_via_hub(
    hub: AgentConversationHub,
    hub_dispatched_jobs: MutableMapping[str, tuple[str, str]],
    explicit_agent_id: str | None,
    text: str,
    *,
    on_no_dispatch: NoDispatchHandler,
) -> TurnDisposition:
    """Route one already-admitted dispatch through the shared conversation hub.

    Registers the dispatched attempt in ``hub_dispatched_jobs`` so the
    caller's own job-completion narration (``_announce_agent_job``) can
    later relay from the hub's own presentation instead of re-deriving one.

    The caller reaching this point has typically already told the user work
    is starting (a ``DELEGATION_ACK_PROMPT``-style reply). When the hub
    instead resolves this turn to a ``_NO_DISPATCH_KINDS`` decision --
    acknowledgment, clarification, or unavailable -- ``on_no_dispatch`` is
    awaited with the disposition so that promise is not silently broken
    (task-8 review round 1, #1).
    """
    request = ConversationTurnRequest(
        text=text,
        source=cfg.MEM0_USER_ID,
        explicit_agent_id=explicit_agent_id,
        correlation_id=uuid4().hex,
        created_at=datetime.now(UTC),
    )
    disposition = await hub.handle_turn(request)
    if disposition.task_id is None:
        await on_no_dispatch(disposition)
        return disposition
    task_ref = hub.task(disposition.task_id)
    if task_ref is not None and task_ref.attempt_id:
        hub_dispatched_jobs[task_ref.attempt_id] = (disposition.channel_id, disposition.task_id)
    return disposition


async def route_chat_turn_through_hub(
    hub: AgentConversationHub, text: str, *, butler_id: str
) -> None:
    """Record a non-delegating turn with Butler so floor/transcript stay current.

    Pure in-memory bookkeeping (no adapter I/O on this path): the hub's
    ``_direct_decision`` recognizes ``butler_id`` and returns
    ``return_to_butler`` deterministically. Never allowed to break the
    ordinary chat pipeline that follows.
    """
    stripped = text.strip()
    if not stripped:
        return
    request = ConversationTurnRequest(
        text=stripped,
        source=cfg.MEM0_USER_ID,
        explicit_agent_id=butler_id,
        correlation_id=uuid4().hex,
        created_at=datetime.now(UTC),
    )
    try:
        await hub.handle_turn(request)
    except Exception as exc:  # noqa: BLE001 - bookkeeping must never break chat
        logger.warning(f"Conversation hub chat-turn bookkeeping failed: {exc}")


def warn_if_cwd_unsupported(cwd: str | None) -> None:
    """Flag a working directory a hub dispatch has no way to honor.

    ``ConversationTurnRequest`` has no ``cwd`` field, and no adapter's
    ``dispatch_in_session`` accepts one (see ``control_plane/adapters/*``),
    so every current caller of ``dispatch_via_hub`` defaults ``cwd`` to
    ``None``. This is a defensive check, not a real fallback: a future
    caller that starts passing a real ``cwd`` needs the adapter protocol
    extended first, rather than silently losing it here (task-8 review
    round 1, #3).
    """
    if cwd is not None:
        logger.warning(
            f"_dispatch_via_hub received cwd={cwd!r} but has no way to pass it through "
            "ConversationTurnRequest or the hub's adapter protocol; it is being dropped"
        )
