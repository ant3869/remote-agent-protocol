"""Pure orchestration-risk scoring.

This is a TUNABLE HEURISTIC, not an authoritative formula -- the weights and
thresholds below are starting points the user explicitly asked to keep
adjustable (``config.ORCHESTRATION_RISK_WEIGHTS_JSON`` and friends), informed
over time by ``orchestration.telemetry`` data. Nothing in this module (or
anywhere else in the package) adjusts its own weights automatically; that is
a human decision.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from remote_agent_protocol.orchestration.models import DEFAULT_RISK_WEIGHTS, RiskFactors, Route

_FACTOR_NAMES = tuple(DEFAULT_RISK_WEIGHTS)

# -- measurable turn signals --------------------------------------------------
#
# Each factor below is read off what exists when the orchestrator runs: the
# utterance, the routing decision that produced it, and the configured harness
# names. A factor that cannot be measured there is worse than absent -- it
# holds weight while contributing a constant, which compresses every score
# toward the floor and puts the cloud band out of reach.

_ANAPHORA_RE = re.compile(
    r"\b(?:it|its|that|this|those|these|them|they|"
    r"the (?:same|previous|last|other) one|again)\b",
    re.IGNORECASE,
)
_PRIOR_WORK_RE = re.compile(
    r"\b(?:you just|earlier|before|last time|previously|a moment ago|"
    r"(?:the|that) (?:job|task|run|agent|result|output|findings|report))\b",
    re.IGNORECASE,
)
_RESULT_INTERPRETATION_RE = re.compile(
    r"\b(?:what did .{0,24}?(?:find|say|do|return)"
    r"|what .{0,30}?(?:found|returned|reported|came back)"
    r"|did it\b|what happened|how did it go"
    r"|(?:the|its) (?:result|output|findings|report)"
    r"|summari[sz]e|explain (?:that|it|the result)"
    r"|was it (?:ok|okay|successful|right))\b",
    re.IGNORECASE,
)
# A target named only relatively has to be resolved against earlier turns;
# one carrying its own path does not.
_UNRESOLVED_TARGET_RE = re.compile(
    r"\b(?:the|that|this) (?:file|repo|repository|project|folder|directory|"
    r"branch|script|module|function|test|change)s?\b",
    re.IGNORECASE,
)
_EXPLICIT_TARGET_RE = re.compile(
    r"[\w./\-]+\.(?:py|js|ts|tsx|md|json|ya?ml|txt|toml|cfg|ini|rs|go)\b"
    r"|[A-Za-z]:[\/]"
    r"|(?:^|\s)/\w+/",
)
_CONSTRAINT_RE = re.compile(
    r"\b(?:without|but|only|don'?t|do not|never|except|unless|if|when|"
    r"before|after|while|make sure|ensure|instead of|rather than|keep|"
    r"preserve|avoid|must not|as long as)\b",
    re.IGNORECASE,
)
_SEQUENCE_RE = re.compile(
    r"\b(?:then|after that|afterwards|next|first|second|third|finally|"
    r"followed by|once (?:that|it|they) (?:is|are) done)\b",
    re.IGNORECASE,
)
_MULTIMODAL_RE = re.compile(
    r"\b(?:look at|analyze|analyse|describe|read) (?:this|the) "
    r"(?:image|photo|screenshot|picture|video frame|diagram)\b",
    re.IGNORECASE,
)

# How far each source tier is from having been told outright which harness.
_HARNESS_UNCERTAINTY_BASE = {
    "explicit": 0.05,
    "capability": 0.30,
    "heuristic": 0.40,
    "classifier": 0.60,
}
# Safety tiers that carry a constraint the wording may not spell out.
_RISK_TIER_CONSTRAINT_FLOOR = {"destructive": 0.60, "low_grounding": 0.35, "ambiguous": 0.30}


def _saturate(count: float, full: float) -> float:
    """0.0 with no signal, rising to 1.0 once ``full`` hits are present."""
    return min(1.0, max(0.0, count) / full)


def context_dependency(text: str, *, grounded: bool = True) -> float:
    """How much of this turn's meaning lives in turns that came before it."""
    anaphora = len(_ANAPHORA_RE.findall(text))
    prior_work = len(_PRIOR_WORK_RE.findall(text))
    unresolved = bool(_UNRESOLVED_TARGET_RE.search(text)) and not _EXPLICIT_TARGET_RE.search(text)
    signal = (
        0.40 * _saturate(anaphora, 3) + 0.40 * _saturate(prior_work, 2) + 0.20 * float(unresolved)
    )
    # An ungrounded decision depends on context by definition, whatever its
    # wording scored. This is a floor, not the whole reading, so turns that
    # reach the orchestrator already grounded can still score high on wording.
    floor = 0.0 if grounded else 0.70
    return round(min(1.0, max(signal, floor)), 3)


