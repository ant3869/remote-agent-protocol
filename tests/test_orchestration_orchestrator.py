"""orchestration/orchestrator.py -- PersonaOrchestrator regression suite.

Covers the orchestration-specific regressions from the spec: mode selection
(local/cloud/hybrid), the tie-breaker that an explicit harness pick can never
be overridden by cloud reasoning, hard-trigger escalation, quota-aware
withholding, graceful local fallback on Copilot unavailability, concurrency/
duplicate admission, and telemetry correctness (wrong-harness correction,
route attribution on outcome). intent_router's OWN tiered-routing regressions
(greetings never dispatching, STT noise, grounding gaps, etc.) already have
an extensive dedicated suite in tests/test_intent_router.py and are not
re-derived here -- this file tests only what PersonaOrchestrator adds on top.
"""

import unittest
from dataclasses import dataclass, field

from remote_agent_protocol.intent_router import RoutingDecision
from remote_agent_protocol.orchestration.concurrency import ConcurrencyGuard
from remote_agent_protocol.orchestration.models import DEFAULT_RISK_WEIGHTS
from remote_agent_protocol.orchestration.orchestrator import (
    OrchestrationContext,
    PersonaOrchestrator,
)
from remote_agent_protocol.orchestration.providers.base import (
    CompletionResult,
    ModelProvider,
    ProviderHealth,
)
from remote_agent_protocol.orchestration.telemetry import TelemetryRecorder


def _decision(**overrides) -> RoutingDecision:
    fields = {
        "text": "tell hermes to check the weather",
        "action": "dispatch",
        "intent": "agent_task",
        "category": "other_action",
        "requirement": "required",
        "confidence": 1.0,
        "task": "check the weather",
        "agent": "hermes",
        "reason": "user addressed the agent by name",
        "source": "explicit",
        "fallback": "",
        "grounded": True,
        "risk": "safe",
    }
    fields.update(overrides)
    return RoutingDecision(**fields)


class _FakeProvider(ModelProvider):
    def __init__(self, name, *, available=True, authenticated=True, quota=None, reply="hermes"):
        self.name = name
        self._health = ProviderHealth(available=available, authenticated=authenticated)
        self._quota = quota
        self._reply = reply
        self.complete_calls: list[str] = []
        self.health_calls = 0
        self.quota_calls = 0

    async def health(self) -> ProviderHealth:
        self.health_calls += 1
        return self._health

    async def list_models(self) -> list[str]:
        return ["fake-model"]

    async def complete(self, prompt, *, capability=None, max_tokens=400) -> CompletionResult:
        self.complete_calls.append(prompt)
        return CompletionResult(text=self._reply, model="fake-model")

    async def quota(self):
        self.quota_calls += 1
        return self._quota


class _ExplodingProvider(_FakeProvider):
    """A cloud provider that fails the test if it's ever touched -- used to
    prove local mode never reaches the cloud provider at all."""

    async def health(self):
        raise AssertionError("cloud provider must not be touched in local mode")

    async def complete(self, *args, **kwargs):
        raise AssertionError("cloud provider must not be touched in local mode")

    async def quota(self):
        raise AssertionError("cloud provider must not be touched in local mode")


@dataclass
class _FakeJob:
    agent: str
    task: str
    job_id: str = "job-1"
    status: str = "done"
    lines: list = field(default_factory=list)
    secs: float | None = 1.5
    failure_kind: str = ""


@dataclass
class _FakeBridge:
    jobs: list = field(default_factory=list)

    def active_jobs(self):
        return self.jobs


def _make_orchestrator(*, mode="hybrid", cloud=None, bridge=None, telemetry_path):
    return PersonaOrchestrator(
        bridge=bridge or _FakeBridge(),
        local_provider=_FakeProvider("local"),
        cloud_provider=cloud or _FakeProvider("copilot"),
        telemetry=TelemetryRecorder(telemetry_path),
        mode=mode,
    )


class LocalModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_mode_never_touches_cloud_provider(self, tmp_path=None):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            orch = PersonaOrchestrator(
                bridge=_FakeBridge(),
                local_provider=_FakeProvider("local"),
                cloud_provider=_ExplodingProvider("copilot"),
                telemetry=TelemetryRecorder(f"{tmp}/t.jsonl"),
                mode="local",
            )
            decision = _decision(
                confidence=0.1, grounded=False, risk="low_grounding", source="classifier"
            )
            structured = await orch.evaluate(decision)
            self.assertEqual(structured.route, "local")


class ExplicitHarnessTieBreakTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_harness_never_overridden_even_when_cloud_used(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            cloud = _FakeProvider(
                "copilot", reply="code-puppy"
            )  # would "suggest" a different harness
            orch = PersonaOrchestrator(
                bridge=_FakeBridge(),
                local_provider=_FakeProvider("local"),
                cloud_provider=cloud,
                telemetry=TelemetryRecorder(f"{tmp}/t.jsonl"),
                mode="cloud",  # force cloud usage
            )
            decision = _decision(source="explicit", agent="hermes")
            structured = await orch.evaluate(decision)
            self.assertEqual(structured.target_harness, "hermes")
            # Explicit source is never sent through _refine_harness at all.
            self.assertEqual(cloud.complete_calls, [])


class HybridRiskTests(unittest.IsolatedAsyncioTestCase):
    async def test_low_risk_confident_explicit_decision_stays_local(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            orch = _make_orchestrator(mode="hybrid", telemetry_path=f"{tmp}/t.jsonl")
            structured = await orch.evaluate(_decision())
            self.assertEqual(structured.route, "local")

    async def test_high_risk_ungrounded_classifier_decision_escalates_to_cloud(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            cloud = _FakeProvider("copilot", reply="hermes")
            orch = PersonaOrchestrator(
                bridge=_FakeBridge(),
                local_provider=_FakeProvider("local"),
                cloud_provider=cloud,
                telemetry=TelemetryRecorder(f"{tmp}/t.jsonl"),
                mode="hybrid",
            )
            decision = _decision(
                confidence=0.2,
                grounded=False,
                risk="destructive",
                source="classifier",
                agent="code-puppy",
            )
            structured = await orch.evaluate(
                decision, OrchestrationContext(previous_routing_failure=True)
            )
            self.assertEqual(structured.route, "cloud")

    async def test_multimodal_need_is_a_hard_trigger(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            cloud = _FakeProvider("copilot", reply="hermes")
            orch = PersonaOrchestrator(
                bridge=_FakeBridge(),
                local_provider=_FakeProvider("local"),
                cloud_provider=cloud,
                telemetry=TelemetryRecorder(f"{tmp}/t.jsonl"),
                mode="hybrid",
            )
            # Confident + grounded (moderate score alone would land in the
            # "local_unless_cloud_required" band), but describes a real
            # multimodal need -- a hard trigger regardless of the score.
            decision = _decision(
                text="can you look at this screenshot for me",
                task="look at this screenshot for me",
                confidence=0.6,
                source="classifier",
            )
            structured = await orch.evaluate(decision)
            self.assertEqual(structured.route, "cloud")


class QuotaGatingTests(unittest.IsolatedAsyncioTestCase):
    async def test_economy_strategy_withholds_cloud_when_quota_unknown_without_hard_trigger(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            cloud = _FakeProvider("copilot", quota=None)
            # Custom weights that let harness/constraint risk alone reach the
            # "cloud" tier while confidence stays >= the harness-confidence
            # floor and nothing else is a hard trigger -- isolates quota
            # gating from the harness-confidence hard trigger, which a
            # low-confidence decision would otherwise also trip.
            weights = dict.fromkeys(DEFAULT_RISK_WEIGHTS, 0.0)
            weights["harness_selection_uncertainty"] = 0.5
            weights["constraint_complexity"] = 0.5
            orch = PersonaOrchestrator(
                bridge=_FakeBridge(),
                local_provider=_FakeProvider("local"),
                cloud_provider=cloud,
                telemetry=TelemetryRecorder(f"{tmp}/t.jsonl"),
                mode="hybrid",
                quota_strategy="economy",
                risk_weights=weights,
            )
            decision = _decision(
                confidence=0.9,  # well above the harness-confidence hard-trigger floor
                grounded=True,
                risk="destructive",
                source="classifier",  # harness_selection_uncertainty -> 0.6
                # Four distinct conditions to respect -> constraint_complexity 1.0.
                text=(
                    "before you deploy make sure the migration ran, "
                    "but don't touch the auth code unless it is stale"
                ),
                task="deploy without touching auth",
            )
            structured = await orch.evaluate(decision)
            self.assertGreaterEqual(
                structured.risk_score, 0.65
            )  # confirms the "cloud" tier was reached
            self.assertEqual(structured.route, "local")
            self.assertIn("quota strategy withheld cloud", structured.reason_summary)


class CloudFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_cloud_mode_falls_back_to_local_when_copilot_unauthenticated(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            cloud = _FakeProvider("copilot", available=False, authenticated=False)
            orch = PersonaOrchestrator(
                bridge=_FakeBridge(),
                local_provider=_FakeProvider("local"),
                cloud_provider=cloud,
                telemetry=TelemetryRecorder(f"{tmp}/t.jsonl"),
                mode="cloud",
            )
            structured = await orch.evaluate(_decision())
            self.assertEqual(structured.route, "local")
            self.assertIn("cloud unavailable", structured.reason_summary)


class AdmitConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_task_is_denied_admission(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            bridge = _FakeBridge(
                jobs=[_FakeJob(agent="hermes", task="check the weather", status="running")]
            )
            orch = PersonaOrchestrator(
                bridge=bridge,
                local_provider=_FakeProvider("local"),
                cloud_provider=_FakeProvider("copilot"),
                telemetry=TelemetryRecorder(f"{tmp}/t.jsonl"),
                mode="local",
                concurrency=ConcurrencyGuard(bridge=bridge, global_cap=5, harness_cap=5),
            )
            structured = await orch.evaluate(_decision())
            allowed, reason = orch.admit(structured)
            self.assertFalse(allowed)
            self.assertIn("already running", reason)


class TelemetryIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_wrong_harness_correction_is_flagged_in_route_telemetry(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/t.jsonl"
            cloud = _FakeProvider("copilot", reply="code-puppy")
            orch = PersonaOrchestrator(
                bridge=_FakeBridge(),
                local_provider=_FakeProvider("local"),
                cloud_provider=cloud,
                telemetry=TelemetryRecorder(path),
                mode="cloud",
            )
            # Non-explicit source -- eligible for cloud refinement.
            decision = _decision(source="heuristic", agent="hermes")
            structured = await orch.evaluate(decision)
            self.assertEqual(structured.target_harness, "code-puppy")
            rows = TelemetryRecorder(path)._rows()
            self.assertTrue(any(r.get("wrong_harness_corrected") for r in rows))

    async def test_record_outcome_attributes_route_from_the_matching_decision(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/t.jsonl"
            bridge = _FakeBridge()
            orch = PersonaOrchestrator(
                bridge=bridge,
                local_provider=_FakeProvider("local"),
                cloud_provider=_FakeProvider("copilot"),
                telemetry=TelemetryRecorder(path),
                mode="local",
            )
            structured = await orch.evaluate(_decision(agent="hermes", task="check the weather"))
            allowed, _ = orch.admit(structured)
            self.assertTrue(allowed)
            job = _FakeJob(agent="hermes", task="check the weather", status="done")
            orch.record_outcome(job)
            summary = TelemetryRecorder(path).summary()
            self.assertEqual(summary["local_resolution_pct"], 100.0)
            self.assertEqual(summary["local_success_pct"], 100.0)

    async def test_record_outcome_for_an_untracked_job_records_nothing(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/t.jsonl"
            orch = _make_orchestrator(mode="local", telemetry_path=path)
            # Never went through evaluate()/admit() -- e.g. a manual GUI
            # dispatch, an LLM marker delegation, or a confirmation relaunch.
            # This must be a true no-op: mixing untracked jobs into route
            # telemetry would silently understate both local and cloud
            # percentages, since neither route matches an untracked row.
            orch.record_outcome(_FakeJob(agent="hermes", task="untracked", status="done"))
            summary = TelemetryRecorder(path).summary()
            self.assertIsNone(summary["local_resolution_pct"])


if __name__ == "__main__":
    unittest.main()
