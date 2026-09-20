"""Unified transcript/controls projection over the hub and the live store."""

from datetime import UTC, datetime, timedelta

import pytest

from remote_agent_protocol import conversation_projection as projection
from remote_agent_protocol.conversation_hub.models import (
    AgentChannel,
    ConversationTurn,
    MemoryConfidence,
    MemoryScope,
    MemoryStatus,
    ResultKind,
    ScopedMemory,
)

NOW = datetime(2026, 9, 19, 12, tzinfo=UTC)


def channel(agent_id: str, *, chapter_id: int = 1, archived: bool = False) -> AgentChannel:
    channel_id = "coordinator:butler" if agent_id == "butler" else f"agent:{agent_id}"
    return AgentChannel(
        channel_id=channel_id,
        agent_id=agent_id,
        chapter_id=chapter_id,
        summary="Looked into the mail backlog.",
        communication_contract_version=1,
        active_task_ids=["task_1"],
        session_binding_id=None,
        created_at=NOW,
        updated_at=NOW,
        archived_at=NOW if archived else None,
    )


def turn(
    *,
    turn_id: str,
    channel_id: str,
    speaker_id: str,
    speaker_role: str,
    full_text: str,
    spoken_text: str | None = None,
    attempt_id: str | None = None,
    task_id: str | None = None,
    result_kind: ResultKind | None = None,
    offset: int = 0,
) -> ConversationTurn:
    return ConversationTurn(
        turn_id=turn_id,
        channel_id=channel_id,
        task_id=task_id,
        speaker_id=speaker_id,
        speaker_role=speaker_role,
        full_text=full_text,
        spoken_text=spoken_text,
        result_kind=result_kind,
        contract_version=1,
        created_at=NOW + timedelta(seconds=offset),
        metadata={"attempt_id": attempt_id} if attempt_id else {},
    )


def live(rows: list[dict]) -> dict:
    return {"epoch": "e1", "truncated": False, "rows": rows}


def transcript_row(
    *,
    key: str,
    text: str,
    role: str = "agent",
    speaker_name: str = "OpenClaw",
    source_agent: str | None = "openclaw",
    job_id: str | None = "job-1",
    delivery: str = "played",
    offset: int = 0,
    **extra,
) -> dict:
    row = {
        "type": "transcript",
        "key": key,
        "text": text,
        "role": role,
        "speaker_id": speaker_name,
        "speaker_name": speaker_name,
        "delivery": delivery,
        "occurred_at": (NOW + timedelta(seconds=offset)).isoformat(),
    }
    if source_agent is not None:
        row["source_agent"] = source_agent
    if job_id is not None:
        row["job_id"] = job_id
    row.update(extra)
    return row


def snapshot(*, channels=(), turns=(), available=True) -> projection.HubSnapshot:
    return projection.HubSnapshot(channels=tuple(channels), turns=tuple(turns), available=available)


def test_channel_summaries_report_archive_state_and_exclude_butler_from_reset():
    summaries = projection.channel_summaries(
        snapshot(
            channels=[channel("openclaw"), channel("butler"), channel("hermes", archived=True)]
        )
    )

    by_id = {row["channel_id"]: row for row in summaries}
    assert by_id["agent:openclaw"]["resettable"] is True
    assert by_id["agent:openclaw"]["archived"] is False
    assert by_id["agent:openclaw"]["chapter_id"] == 1
    assert by_id["agent:openclaw"]["active_task_ids"] == ["task_1"]
    # Butler is never a harness backend, so rotating a physical session for it
    # could only ever fail -- the UI must not offer the action at all.
    assert by_id["coordinator:butler"]["resettable"] is False
    assert by_id["agent:hermes"]["archived"] is True
    assert by_id["agent:hermes"]["archived_at"] == NOW.isoformat()


