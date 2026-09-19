"""The versioned communication contract and agent-authored result envelope.

Separates an agent's canonical full result from its voice-sized spoken
presentation while preserving authorship: ``ResultPresenter`` may remove
speech-hostile formatting and segment at semantic boundaries, but it never
summarizes, discards material findings, or speaks in place of the agent.
Butler's failure-recovery path is likewise data, not narration -- see
``RecoveryDecision`` and ``classify_recovery``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from .models import ResultKind

COMMUNICATION_CONTRACT_VERSION = 1
COMMUNICATION_CONTRACT = (
    "Speak to Ant like a trusted teammate: natural, direct, and brief, but complete. "
    "Lead with the outcome. Include every important result, decision, warning, failure, "
    "and next step. Omit internal tool chatter unless asked. Keep operational progress "
    "separate from the final answer. If blocked, ask one clear question. Never impersonate Butler."
)

DEFAULT_SPEECH_SEGMENT_CHARS = 1200

_FORMATTING_STRIP_RE = re.compile(r"[*_`#]+")
_WHITESPACE_RE = re.compile(r"[ \t]+")
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


@dataclass(frozen=True)
class AgentResultEnvelope:
    """The task-owning agent's accepted result, canonical and as-spoken.

    ``full_text`` is stored without semantic rewriting -- it is never raw
    stdout, hidden reasoning, or secret-bearing output, but it is also never
    paraphrased by Butler or any other model. ``spoken_text`` is the derived
    voice-sized presentation; when it is shorter than the cleaned full text,
    ``continuation_available`` is set so the same agent can be asked to keep
    reading rather than the detail being silently dropped.
    """

    task_id: str
    attempt_id: str
    channel_id: str
    agent_id: str
    result_kind: ResultKind
    full_text: str
    spoken_text: str
    created_at: datetime
    contract_version: int = COMMUNICATION_CONTRACT_VERSION
    artifact_refs: tuple[str, ...] = field(default_factory=tuple)
    continuation_available: bool = False

    def __post_init__(self) -> None:
        """Require an aware timestamp and non-empty canonical text."""
        _aware(self.created_at, "created_at")
        if not self.full_text.strip():
            raise ValueError("full_text must be non-empty")


class ResultPresenter:
    """Derives a voice-sized presentation from an agent's canonical result.

    Only removes formatting that is hostile to speech (markdown emphasis,
    headers, stray whitespace) and segments at a paragraph or sentence
    boundary when the cleaned text exceeds the speech budget. It never
    shortens by discarding content -- the canonical ``full_text`` on the
    resulting envelope is always the original, complete text.
    """

    def __init__(self, segment_chars: int = DEFAULT_SPEECH_SEGMENT_CHARS) -> None:
        """Configure the comfortable-speech character budget for segmentation."""
        self._segment_chars = segment_chars

    def present(
        self,
        *,
        task_id: str,
        attempt_id: str,
        channel_id: str,
        agent_id: str,
        result_kind: ResultKind,
        full_text: str,
        now: datetime,
        artifact_refs: tuple[str, ...] = (),
    ) -> AgentResultEnvelope:
        """Build the envelope for one agent-authored result."""
        cleaned = self._clean(full_text)
        spoken, continuation_available = self._segment(cleaned)
        return AgentResultEnvelope(
            task_id=task_id,
            attempt_id=attempt_id,
            channel_id=channel_id,
            agent_id=agent_id,
            result_kind=result_kind,
            full_text=full_text,
            spoken_text=spoken,
            created_at=now,
            artifact_refs=artifact_refs,
            continuation_available=continuation_available,
        )

    def _clean(self, text: str) -> str:
        stripped = _FORMATTING_STRIP_RE.sub("", text)
        stripped = _WHITESPACE_RE.sub(" ", stripped)
        return "\n".join(line.strip() for line in stripped.splitlines()).strip()

    def _segment(self, cleaned: str) -> tuple[str, bool]:
        if len(cleaned) <= self._segment_chars:
            return cleaned, False
        segment = self._fill_boundaries(_PARAGRAPH_SPLIT_RE.split(cleaned), "\n\n")
        if not segment:
            segment = self._fill_boundaries(_SENTENCE_SPLIT_RE.split(cleaned), " ")
        if not segment:
            segment = cleaned[: self._segment_chars]
        return segment, len(segment) < len(cleaned)

    def _fill_boundaries(self, pieces: list[str], joiner: str) -> str:
        segment = ""
        for piece in pieces:
            candidate = f"{segment}{joiner}{piece}" if segment else piece
            if len(candidate) > self._segment_chars:
                break
            segment = candidate
        return segment


RECOVERY_RETRY = "retry"
RECOVERY_REASSIGN = "reassign"
RECOVERY_CLARIFY = "clarify"
RECOVERY_MANUAL = "manual"
_RECOVERY_KINDS = frozenset({RECOVERY_RETRY, RECOVERY_REASSIGN, RECOVERY_CLARIFY, RECOVERY_MANUAL})

DISPATCH_FAILURE = "dispatch_failure"
_LOAD_FAILURE_KINDS = frozenset({"quota", "rate_limit", "capacity"})
_ACCESS_FAILURE_KINDS = frozenset({"auth"})
_TIMEOUT_FAILURE_KINDS = frozenset({"timeout"})
_INTERVENTION_FAILURE_KINDS = frozenset({"interactive_prompt"})
# Reassignment-eligible: the outcome and safety scope are unchanged by
# picking a different, already-qualified agent. Everything else needs a
# person's decision rather than a guessed substitute.
_REASSIGNABLE_FAILURE_KINDS = _LOAD_FAILURE_KINDS | _ACCESS_FAILURE_KINDS | {DISPATCH_FAILURE}


@dataclass(frozen=True)
class RecoveryDecision:
    """Butler's structured recovery path for one failed or stalled attempt.

    This is a decision, not narration -- Butler speaks from it, it never
    executes a reassignment on its own authority. Reassignment requires an
    already-verified ``candidate_agent_id``; RAP never invents a substitute.
    """

    kind: str
    reason_code: str
    detail: str
    candidate_agent_id: str | None = None

    def __post_init__(self) -> None:
        """Reject an unknown recovery kind or a reassignment with no candidate."""
        if self.kind not in _RECOVERY_KINDS:
            raise ValueError(f"Unknown recovery kind: {self.kind}")
        if self.kind == RECOVERY_REASSIGN and not self.candidate_agent_id:
            raise ValueError("reassign requires a candidate_agent_id")


def classify_recovery(
    failure_kind: str,
    *,
    detail: str = "",
    has_alternative_candidate: bool = False,
    candidate_agent_id: str | None = None,
) -> RecoveryDecision:
    """Map a normalized failure signal to one of Butler's recovery paths.

    Covers the design's recovery triggers: dispatch/launch failure, provider
    quota/rate-limit/capacity failure, verified access loss (``auth``),
    silence past the adapter deadline (``timeout``), and an agent stalled on
    unsupported interactive control. An empty or unrecognized ``failure_kind``
    falls back to manual intervention rather than guessing.
    """
    kind = failure_kind or DISPATCH_FAILURE
    if kind in _REASSIGNABLE_FAILURE_KINDS:
        if has_alternative_candidate and candidate_agent_id:
            return RecoveryDecision(
                RECOVERY_REASSIGN, kind, detail, candidate_agent_id=candidate_agent_id
            )
        return RecoveryDecision(RECOVERY_MANUAL, kind, detail)
    if kind in _TIMEOUT_FAILURE_KINDS:
        return RecoveryDecision(RECOVERY_RETRY, kind, detail)
    if kind in _INTERVENTION_FAILURE_KINDS:
        return RecoveryDecision(RECOVERY_CLARIFY, kind, detail)
    return RecoveryDecision(RECOVERY_MANUAL, kind, detail)
