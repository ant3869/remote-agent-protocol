"""PersonaOrchestrator -- the Local/Cloud/Hybrid orchestration lifecycle.

UNDERSTAND -> ROUTE -> DISPATCH -> TRACK -> RELAY, built ON TOP OF (never
replacing) ``intent_router.RoutingDecision`` and ``agent_bridge.AgentBridge``.
Every stage here is additive: ``session.py`` still owns the actual dispatch
call, ``AgentBridge`` still owns job truth, ``intent_router`` still owns the
tiered dispatch/confirm/none decision. This module adds:

  * a risk-scored decision (orchestration/risk.py -- a tunable heuristic, not
    authoritative math) about whether ORCHESTRATION REASONING itself should
    escalate to a cloud provider;
  * the persisted :class:`~orchestration.models.StructuredDecision` that
    execution must consume rather than re-infer;
  * concurrency/duplicate admission before any dispatch;
  * telemetry, kept separate from persona memory.

Post-dispatch INTERPRET/RELAY of a harness's actual status and result stays
where it already lived and is tested: ``session._announce_agent_job``, via
``agent_bridge.requests_confirmation`` / ``result_detail`` / ``announcement``
/ ``detect_provider_failure``. This module does not re-implement that --
:meth:`record_outcome` only closes out telemetry for a job those helpers have
already classified as terminal.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from loguru import logger

from remote_agent_protocol import agent_bridge
from remote_agent_protocol import config as cfg
from remote_agent_protocol.orchestration import risk
from remote_agent_protocol.orchestration.concurrency import (
    ConcurrencyGuard,
    default_guard,
    normalize_task,
)
from remote_agent_protocol.orchestration.models import RiskFactors, StructuredDecision
from remote_agent_protocol.orchestration.providers.base import ModelCapability, ModelProvider
from remote_agent_protocol.orchestration.quota import evaluate as evaluate_quota
from remote_agent_protocol.orchestration.telemetry import TelemetryEvent, TelemetryRecorder

if TYPE_CHECKING:
    from remote_agent_protocol.intent_router import RoutingDecision


@dataclass
class OrchestrationContext:
    """Cheap per-turn signals ``RoutingDecision`` alone doesn't carry."""

    persona: str = ""
    previous_routing_failure: bool = False