def test_history_merges_the_live_row_whose_text_matches_the_hub_turn_exactly():
    hub_turn = turn(
        turn_id="turn_a",
        channel_id="agent:openclaw",
        speaker_id="openclaw",
        speaker_role="agent",
        full_text="Found 12 unread messages, 3 of them from billing.",
        spoken_text="Found twelve unread messages.",
        attempt_id="job-1",
        task_id="task_1",
        result_kind=ResultKind.SUCCESS,
    )
    rows = [transcript_row(key="speech:1", text="Found twelve unread messages.")]

    entries = projection.unified_history(snapshot(turns=[hub_turn]), live(rows))

    assert len(entries) == 1
    entry = entries[0]
    assert entry["source"] == "hub+live"
    assert entry["entry_id"] == "turn_a"
    assert entry["voice"] == "OpenClaw"
    assert entry["playback_state"] == "played"
    assert entry["result_kind"] == "success"
    assert entry["full_text"] == "Found 12 unread messages, 3 of them from billing."
    assert entry["attempt_id"] == "job-1"


def test_a_live_row_matching_only_the_job_id_stays_its_own_entry():
    # start/progress/consult/result narrations all carry the same
    # (source_agent, job_id); merging on that pair alone would collapse four
    # distinct transcript events into one and silently drop three.
    hub_turn = turn(
        turn_id="turn_a",
        channel_id="agent:openclaw",
        speaker_id="openclaw",
        speaker_role="agent",
        full_text="Found twelve unread messages.",
        spoken_text="Found twelve unread messages.",
        attempt_id="job-1",
        offset=3,
    )
    rows = [
        transcript_row(key="speech:1", text="I've asked OpenClaw to look.", offset=0),
        transcript_row(key="speech:2", text="OpenClaw is still working.", offset=1),
        transcript_row(key="speech:3", text="Found twelve unread messages.", offset=3),
    ]

    entries = projection.unified_history(snapshot(turns=[hub_turn]), live(rows))

    assert [entry["source"] for entry in entries] == ["live", "live", "hub+live"]
    assert [entry["full_text"] for entry in entries] == [
        "I've asked OpenClaw to look.",
        "OpenClaw is still working.",
        "Found twelve unread messages.",
    ]


def test_an_unmatched_hub_turn_keeps_null_playback_rather_than_a_guess():
    hub_turn = turn(
        turn_id="turn_a",
        channel_id="agent:openclaw",
        speaker_id="openclaw",
        speaker_role="agent",
        full_text="Done.",
        spoken_text="Done.",
        attempt_id="job-1",
    )

    entries = projection.unified_history(snapshot(turns=[hub_turn]), live([]))

    assert entries[0]["source"] == "hub"
    assert entries[0]["voice"] is None
    assert entries[0]["playback_state"] is None


def test_a_hub_turn_without_spoken_text_merges_on_its_full_text():
    # present_hub_result voices `spoken_text or full_text`, so the live row a
    # relay produced for a spoken_text-less turn carries the full text.
    hub_turn = turn(
        turn_id="turn_a",
        channel_id="agent:hermes",
        speaker_id="hermes",
        speaker_role="agent",
        full_text="The build passed.",
        spoken_text=None,
        attempt_id="job-9",
    )
    rows = [
        transcript_row(
            key="speech:1", text="The build passed.", source_agent="hermes", job_id="job-9"
        )
    ]

    entries = projection.unified_history(snapshot(turns=[hub_turn]), live(rows))

    assert entries[0]["source"] == "hub+live"


def test_a_remote_prefixed_source_agent_still_matches_its_hub_turn():
    hub_turn = turn(
        turn_id="turn_a",
        channel_id="agent:hermes",
        speaker_id="hermes",
        speaker_role="agent",
        full_text="Done.",
        spoken_text="Done.",
        attempt_id="job-9",
    )
    rows = [
        transcript_row(key="speech:1", text="Done.", source_agent="laptop:hermes", job_id="job-9")
    ]

    entries = projection.unified_history(snapshot(turns=[hub_turn]), live(rows))

    assert entries[0]["source"] == "hub+live"
    assert entries[0]["channel_id"] == "agent:hermes"


def test_live_user_rows_are_suppressed_whenever_a_hub_is_available():
    hub_turn = turn(
        turn_id="turn_u",
        channel_id="coordinator:butler",
        speaker_id="ant",
        speaker_role="user",
        full_text="Check my mail.",
    )
    rows = [
        transcript_row(
            key="speech:u",
            text="Check my mail.",
            role="user",
            speaker_name="You",
            source_agent=None,
            job_id=None,
            delivery="text_only",
        )
    ]

    entries = projection.unified_history(snapshot(turns=[hub_turn]), live(rows))

    assert [entry["source"] for entry in entries] == ["hub"]
    assert entries[0]["speaker_role"] == "user"


