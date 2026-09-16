"""Plain data for the orchestration package -- no I/O, no logic.

Kept separate from risk.py/orchestrator.py so the data shapes are trivially
importable (including by tests) without pulling in aiohttp, the Copilot SDK,
or AgentBridge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class OrchestrationMode(StrEnum):
    """Global/per-persona orchestration mode."""

    LOCAL = "local"
    CLOUD = "cloud"
    HYBRID = "hybrid"


class Route(StrEnum):
    """Risk-tier classification.

    ``LOCAL_UNLESS_CLOUD_REQUIRED`` is a hybrid-only middle tier: local
    unless a hard trigger (orchestrator.py) forces cloud.
    """

    LOCAL = "local"
    LOCAL_UNLESS_CLOUD_REQUIRED = "local_unless_cloud_required"
    CLOUD = "cloud"


# The tunable orchestration-risk inputs. Each is a 0.0-1.0 signal, not a
# probability in any formal sense -- see risk.py's module docstring: this is a
# tunable heuristic, not authoritative math.
#
# Every factor here must be *measurable* from what exists when evaluate() runs.
# A factor that cannot move spends weight without ever expressing difficulty,
# and the score can then never reach the band it is compared against.
DEFAULT_RISK_WEIGHTS: dict[str, float] = {
    "context_dependency": 0.22,
    "constraint_complexity": 0.20,
    "harness_selection_uncertainty": 0.18,
    "result_interpretation_need": 0.15,
    "sequential_coordination": 0.12,
    "routing_disagreement": 0.08,
    "multimodal_capability_need": 0.03,
    "previous_routing_failure": 0.02,
}


@dataclass(frozen=True)
class RiskFactors:
    """One utterance's orchestration-risk inputs, each 0.0-1.0.

    Parameters:
        context_dependency: How much of the turn's meaning lives in earlier
            turns -- pronouns standing in for earlier subjects, references to
            previous work, targets named only relatively.
        constraint_complexity: How many distinct conditions the work has to
            respect, with a floor for turns whose safety tier carries an
            implicit one.
        harness_selection_uncertainty: How much of a choice the harness pick
            actually was: named outright, inferred, or contested.
        result_interpretation_need: Whether answering means reading and judging
            work that has already run.
        sequential_coordination: Whether several ordered actions have to be
            coordinated rather than one action taken.
        routing_disagreement: How unsure the local tiers themselves are,
            including a classifier that never answered at all.
        multimodal_capability_need: Whether the turn needs a capability the
            local path may not have. Also a hard trigger.
        previous_routing_failure: Whether this turn is a retry of one that was
            routed wrongly. Also a hard trigger.
    """

    context_dependency: float = 0.0
    constraint_complexity: float = 0.0
    harness_selection_uncertainty: float = 0.0
    result_interpretation_need: float = 0.0
    sequential_coordination: float = 0.0
    routing_disagreement: float = 0.0
    multimodal_capability_need: float = 0.0
    previous_routing_failure: float = 0.0


@dataclass
class StructuredDecision:
    """The persisted pre-dispatch decision.

    Execution MUST consume this object rather than re-inferring the harness
    later -- that is what prevents "claimed the wrong harness ran" and lets a
    dispatch be re-checked against what was actually decided.
    """

    intent: str  # "delegate" | "chat"
    target_harness: str
    route: str  # models.Route value actually used ("local" or "cloud")
    intent_confidence: float
    routing_confidence: float
    task: str
    constraints: list[str] = field(default_factory=list)
    reason_summary: str = ""
    risk_score: float = 0.0
    decision_id: str = ""