def constraint_complexity(text: str, *, risk_tier: str = "") -> float:
    """How many distinct conditions the work has to respect."""
    distinct = {match.group(0).lower() for match in _CONSTRAINT_RE.finditer(text)}
    floor = _RISK_TIER_CONSTRAINT_FLOOR.get(risk_tier, 0.0)
    return round(min(1.0, max(_saturate(len(distinct), 4), floor)), 3)


def sequential_coordination(text: str) -> float:
    """Whether several ordered actions must be coordinated, not one taken."""
    return round(_saturate(len(_SEQUENCE_RE.findall(text)), 3), 3)


def result_interpretation_need(text: str) -> float:
    """Whether answering means reading and judging work that already ran."""
    return round(_saturate(len(_RESULT_INTERPRETATION_RE.findall(text)), 2), 3)


def named_harnesses(text: str, backends: Iterable[str], aliases: Mapping[str, str] | None) -> int:
    """How many configured harnesses this utterance actually names."""
    lowered = text.lower()
    found: set[str] = set()
    for name in backends or ():
        if re.search(rf"\b{re.escape(name.lower())}\b", lowered):
            found.add(name)
    for phrase, name in (aliases or {}).items():
        if phrase and re.search(rf"\b{re.escape(phrase.lower())}\b", lowered):
            found.add(name)
    return len(found)


def harness_selection_uncertainty(*, source: str, confidence: float, named: int = 0) -> float:
    """How much of a choice the harness pick actually was."""
    signal = _HARNESS_UNCERTAINTY_BASE.get(source, 0.30)
    if source != "explicit":
        if named >= 2:
            # Several were named and none was taken outright: a real decision
            # between them, which is exactly what cloud reasoning is for.
            signal = max(signal, 0.85)
        signal += 0.25 * max(0.0, 0.9 - confidence)
    return round(min(1.0, signal), 3)


def routing_disagreement(*, confidence: float, fallback: str = "", risk_tier: str = "") -> float:
    """How unsure the local tiers themselves are about this turn.

    Confidence alone collapses to nothing here, because the classifier answers
    with high confidence nearly always. A classifier that failed to answer at
    all is the stronger signal, and it is one the score never used to see.
    """
    signal = max(0.0, 1.0 - confidence)
    if fallback in {"timeout", "error", "invalid"}:
        signal = max(signal, 0.80)
    if risk_tier in {"ambiguous", "low_grounding"}:
        signal = max(signal, 0.60)
    return round(min(1.0, signal), 3)


def multimodal_capability_need(text: str) -> float:
    """Whether the turn needs a capability the local path may not have."""
    return 0.6 if _MULTIMODAL_RE.search(text) else 0.0


def validate_weights(weights: dict[str, float]) -> dict[str, float]:
    """Confirm ``weights`` covers every factor and sums to ~1.0; return it as-is."""
    missing = set(_FACTOR_NAMES) - set(weights)
    if missing:
        raise ValueError(f"risk weights missing factors: {sorted(missing)}")
    total = sum(weights[name] for name in _FACTOR_NAMES)
    if not 0.99 <= total <= 1.01:
        raise ValueError(f"risk weights must sum to ~1.0, got {total:.3f}")
    return weights


def score_risk(factors: RiskFactors, weights: dict[str, float] | None = None) -> float:
    """Weighted sum of ``factors``, clamped to [0.0, 1.0]."""
    weights = weights if weights is not None else DEFAULT_RISK_WEIGHTS
    total = sum(getattr(factors, name) * weights[name] for name in _FACTOR_NAMES)
    return min(max(total, 0.0), 1.0)


def classify_route(score: float, *, local_ceiling: float = 0.39, cloud_floor: float = 0.65) -> str:
    """Map a risk score to a :class:`~orchestration.models.Route` value.

    ``score <= local_ceiling`` -> local. ``score >= cloud_floor`` -> cloud.
    Between the two: local, unless a hard trigger elsewhere in
    ``orchestrator.py`` forces cloud despite the moderate score.
    """
    if score <= local_ceiling:
        return Route.LOCAL.value
    if score < cloud_floor:
        return Route.LOCAL_UNLESS_CLOUD_REQUIRED.value
    return Route.CLOUD.value