def test_live_user_rows_survive_when_no_hub_exists_at_all():
    rows = [
        transcript_row(
            key="speech:u",
            text="Check my mail.",
            role="user",
            speaker_name="You",
            source_agent=None,
            job_id=None,
            delivery="text_only",
        )
    ]

    entries = projection.unified_history(snapshot(available=False), live(rows))

    assert [entry["source"] for entry in entries] == ["live"]
    assert entries[0]["speaker_role"] == "user"


def test_a_live_only_row_infers_its_channel_and_never_fabricates_spoken_text():
    rows = [
        transcript_row(key="speech:1", text="Starting that now.", source_agent=None, job_id=None),
        transcript_row(key="speech:2", text="OpenClaw is working.", source_agent="pc:openclaw"),
    ]

    entries = projection.unified_history(snapshot(), live(rows))

    assert entries[0]["channel_id"] == "coordinator:butler"
    assert entries[1]["channel_id"] == "agent:openclaw"
    assert entries[0]["spoken_text"] is None
    assert entries[0]["result_kind"] is None


def test_the_channel_filter_keeps_a_merged_entry_and_drops_other_channels():
    merged = turn(
        turn_id="turn_a",
        channel_id="agent:openclaw",
        speaker_id="openclaw",
        speaker_role="agent",
        full_text="Done.",
        spoken_text="Done.",
        attempt_id="job-1",
    )
    other = turn(
        turn_id="turn_b",
        channel_id="agent:hermes",
        speaker_id="hermes",
        speaker_role="agent",
        full_text="Also done.",
        spoken_text="Also done.",
        attempt_id="job-2",
        offset=1,
    )
    rows = [transcript_row(key="speech:1", text="Done.")]

    entries = projection.unified_history(
        snapshot(turns=[merged, other]), live(rows), channel_id="agent:openclaw"
    )

    assert [entry["entry_id"] for entry in entries] == ["turn_a"]
    assert entries[0]["source"] == "hub+live"


def test_the_query_filter_matches_full_text_or_spoken_text_case_insensitively():
    turns = [
        turn(
            turn_id="turn_a",
            channel_id="agent:openclaw",
            speaker_id="openclaw",
            speaker_role="agent",
            full_text="Billing invoice attached.",
            spoken_text="All sorted.",
        ),
        turn(
            turn_id="turn_b",
            channel_id="agent:openclaw",
            speaker_id="openclaw",
            speaker_role="agent",
            full_text="Nothing relevant.",
            spoken_text="BILLING is quiet.",
            offset=1,
        ),
        turn(
            turn_id="turn_c",
            channel_id="agent:openclaw",
            speaker_id="openclaw",
            speaker_role="agent",
            full_text="Unrelated.",
            spoken_text="Unrelated.",
            offset=2,
        ),
    ]

    entries = projection.unified_history(snapshot(turns=turns), live([]), query="billing")

    assert [entry["entry_id"] for entry in entries] == ["turn_a", "turn_b"]


def test_entries_interleave_by_parsed_timestamp_across_both_sources():
    turns = [
        turn(
            turn_id="turn_late",
            channel_id="agent:openclaw",
            speaker_id="openclaw",
            speaker_role="agent",
            full_text="Later hub turn.",
            offset=30,
        )
    ]
    rows = [
        transcript_row(key="speech:1", text="Earlier live row.", offset=10),
        # A different textual timestamp shape must still order correctly,
        # which a plain string comparison would get wrong.
        transcript_row(
            key="speech:2",
            text="Middle live row.",
            offset=0,
            occurred_at=(NOW + timedelta(seconds=20, microseconds=500000)).isoformat(),
        ),
    ]

    entries = projection.unified_history(snapshot(turns=turns), live(rows))

    assert [entry["full_text"] for entry in entries] == [
        "Earlier live row.",
        "Middle live row.",
        "Later hub turn.",
    ]


