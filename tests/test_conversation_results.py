"""Agent-authored result envelopes, spoken presentation, and Butler recovery."""

from datetime import UTC, datetime

import pytest

from remote_agent_protocol.conversation_hub.models import ResultKind
from remote_agent_protocol.conversation_hub.results import (
    COMMUNICATION_CONTRACT,
    COMMUNICATION_CONTRACT_VERSION,
    RECOVERY_CLARIFY,
    RECOVERY_MANUAL,
    RECOVERY_REASSIGN,
    RECOVERY_RETRY,
    AgentResultEnvelope,
    RecoveryDecision,
    ResultPresenter,
    classify_recovery,
)

NOW = datetime(2026, 9, 18, 12, tzinfo=UTC)


def test_communication_contract_matches_the_design_doc_exactly():
    """The hidden contract text and version are the spec's literal values."""
    assert COMMUNICATION_CONTRACT_VERSION == 1
    assert COMMUNICATION_CONTRACT == (
        "Speak to Ant like a trusted teammate: natural, direct, and brief, but complete. "
        "Lead with the outcome. Include every important result, decision, warning, failure, "
        "and next step. Omit internal tool chatter unless asked. Keep operational progress "
        "separate from the final answer. If blocked, ask one clear question. "
        "Never impersonate Butler."
    )


def test_envelope_requires_an_aware_timestamp():
    """A naive timestamp is rejected the same way the rest of the hub rejects it."""
    with pytest.raises(ValueError, match="timezone-aware"):
        AgentResultEnvelope(
            task_id="task-1",
            attempt_id="job-1",
            channel_id="agent:openclaw",
            agent_id="openclaw",
            result_kind=ResultKind.SUCCESS,
            full_text="Inbox is clear.",
            spoken_text="Inbox is clear.",
            created_at=datetime(2026, 9, 18, 12),
        )


def test_envelope_rejects_empty_full_text():
    """An empty canonical result would silently hide that nothing was said."""
    with pytest.raises(ValueError, match="full_text"):
        AgentResultEnvelope(
            task_id="task-1",
            attempt_id="job-1",
            channel_id="agent:openclaw",
            agent_id="openclaw",
            result_kind=ResultKind.SUCCESS,
            full_text="   ",
            spoken_text="",
            created_at=NOW,
        )


def test_presenter_preserves_full_text_verbatim_for_a_short_result():
    """A result under budget is not reformatted away from the agent's own words."""
    presenter = ResultPresenter()
    raw = "Your inbox is clear. No important emails since yesterday."

    envelope = presenter.present(
        task_id="task-1",
        attempt_id="job-1",
        channel_id="agent:openclaw",
        agent_id="openclaw",
        result_kind=ResultKind.SUCCESS,
        full_text=raw,
        now=NOW,
    )

    assert envelope.full_text == raw
    assert envelope.spoken_text == raw
    assert envelope.continuation_available is False
    assert envelope.contract_version == COMMUNICATION_CONTRACT_VERSION


def test_presenter_strips_speech_hostile_formatting_from_spoken_text_only():
    """Markdown emphasis is voice-hostile but must not alter the canonical record."""
    presenter = ResultPresenter()
    raw = "**Result:** all `3` tasks are done.\n\n# Summary\nEverything checks out."

    envelope = presenter.present(
        task_id="task-1",
        attempt_id="job-1",
        channel_id="agent:openclaw",
        agent_id="openclaw",
        result_kind=ResultKind.SUCCESS,
        full_text=raw,
        now=NOW,
    )

    assert envelope.full_text == raw
    assert "**" not in envelope.spoken_text
    assert "`" not in envelope.spoken_text
    assert "Result:" in envelope.spoken_text
    assert "Everything checks out." in envelope.spoken_text


def test_full_text_survives_segmentation_of_a_long_result():
    """A result over the speech budget keeps its entire content in full_text."""
    presenter = ResultPresenter(segment_chars=80)
    paragraph_one = "First finding. " * 6
    paragraph_two = "Second finding. " * 6
    raw = f"{paragraph_one}\n\n{paragraph_two}"

    envelope = presenter.present(
        task_id="task-1",
        attempt_id="job-1",
        channel_id="agent:openclaw",
        agent_id="openclaw",
        result_kind=ResultKind.SUCCESS,
        full_text=raw,
        now=NOW,
    )

    assert envelope.full_text == raw
    assert len(envelope.spoken_text) <= 80
    assert envelope.continuation_available is True
    assert paragraph_two.strip() not in envelope.spoken_text


def test_segmentation_never_splits_mid_sentence_when_a_boundary_exists():
    """A segment ends at a sentence boundary rather than an arbitrary character cut."""
    presenter = ResultPresenter(segment_chars=40)
    raw = "Short one. Another short sentence here. A third one follows after that."

    envelope = presenter.present(
        task_id="task-1",
        attempt_id="job-1",
        channel_id="agent:openclaw",
        agent_id="openclaw",
        result_kind=ResultKind.SUCCESS,
        full_text=raw,
        now=NOW,
    )

    assert envelope.spoken_text in {"Short one.", "Short one. Another short sentence here."}
    assert envelope.spoken_text.endswith(".")


@pytest.mark.parametrize(
    ("failure_kind", "has_alternative", "expected_kind"),
    [
        ("quota", True, RECOVERY_REASSIGN),
        ("quota", False, RECOVERY_MANUAL),
        ("rate_limit", True, RECOVERY_REASSIGN),
        ("capacity", True, RECOVERY_REASSIGN),
        ("auth", True, RECOVERY_REASSIGN),
        ("auth", False, RECOVERY_MANUAL),
        ("", True, RECOVERY_REASSIGN),  # empty failure_kind normalizes to dispatch_failure
        ("", False, RECOVERY_MANUAL),
        ("timeout", False, RECOVERY_RETRY),
        ("interactive_prompt", False, RECOVERY_CLARIFY),
        ("something_unrecognized", True, RECOVERY_MANUAL),
    ],
)
def test_classify_recovery_maps_failure_signals_to_a_recovery_path(
    failure_kind, has_alternative, expected_kind
):
    """Each documented Butler recovery trigger resolves to its designed path."""
    decision = classify_recovery(
        failure_kind,
        detail="detail",
        has_alternative_candidate=has_alternative,
        candidate_agent_id="hermes" if has_alternative else None,
    )

    assert decision.kind == expected_kind


def test_reassignment_always_carries_a_verified_candidate():
    """Reassignment never guesses at a substitute agent."""
    decision = classify_recovery(
        "quota", has_alternative_candidate=True, candidate_agent_id="hermes"
    )

    assert decision.kind == RECOVERY_REASSIGN
    assert decision.candidate_agent_id == "hermes"


def test_recovery_decision_rejects_reassignment_without_a_candidate():
    """Constructing a reassign decision with no candidate is a programming error."""
    with pytest.raises(ValueError, match="candidate_agent_id"):
        RecoveryDecision(RECOVERY_REASSIGN, "quota", "detail", candidate_agent_id=None)


def test_recovery_decision_rejects_an_unknown_kind():
    """An unrecognized recovery kind fails fast instead of silently no-op'ing."""
    with pytest.raises(ValueError, match="Unknown recovery kind"):
        RecoveryDecision("improvise", "quota", "detail")