class PersonaOrchestrator:
    """Owns the risk-scored escalation decision, concurrency admission, and telemetry."""

    def __init__(
        self,
        *,
        bridge: agent_bridge.AgentBridge,
        local_provider: ModelProvider,
        cloud_provider: ModelProvider,
        telemetry: TelemetryRecorder,
        mode: str | None = None,
        persona_modes: dict[str, str] | None = None,
        quota_strategy: str | None = None,
        risk_weights: dict[str, float] | None = None,
        local_ceiling: float | None = None,
        cloud_floor: float | None = None,
        harness_confidence_floor: float | None = None,
        concurrency: ConcurrencyGuard | None = None,
    ) -> None:
        """Initialize the orchestrator; all overrides default to their config.py setting."""
        self._bridge = bridge
        self._local = local_provider
        self._cloud = cloud_provider
        self._telemetry = telemetry
        self._mode = mode or cfg.ORCHESTRATION_MODE
        self._persona_modes = (
            persona_modes if persona_modes is not None else cfg.ORCHESTRATION_PERSONA_MODES
        )
        self._quota_strategy = quota_strategy or cfg.ORCHESTRATION_QUOTA_STRATEGY
        self._weights = risk.validate_weights(risk_weights or cfg.ORCHESTRATION_RISK_WEIGHTS)
        self._local_ceiling = (
            local_ceiling if local_ceiling is not None else cfg.ORCHESTRATION_LOCAL_THRESHOLD
        )
        self._cloud_floor = (
            cloud_floor if cloud_floor is not None else cfg.ORCHESTRATION_CLOUD_THRESHOLD
        )
        self._harness_floor = (
            harness_confidence_floor
            if harness_confidence_floor is not None
            else cfg.ORCHESTRATION_HARNESS_CONFIDENCE_FLOOR
        )
        self._concurrency = concurrency or default_guard(bridge)
        # decision_id -> StructuredDecision, and (agent, normalized task) ->
        # decision_id -- the latter lets record_outcome() find the decision
        # for a job without threading a job_id through session.py's
        # fire-and-forget dispatch (AgentBridge.start() is scheduled as a
        # background task there, so the job_id isn't known synchronously).
        self._decisions: dict[str, StructuredDecision] = {}
        self._pending_by_key: dict[tuple[str, str], str] = {}
        # Last-known cloud provider status, for cheap synchronous UI polling
        # (status() below never touches the network itself).
        self._cloud_status_cache: dict = {
            "available": None,
            "authenticated": None,
            "detail": "not checked yet",
            "quota": None,
            "models": [],
            "reasoning_model": "",
        }
        self._local_status_cache: dict = {"available": None, "detail": "not checked yet"}

    def mode_for(self, persona: str) -> str:
        """Effective mode for ``persona``: its override, or the global default."""
        return self._persona_modes.get(persona, self._mode)

    # -- runtime controls (UI) ---------------------------------------------------

    def set_mode(self, mode: str) -> None:
        """Change the global orchestration mode at runtime."""
        if mode not in {"local", "cloud", "hybrid"}:
            raise ValueError(f"unknown orchestration mode: {mode!r}")
        self._mode = mode

    def set_quota_strategy(self, strategy: str) -> None:
        """Change the quota strategy at runtime."""
        if strategy not in {"economy", "balanced", "performance", "cloud_preferred"}:
            raise ValueError(f"unknown quota strategy: {strategy!r}")
        self._quota_strategy = strategy

    def set_persona_mode(self, persona: str, mode: str | None) -> None:
        """Set (or, with ``mode=None``, clear) a per-persona mode override."""
        if mode is None:
            self._persona_modes.pop(persona, None)
            return
        if mode not in {"local", "cloud", "hybrid"}:
            raise ValueError(f"unknown orchestration mode: {mode!r}")
        self._persona_modes[persona] = mode

    async def refresh_provider_status(self) -> dict:
        """Actively probe both providers; updates the caches read by status().

        Both are probed together because the UI shows them side by side, and a
        panel reporting one provider as "not checked yet" while the other is
        live reads as a bug rather than as two independent checks.
        """
        local_health = await self._local.health()
        self._local_status_cache = {
            "available": local_health.available,
            "detail": local_health.detail,
        }
        health = await self._cloud.health()
        quota = await self._cloud.quota()
        self._cloud_status_cache = {
            "available": health.available,
            "authenticated": health.authenticated,
            "detail": health.detail,
            "quota": quota,
            # Cached by the health probe above, so this costs no extra call.
            "models": (await self._cloud.list_models()) if health.available else [],
            "reasoning_model": self._cloud_reasoning_model(),
        }
        return self._cloud_status_cache

    def _cloud_reasoning_model(self) -> str:
        """Which cloud model orchestration reasoning would actually use, if knowable."""
        model_for = getattr(self._cloud, "model_for", None)
        if model_for is None:
            return ""
        try:
            return str(model_for(ModelCapability.REASONING))
        except Exception:
            return ""

    def status(self) -> dict:
        """Synchronous snapshot for a UI poll -- never touches the network."""
        return {
            "mode": self._mode,
            "persona_modes": dict(self._persona_modes),
            "quota_strategy": self._quota_strategy,
            "local_provider": self._local.name,
            "local_status": dict(self._local_status_cache),
            "cloud_provider": self._cloud.name,
            "cloud_status": dict(self._cloud_status_cache),
            "telemetry": self._telemetry.summary(),
            # Raw route/outcome rows, newest first -- lets a human correlate
            # "why local vs cloud" with what actually happened to that job,
            # without waiting for the aggregate summary() percentages to shift.
            "recent_events": self._telemetry.recent(20),
        }

    # -- UNDERSTAND + ROUTE ---------------------------------------------------

    def _factors(self, decision: RoutingDecision, ctx: OrchestrationContext) -> RiskFactors:
        """Read this turn's difficulty off the decision and the words themselves.

        Every reading here comes from something that varies per turn. The
        scoring lives in :mod:`~orchestration.risk` so each signal can be
        checked on its own.
        """
        haystack = f"{decision.task} {decision.text}"
        return RiskFactors(
            context_dependency=risk.context_dependency(haystack, grounded=decision.grounded),
            constraint_complexity=risk.constraint_complexity(haystack, risk_tier=decision.risk),
            harness_selection_uncertainty=risk.harness_selection_uncertainty(
                source=decision.source,
                confidence=decision.confidence,
                named=risk.named_harnesses(haystack, cfg.AGENT_BACKENDS, cfg.AGENT_SPOKEN_ALIASES),
            ),
            result_interpretation_need=risk.result_interpretation_need(haystack),
            sequential_coordination=risk.sequential_coordination(haystack),
            routing_disagreement=risk.routing_disagreement(
                confidence=decision.confidence,
                fallback=decision.fallback,
                risk_tier=decision.risk,
            ),
            multimodal_capability_need=risk.multimodal_capability_need(haystack),
            previous_routing_failure=1.0 if ctx.previous_routing_failure else 0.0,
        )

    async def evaluate(
        self, decision: RoutingDecision, ctx: OrchestrationContext | None = None
    ) -> StructuredDecision:
        """UNDERSTAND + ROUTE: score risk, decide local vs cloud, persist the decision.

        ``decision`` must come straight from ``IntentRouter.route()`` -- this
        must never be called for cancel/correct/acknowledgment turns, only for
        a genuine new-task routing decision, which is what keeps "cancellations
        starting new work" and "ack turns creating jobs" structurally
        impossible rather than merely discouraged.
        """
        t0 = time.perf_counter()
        ctx = ctx or OrchestrationContext()
        mode = self.mode_for(ctx.persona)
        factors = self._factors(decision, ctx)
        score = risk.score_risk(factors, self._weights)
        tier = risk.classify_route(
            score, local_ceiling=self._local_ceiling, cloud_floor=self._cloud_floor
        )

        hard_trigger = (
            factors.multimodal_capability_need >= 0.5
            or factors.previous_routing_failure >= 1.0
            or (decision.action != "none" and decision.confidence < self._harness_floor)
        )

        if mode == "local":
            want_cloud = False
        elif mode == "cloud":
            want_cloud = True
        else:  # hybrid -- a hard trigger overrides the risk-tier band entirely
            want_cloud = tier == "cloud" or hard_trigger

        used_cloud = False
        fallback = False
        target_harness = decision.agent
        reason_bits = [decision.reason]

        if want_cloud:
            quota = await self._cloud.quota()
            quota_decision = evaluate_quota(self._quota_strategy, quota)
            if hard_trigger or quota_decision.cloud_allowed:
                health = await self._cloud.health()
                if health.available and health.authenticated:
                    used_cloud = True
                else:
                    fallback = True
                    reason_bits.append(f"cloud unavailable ({health.detail}); fell back to local")
            else:
                reason_bits.append(f"quota strategy withheld cloud: {quota_decision.reason}")

        # Tie-breaker: an explicit user instruction always outranks an
        # inferred harness pick, so cloud reasoning only ever refines a
        # harness that a non-explicit tier left uncertain -- this is what
        # keeps "explicit Hermes request launches Code Puppy instead" and
        # "claims the wrong harness ran" structurally impossible.
        if used_cloud and decision.source != "explicit" and decision.action != "none":
            try:
                refined = await self._refine_harness(decision)
                if refined and refined in cfg.AGENT_BACKENDS and refined != target_harness:
                    target_harness = refined
                    reason_bits.append(f"cloud reasoning selected '{refined}'")
            except Exception as exc:
                logger.warning(f"Copilot orchestration reasoning failed, keeping local pick: {exc}")
                fallback = True

        structured = StructuredDecision(
            intent="delegate" if decision.action != "none" else "chat",
            target_harness=target_harness,
            route="cloud" if used_cloud else "local",
            intent_confidence=decision.confidence,
            routing_confidence=decision.confidence,
            task=decision.task,
            constraints=[],
            reason_summary=" -- ".join(bit for bit in reason_bits if bit),
            risk_score=score,
            decision_id=uuid.uuid4().hex[:12],
        )
        self._decisions[structured.decision_id] = structured
        route_latency_ms = (time.perf_counter() - t0) * 1000
        self._telemetry.record(
            TelemetryEvent(
                stage="route",
                route=structured.route,
                provider=self._cloud.name if used_cloud else self._local.name,
                persona=ctx.persona,
                intent=structured.intent,
                harness=target_harness,
                original_harness=decision.agent,
                risk_score=score,
                risk_factors=asdict(factors),
                reason=structured.reason_summary,
                intent_confidence=decision.confidence,
                routing_confidence=decision.confidence,
                route_latency_ms=round(route_latency_ms, 1),
                fallback=fallback,
                wrong_harness_corrected=target_harness != decision.agent,
            )
        )
        return structured

    async def _refine_harness(self, decision: RoutingDecision) -> str | None:
        prompt = (
            "You are selecting which local automation harness should run one task. "
            f"Available harnesses: {', '.join(sorted(cfg.AGENT_BACKENDS))}. "
            f"Currently selected: {decision.agent}. Task: {decision.task}. "
            "Reply with ONLY the harness name to use, nothing else."
        )
        result = await self._cloud.complete(
            prompt, capability=ModelCapability.REASONING, max_tokens=20
        )
        text = result.text.strip()
        return text.split()[0].strip(" .,'\"") if text else None

    # -- DISPATCH ---------------------------------------------------------------

    def admit(self, decision: StructuredDecision) -> tuple[bool, str]:
        """Concurrency/duplicate admission -- call right before actually dispatching."""
        allowed, reason = self._concurrency.admit(decision.target_harness, decision.task)
        key = (decision.target_harness, normalize_task(decision.task))
        if allowed:
            self._pending_by_key[key] = decision.decision_id
        else:
            self._telemetry.record(
                TelemetryEvent(
                    stage="outcome",
                    route=decision.route,
                    harness=decision.target_harness,
                    outcome="cancelled",
                    duplicate_blocked=True,
                )
            )
        return allowed, reason

    def admit_by_task(self, agent: str, task: str) -> tuple[bool, str]:
        """Concurrency/duplicate admission for a dispatch with no StructuredDecision.

        Used at confirmation-approval relaunch, where the decision that
        originally routed the request is no longer in scope -- this checks
        the same global/per-harness caps and duplicate-task rule as
        :meth:`admit`, just without the telemetry route-attribution that
        requires a decision id.
        """
        return self._concurrency.admit(agent, task)

    # -- TRACK / RELAY (telemetry close-out) ---------------------------------------

    def record_outcome(self, job: agent_bridge.AgentJob) -> None:
        """Close out telemetry for one terminal job.

        True no-op -- records nothing -- when this job wasn't one the
        orchestrator itself routed (e.g. a manual GUI dispatch, an LLM
        marker-triggered delegation, or a confirmation relaunch admitted via
        :meth:`admit_by_task`). Mixing those into local/cloud route
        percentages would silently understate both, since neither route
        would match -- better to omit them than report a misleading number.
        """
        key = (job.agent, normalize_task(job.task))
        decision_id = self._pending_by_key.pop(key, None)
        decision = self._decisions.pop(decision_id, None) if decision_id else None
        if decision is None:
            return
        outcome = {
            agent_bridge.STATUS_DONE: "success",
            agent_bridge.STATUS_FAILED: "failed",
            agent_bridge.STATUS_CANCELLED: "cancelled",
        }.get(job.status, "")
        latency_ms = job.secs * 1000 if job.secs is not None else None
        self._telemetry.record(
            TelemetryEvent(
                stage="outcome",
                route=decision.route,
                harness=job.agent,
                outcome=outcome,
                latency_ms=latency_ms,
                fallback=job.failure_kind in {"quota", "rate_limit", "capacity"},
            )
        )