def test_non_transcript_live_rows_never_enter_the_unified_history():
    rows = [
        {"type": "agent_job", "key": "job:job-1", "job_id": "job-1", "status": "done"},
        {"type": "routing", "key": "route:1"},
        transcript_row(key="speech:1", text="Kept."),
    ]

    entries = projection.unified_history(snapshot(), live(rows))

    assert [entry["full_text"] for entry in entries] == ["Kept."]


def memory(
    *,
    memory_id: str = "memory_1",
    scope: MemoryScope = MemoryScope.CHANNEL,
    confidence: MemoryConfidence = MemoryConfidence.USER_STATED,
    status: MemoryStatus = MemoryStatus.ACTIVE,
    subject: str = "preferred mailbox",
    value: str = "work inbox",
    channel_id: str | None = "agent:openclaw",
    supersedes: str | None = None,
) -> ScopedMemory:
    return ScopedMemory(
        memory_id=memory_id,
        scope=scope,
        subject=subject,
        value=value,
        source_turn_ids=["turn_a"],
        confidence=confidence,
        observed_at=NOW,
        supersedes=supersedes,
        status=status,
        channel_id=channel_id,
    )


def test_memory_detail_reports_scope_confidence_and_channel_eligibility():
    detail = projection.memory_detail(memory())

    assert detail["memory_id"] == "memory_1"
    assert detail["scope"] == "channel"
    assert detail["confidence"] == "user_stated"
    assert detail["status"] == "active"
    assert detail["subject"] == "preferred mailbox"
    assert detail["value"] == "work inbox"
    assert detail["source_turn_ids"] == ["turn_a"]
    assert detail["observed_at"] == NOW.isoformat()
    assert detail["supersedes"] is None
    assert "agent:openclaw" in detail["eligibility"]


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        (MemoryScope.SHARED, "every channel"),
        (MemoryScope.TASK, "task"),
        (MemoryScope.PROJECT, "project"),
    ],
)
def test_memory_eligibility_explains_each_scope(scope, expected):
    detail = projection.memory_detail(memory(scope=scope, channel_id=None))

    assert expected in detail["eligibility"]


def test_a_superseded_memory_is_reported_as_excluded_from_context():
    detail = projection.memory_detail(
        memory(status=MemoryStatus.SUPERSEDED, memory_id="memory_old")
    )

    assert detail["status"] == "superseded"
    assert "uperseded" in detail["eligibility"]
    assert "xcluded" in detail["eligibility"]


def test_a_forgotten_tombstone_never_reconstructs_the_value_it_scrubbed():
    forgotten = memory(status=MemoryStatus.FORGOTTEN, subject="", value="", supersedes="memory_0")

    detail = projection.memory_detail(forgotten)

    assert detail["value"] == ""
    assert detail["subject"] == ""
    assert detail["status"] == "forgotten"
    assert "orgotten" in detail["eligibility"]
    assert detail["source_turn_ids"] == ["turn_a"]


def test_memory_detail_of_nothing_is_nothing():
    assert projection.memory_detail(None) is None


@pytest.mark.asyncio
async def test_gather_hub_snapshot_detaches_channels_and_turns_from_the_hub(tmp_path):
    from tests.test_agent_conversation_hub import make_hub, turn_request

    hub, _registry, _adapters = make_hub(tmp_path)
    await hub.handle_turn(turn_request("what is the weather"))

    gathered = await projection.gather_hub_snapshot(hub)

    assert gathered.available is True
    assert [c.channel_id for c in gathered.channels] == ["coordinator:butler"]
    assert [t.full_text for t in gathered.turns] == ["what is the weather"]
    assert isinstance(gathered.turns, tuple)


@pytest.mark.asyncio
async def test_gather_hub_snapshot_without_a_hub_is_empty_and_unavailable():
    gathered = await projection.gather_hub_snapshot(None)

    assert gathered.available is False
    assert gathered.channels == ()
    assert gathered.turns == ()


@pytest.mark.asyncio
async def test_gather_memory_returns_none_for_an_unknown_id(tmp_path):
    from tests.test_agent_conversation_hub import make_hub

    hub, _registry, _adapters = make_hub(tmp_path)

    assert await projection.gather_memory(hub, "memory_missing") is None
    assert await projection.gather_memory(None, "memory_1") is None
