"""Bounded context, attribution and transfer tests independent of live harnesses."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from remote_agent_protocol.conversation_hub.context import (
    ChannelSummary,
    ContextAssembler,
    ContextBudget,
    ContextRequest,
    LiveObservation,
    SummaryItem,
    TaskContext,
)
from remote_agent_protocol.conversation_hub.memory import MemoryRepository, PromotionReason
from remote_agent_protocol.conversation_hub.models import (
    ConversationTurn,
    MemoryConfidence,
    MemoryScope,
    ScopedMemory,
)

NOW = datetime(2026, 9, 18, tzinfo=UTC)


def turn(text, channel="agent:hermes", **kwargs):
    return ConversationTurn.new(
        channel_id=channel,
        speaker_id="user",
        speaker_role="user",
        full_text=text,
        now=NOW,
        **kwargs,
    )


def request(**kwargs):
    return replace(
        ContextRequest(
            channel_id="agent:hermes",
            current_request="CURRENT",
            communication_contract="CONTRACT",
            task_id="task-1",
        ),
        **kwargs,
    )


def test_context_order_and_channel_isolation():
    req = request(
        active_task=TaskContext("task-1", "ACTIVE"),
        recent_turns=(turn("RECENT"), turn("private OpenClaw-only turn", "agent:openclaw")),
        summary=ChannelSummary("agent:hermes", user_statements=(SummaryItem("SUMMARY"),)),
        live_state=(LiveObservation("LIVE", NOW, channel_id="agent:hermes"),),
    )
    package = ContextAssembler(MemoryRepository()).assemble(req)
    rendered = package.render()
    assert "private OpenClaw-only turn" not in rendered
    assert [
        rendered.index(s) for s in ["CONTRACT", "CURRENT", "ACTIVE", "RECENT", "SUMMARY", "LIVE"]
    ] == sorted(
        rendered.index(s) for s in ["CONTRACT", "CURRENT", "ACTIVE", "RECENT", "SUMMARY", "LIVE"]
    )
    assert package == ContextAssembler(MemoryRepository()).assemble(req)


def test_budgets_count_rendered_headers_and_keep_required_sections():
    budget = ContextBudget(
        total_chars=260,
        active_task_chars=60,
        recent_turns_chars=70,
        summary_chars=50,
        memory_chars=50,
        live_state_chars=50,
    )
    req = request(active_task=TaskContext("task-1", "A" * 500), recent_turns=(turn("B" * 500),))
    package = ContextAssembler(MemoryRepository(), budget).assemble(req)
    assert len(package.render()) <= budget.total_chars
    assert "CONTRACT" in package.render() and "CURRENT" in package.render()
    assert len(package.section("active_task")) <= 60
    assert len(package.section("recent_turns")) <= 70
    assert package.omitted_sections


def test_oversized_contract_or_request_fails_instead_of_truncating():
    with pytest.raises(ValueError, match="contract.*request"):
        ContextAssembler(MemoryRepository(), ContextBudget(total_chars=10)).assemble(request())


def test_newest_complete_turns_are_chosen_and_rendered_chronologically():
    turns = (
        replace(turn("old" * 100), created_at=NOW - timedelta(seconds=3)),
        replace(turn("middle"), created_at=NOW - timedelta(seconds=2)),
        turn("newest"),
    )
    package = ContextAssembler(MemoryRepository(), ContextBudget(recent_turns_chars=120)).assemble(
        request(recent_turns=tuple(reversed(turns)))
    )
    assert "oldold" not in package.render()
    assert package.render().index("middle") < package.render().index("newest")


def test_summary_preserves_inference_and_has_structured_sections():
    summary = ChannelSummary(
        "agent:hermes",
        user_statements=(SummaryItem("USER"),),
        verified_observations=(SummaryItem("OBS", MemoryConfidence.VERIFIED),),
        decisions=(SummaryItem("GUESS", MemoryConfidence.INFERRED),),
        open_questions=(SummaryItem("QUESTION"),),
        active_tasks=(SummaryItem("TASK"),),
    )
    rendered = ContextAssembler(MemoryRepository()).assemble(request(summary=summary)).render()
    for label in [
        "User statements",
        "Verified observations",
        "Decisions",
        "Open questions",
        "Active tasks",
    ]:
        assert label in rendered
    assert "[inferred; not access evidence] GUESS" in rendered
    with pytest.raises(ValueError):
        ChannelSummary(
            "agent:hermes", verified_observations=(SummaryItem("guess", MemoryConfidence.INFERRED),)
        )


def test_switch_only_transfers_matching_task_selected_material_and_shared_memory():
    req = request(
        previous_channel_id="agent:openclaw",
        active_task=TaskContext("task-1", "TASK"),
        selected_material=("SELECTED",),
        recent_turns=(turn("private OpenClaw-only turn", "agent:openclaw"),),
        summary=ChannelSummary("agent:openclaw", user_statements=(SummaryItem("PRIVATE SUMMARY"),)),
    )
    rendered = ContextAssembler(MemoryRepository()).assemble(req).render()
    assert "TASK" in rendered and "SELECTED" in rendered
    assert "private" not in rendered and "PRIVATE SUMMARY" not in rendered


def test_unrelated_task_and_live_state_are_excluded():
    req = request(
        active_task=TaskContext("other", "PRIVATE TASK"),
        live_state=(
            LiveObservation("PRIVATE STATE", NOW, channel_id="agent:other"),
            LiveObservation("MATCHING STATE", NOW, task_id="task-1"),
        ),
    )
    rendered = ContextAssembler(MemoryRepository()).assemble(req).render()
    assert "PRIVATE" not in rendered and "MATCHING STATE" in rendered


def test_secret_tool_and_hidden_reasoning_exclusion():
    req = request(
        recent_turns=(
            turn("api_key=never-store"),
            turn("RAW TOOL", metadata={"raw_tool_output": True}),
            turn("<analysis>HIDDEN</analysis>"),
        ),
        selected_material=("password is private", "SAFE"),
    )
    rendered = ContextAssembler(MemoryRepository()).assemble(req).render()
    for denied in ["never-store", "RAW TOOL", "HIDDEN", "private"]:
        assert denied not in rendered
    assert "SAFE" in rendered


def test_secret_current_request_is_rejected_without_truncation():
    with pytest.raises(ValueError, match="secret"):
        ContextAssembler(MemoryRepository()).assemble(request(current_request="api_key=private"))


@pytest.mark.parametrize(
    "field",
    [
        "total_chars",
        "recent_turns_chars",
        "summary_chars",
        "active_task_chars",
        "memory_chars",
        "live_state_chars",
    ],
)
def test_negative_budgets_rejected(field):
    with pytest.raises(ValueError):
        ContextBudget(**{field: -1})


def test_eligible_memory_uses_scopes_provenance_and_latest_correction():
    repo = MemoryRepository()
    base = ScopedMemory(
        "local",
        MemoryScope.CHANNEL,
        "topic",
        "OLD",
        ["turn-1"],
        MemoryConfidence.USER_STATED,
        NOW,
        channel_id="agent:hermes",
    )
    repo.add(base)
    corrected = repo.correct("local", "CORRECTED", "turn-2", NOW)
    repo.add(replace(base, memory_id="private", channel_id="agent:openclaw", value="PRIVATE"))
    repo.add(
        replace(base, memory_id="shared", scope=MemoryScope.SHARED, value="SHARED"),
        reason=PromotionReason.STABLE_USER_FACT,
    )
    repo.add(
        replace(
            base, memory_id="project", scope=MemoryScope.PROJECT, project_id="rap", value="PROJECT"
        ),
        reason=PromotionReason.PROJECT_DECISION,
    )
    repo.add(
        replace(base, memory_id="inferred", confidence=MemoryConfidence.INFERRED, value="GUESS")
    )
    assembler = ContextAssembler(repo)
    rendered = assembler.assemble(
        request(
            project_id="rap", live_state=(LiveObservation("LIVE", NOW, channel_id="agent:hermes"),)
        )
    ).render()
    assert "OLD" not in rendered and "PRIVATE" not in rendered
    for expected in (
        "CORRECTED",
        "SHARED",
        "PROJECT",
        "inferred; not access evidence",
        "sources turn-2",
    ):
        assert expected in rendered
    assert rendered.index("CORRECTED") < rendered.index("LIVE")
    repo.forget(corrected.memory_id)
    assert "CORRECTED" not in assembler.assemble(request()).render()
    switched = assembler.assemble(
        request(channel_id="agent:codex", previous_channel_id="agent:hermes")
    ).render()
    assert "SHARED" in switched and "PROJECT" not in switched and "GUESS" not in switched


def test_every_optional_section_respects_its_rendered_budget():
    repo = MemoryRepository()
    repo.add(
        ScopedMemory(
            "m",
            MemoryScope.CHANNEL,
            "s",
            "FACT",
            ["t"],
            MemoryConfidence.USER_STATED,
            NOW,
            channel_id="agent:hermes",
        )
    )
    budget = ContextBudget(
        total_chars=700,
        active_task_chars=100,
        recent_turns_chars=100,
        summary_chars=100,
        memory_chars=190,
        live_state_chars=100,
    )
    req = request(
        active_task=TaskContext("task-1", "TASK"),
        recent_turns=(turn("TURN"),),
        summary=ChannelSummary("agent:hermes", user_statements=(SummaryItem("SUMMARY"),)),
        live_state=(LiveObservation("LIVE", NOW, channel_id="agent:hermes"),),
    )
    package = ContextAssembler(repo, budget).assemble(req)
    assert len(package.render()) <= budget.total_chars
    for section, limit in [
        ("active_task", 100),
        ("recent_turns", 100),
        ("summary", 100),
        ("memories", 190),
        ("live_state", 100),
    ]:
        assert 0 < len(package.section(section)) <= limit


def test_live_state_and_turn_order_are_independent_of_input_order():
    turns = (replace(turn("EARLIER"), created_at=NOW - timedelta(seconds=1)), turn("LATER"))
    state = (
        LiveObservation("Z", NOW, task_id="task-1"),
        LiveObservation("A", NOW, task_id="task-1"),
    )
    assembler = ContextAssembler(MemoryRepository())
    assert assembler.assemble(request(recent_turns=turns, live_state=state)) == assembler.assemble(
        request(recent_turns=tuple(reversed(turns)), live_state=tuple(reversed(state)))
    )
