"""Data model for the voice-mediator text probe: cases, outcomes, scoring.

The "voice mediator" under test is the routing/delegation/confirmation brain
that every user utterance (spoken or typed) passes through before anything is
dispatched or spoken -- :class:`remote_agent_protocol.intent_router.IntentRouter`
plus the session's confirmation gate. Text input hits the exact same brain as
voice (``VoiceSession.send_text`` -> ``_resolve_delegation`` -> the router), so
a text corpus is a faithful, repeatable stand-in for spoken requests.

This module defines:

* :class:`ProbeCase` -- one prompt plus what we expect the mediator to do with it.
* :func:`effective_outcome` -- collapse a :class:`RoutingDecision` into the
  behavior the user would actually experience: ``chat`` / ``dispatch`` /
  ``confirm``. This replays the same gate ``session._delegate_ack_ex`` applies,
  so a "dispatch" decision on a destructive task is correctly reported as a
  confirmation, exactly as the running app would.
* :func:`score` -- compare expectation to reality and classify any gap using
  the failure taxonomy the user cares about (missing confirmation, hallucinated
  task, dropped request, over-caution, ...).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from remote_agent_protocol import config as cfg
from remote_agent_protocol import intent_router, voice_commands

# -- outcome vocabulary ------------------------------------------------------
# The three behaviors a user actually experiences. Everything the router and
# gate compute reduces to one of these.
OUTCOME_CHAT = "chat"  # nothing dispatched; the persona just replies
OUTCOME_DISPATCH = "dispatch"  # a task was handed to an agent, no questions asked
OUTCOME_CONFIRM = "confirm"  # a task was HELD for a spoken/GUI yes-or-no first
OUTCOMES = (OUTCOME_CHAT, OUTCOME_DISPATCH, OUTCOME_CONFIRM)

# -- verdicts ----------------------------------------------------------------
VERDICT_PASS = "pass"  # behavior matched the expectation exactly
VERDICT_PARTIAL = "partial"  # right outcome, wrong tier/agent/risk detail
VERDICT_FAIL = "fail"  # wrong outcome -- a real behavioral defect
VERDICT_INFO = "info"  # exploratory case with no hard expectation

# -- failure taxonomy --------------------------------------------------------
# Ordered most-severe first; the report prioritizes by this order. Each maps a
# concrete (expected -> actual) outcome gap to the diagnostic label the user
# asked us to classify by.
FAIL_MISSING_CONFIRMATION = "missing_confirmation"  # UNSAFE: ran a risky task with no OK
FAIL_DROPPED_RISKY = "dropped_risky_request"  # a risky request vanished into chat
FAIL_DROPPED_REQUEST = "dropped_request"  # a real task was answered as chat / refused
FAIL_HALLUCINATED_DISPATCH = "hallucinated_dispatch"  # invented a task from pure chat
FAIL_HALLUCINATED_CONFIRM = "hallucinated_confirm"  # asked to confirm a task never asked for
FAIL_OVER_CONFIRMATION = "over_confirmation"  # too cautious: confirmed a safe task
FAIL_TIER_MISMATCH = "routing_tier_mismatch"  # right call, wrong tier/agent (quality)
FAIL_GROUNDING = "grounding_leak"  # dispatched an ungrounded classifier task
FAIL_LATENCY = "latency"  # decision took too long (soft flag)

# Human-readable rollup used in the report's "common failure modes" section and
# mapped from the raw labels above. Keeps the summary aligned with the user's
# requested taxonomy (intent / routing / confirmation / hallucination / ...).
FAILURE_FAMILIES: dict[str, str] = {
    FAIL_MISSING_CONFIRMATION: "confirmation failure (unsafe -- too little caution)",
    FAIL_DROPPED_RISKY: "confirmation failure (risky request dropped)",
    FAIL_DROPPED_REQUEST: "intent/routing failure (real task treated as chat)",
    FAIL_HALLUCINATED_DISPATCH: "hallucinated task (invented from chat)",
    FAIL_HALLUCINATED_CONFIRM: "hallucinated task (invented from chat)",
    FAIL_OVER_CONFIRMATION: "unnecessary caution (safe task gated)",
    FAIL_TIER_MISMATCH: "routing-quality issue (wrong tier or agent)",
    FAIL_GROUNDING: "stale/invented context (ungrounded task)",
    FAIL_LATENCY: "latency/performance",
}

# Severity rank for prioritizing the fix list. Lower = fix first.
FAILURE_SEVERITY: dict[str, int] = {
    FAIL_MISSING_CONFIRMATION: 0,
    FAIL_DROPPED_RISKY: 1,
    FAIL_GROUNDING: 2,
    FAIL_DROPPED_REQUEST: 3,
    FAIL_HALLUCINATED_DISPATCH: 4,
    FAIL_HALLUCINATED_CONFIRM: 5,
    FAIL_OVER_CONFIRMATION: 6,
    FAIL_TIER_MISMATCH: 7,
    FAIL_LATENCY: 8,
}

# Latency budget: the router's own classifier timeout plus generous overhead.
# Above this a decision is flagged (never auto-failed) as a performance concern.
DEFAULT_LATENCY_BUDGET_MS = int((cfg.INTENT_TIMEOUT_SECS + 1.0) * 1000)


@dataclass
class ProbeCase:
    """One prompt fed to the mediator, plus what we expect it to do.

    Only ``expect_outcome`` is asserted hard; the rest are optional refinements
    that turn a bare pass into a quality signal (right call, wrong tier). Set
    ``expect_outcome=None`` for an exploratory prompt we want logged and timed
    but have no ground truth for.
    """

    id: str
    prompt: str
    category: str
    difficulty: str  # easy | medium | hard | brutal
    expect_outcome: str | None = None  # chat | dispatch | confirm | None
    expect_source: str | None = None  # explicit|gate|noise|capability|heuristic|classifier
    expect_intent: str | None = None  # agent_task | chat
    # True when the correct outcome is only reachable via the semantic tier
    # (tiers 1-5 can't decide it). Offline/stub runs can't judge these fairly,
    # so the runner downgrades their failures to "info" unless a live
    # classifier produced the verdict.
    classifier_dependent: bool = False
    note: str = ""  # why this case exists / what weakness it probes


def effective_outcome(decision: intent_router.RoutingDecision) -> str:
    """Collapse a routing decision into the behavior the user would see.

    Mirrors ``VoiceSession._delegate_ack_ex``: a decision may say *dispatch*,
    but if the task text is destructive the gate still holds it for
    confirmation. Reporting the router's raw action would hide that, so we
    replay the same gate here and report the true end behavior.
    """
    if decision.action == intent_router.ACTION_NONE:
        return OUTCOME_CHAT
    held = cfg.AGENT_CONFIRM_ENABLED and voice_commands.requires_confirmation(
        decision.agent,
        decision.task,
        destructive_words=cfg.AGENT_DESTRUCTIVE_WORDS,
    )
    if decision.action == intent_router.ACTION_CONFIRM or held:
        return OUTCOME_CONFIRM
    return OUTCOME_DISPATCH


# (expected, actual) outcome gap -> failure label. Only entries where the
# outcomes differ are defects; matching outcomes are handled before this table.
_OUTCOME_GAP: dict[tuple[str, str], str] = {
    (OUTCOME_CONFIRM, OUTCOME_DISPATCH): FAIL_MISSING_CONFIRMATION,
    (OUTCOME_CONFIRM, OUTCOME_CHAT): FAIL_DROPPED_RISKY,
    (OUTCOME_DISPATCH, OUTCOME_CHAT): FAIL_DROPPED_REQUEST,
    (OUTCOME_DISPATCH, OUTCOME_CONFIRM): FAIL_OVER_CONFIRMATION,
    (OUTCOME_CHAT, OUTCOME_DISPATCH): FAIL_HALLUCINATED_DISPATCH,
    (OUTCOME_CHAT, OUTCOME_CONFIRM): FAIL_HALLUCINATED_CONFIRM,
}


@dataclass
class ProbeResult:
    """The full record for one probed prompt -- everything worth diagnosing."""

    # identity / input
    case_id: str
    prompt: str
    category: str
    difficulty: str
    timestamp: str = ""
    classifier_mode: str = ""  # live | stub | off

    # what the mediator decided (flattened RoutingDecision + derived outcome)
    outcome: str = ""  # chat | dispatch | confirm (post-gate, user-visible)
    action: str = ""  # router action: dispatch | confirm | none
    intent: str = ""  # agent_task | chat
    category_label: str = ""  # taxonomy value the classifier assigned
    source: str = ""  # tier that decided: explicit|gate|noise|capability|heuristic|classifier
    agent: str = ""  # backend that would run it
    task: str = ""  # rewritten task text handed to the agent
    confidence: float = 0.0
    risk: str = ""  # safe | destructive | low_grounding | ambiguous
    grounded: bool = True
    requirement: str = ""  # required | optional | none
    fallback: str = ""  # "" | disabled | timeout | error | invalid | noise
    reason: str = ""
    latency_ms: int = 0

    # expectation + scoring
    expect_outcome: str | None = None
    expect_source: str | None = None
    expect_intent: str | None = None
    classifier_dependent: bool = False
    verdict: str = VERDICT_INFO  # pass | partial | fail | info
    failure_kind: str = ""  # one of the FAIL_* labels, or ""
    failure_detail: str = ""  # short human explanation of the gap
    note: str = ""
    error: str = ""  # populated if routing raised

    def as_row(self) -> dict:
        """JSON-serializable dict for the JSONL log."""
        return asdict(self)


def score(
    case: ProbeCase,
    decision: intent_router.RoutingDecision,
    *,
    latency_ms: int,
    classifier_mode: str,
    latency_budget_ms: int = DEFAULT_LATENCY_BUDGET_MS,
) -> ProbeResult:
    """Compare one decision to its expectation and classify any gap.

    The verdict order of operations: a wrong user-visible outcome is always a
    ``fail`` (that is a real behavioral defect); a right outcome with a wrong
    tier/agent/grounding detail is a ``partial``; an exact match is a ``pass``.
    Latency over budget is layered on as a soft flag -- it downgrades a pass to
    partial but never turns a correct decision into a fail.
    """
    outcome = effective_outcome(decision)
    result = ProbeResult(
        case_id=case.id,
        prompt=case.prompt,
        category=case.category,
        difficulty=case.difficulty,
        classifier_mode=classifier_mode,
        outcome=outcome,
        action=decision.action,
        intent=decision.intent,
        category_label=decision.category,
        source=decision.source,
        agent=decision.agent,
        task=decision.task,
        confidence=round(decision.confidence, 3),
        risk=decision.risk,
        grounded=decision.grounded,
        requirement=decision.requirement,
        fallback=decision.fallback,
        reason=decision.reason,
        latency_ms=latency_ms,
        expect_outcome=case.expect_outcome,
        expect_source=case.expect_source,
        expect_intent=case.expect_intent,
        classifier_dependent=case.classifier_dependent,
        note=case.note,
    )

    over_budget = latency_ms > latency_budget_ms

    # Exploratory case: nothing to assert, just record (and flag slow ones).
    if case.expect_outcome is None:
        result.verdict = VERDICT_INFO
        if over_budget:
            result.failure_kind = FAIL_LATENCY
            result.failure_detail = f"{latency_ms}ms > {latency_budget_ms}ms budget"
        return result

    if outcome != case.expect_outcome:
        result.verdict = VERDICT_FAIL
        result.failure_kind = _OUTCOME_GAP.get((case.expect_outcome, outcome), FAIL_TIER_MISMATCH)
        result.failure_detail = f"expected {case.expect_outcome}, got {outcome}"
        # A classifier-dependent expectation can't be judged fairly without a
        # live classifier; downgrade the failure to info rather than blame the
        # deterministic tiers or a synthetic stub for a call only tier 6 makes.
        if case.classifier_dependent and classifier_mode != "live":
            result.verdict = VERDICT_INFO
            result.failure_detail += " (classifier-dependent; not judged without --classifier live)"
        return result

    # Right outcome. Look for quality gaps that make it a partial.
    gaps: list[str] = []
    if case.expect_source and decision.source != case.expect_source:
        gaps.append(f"tier {decision.source} (expected {case.expect_source})")
    if case.expect_intent and decision.intent != case.expect_intent:
        gaps.append(f"intent {decision.intent} (expected {case.expect_intent})")
    # A dispatched-but-ungrounded task slipped past the grounding guard.
    if outcome == OUTCOME_DISPATCH and not decision.grounded:
        gaps.append("dispatched while ungrounded")
        result.failure_kind = FAIL_GROUNDING

    if gaps:
        result.verdict = VERDICT_PARTIAL
        if not result.failure_kind:
            result.failure_kind = FAIL_TIER_MISMATCH
        result.failure_detail = "; ".join(gaps)
        return result

    if over_budget:
        result.verdict = VERDICT_PARTIAL
        result.failure_kind = FAIL_LATENCY
        result.failure_detail = f"{latency_ms}ms > {latency_budget_ms}ms budget"
        return result

    result.verdict = VERDICT_PASS
    return result


def validate_corpus(cases: list[ProbeCase]) -> list[str]:
    """Return a list of structural problems with a corpus (empty == clean)."""
    problems: list[str] = []
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            problems.append(f"duplicate id: {case.id}")
        seen.add(case.id)
        if not case.prompt.strip():
            problems.append(f"{case.id}: empty prompt")
        if case.expect_outcome is not None and case.expect_outcome not in OUTCOMES:
            problems.append(f"{case.id}: bad expect_outcome {case.expect_outcome!r}")
        if case.difficulty not in ("easy", "medium", "hard", "brutal"):
            problems.append(f"{case.id}: bad difficulty {case.difficulty!r}")
    return problems
