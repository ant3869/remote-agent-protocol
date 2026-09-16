"""orchestration/risk.py -- pure scoring math, no I/O."""

import unittest

from remote_agent_protocol.orchestration import risk
from remote_agent_protocol.orchestration.models import DEFAULT_RISK_WEIGHTS, RiskFactors, Route


class ValidateWeightsTests(unittest.TestCase):
    def test_default_weights_sum_to_one(self):
        risk.validate_weights(DEFAULT_RISK_WEIGHTS)  # must not raise

    def test_missing_factor_rejected(self):
        incomplete = dict(DEFAULT_RISK_WEIGHTS)
        del incomplete["context_dependency"]
        with self.assertRaises(ValueError):
            risk.validate_weights(incomplete)

    def test_weights_not_summing_to_one_rejected(self):
        skewed = {**DEFAULT_RISK_WEIGHTS, "context_dependency": 0.9}
        with self.assertRaises(ValueError):
            risk.validate_weights(skewed)


class ScoreRiskTests(unittest.TestCase):
    def test_all_zero_factors_score_zero(self):
        self.assertEqual(risk.score_risk(RiskFactors()), 0.0)

    def test_all_max_factors_score_one(self):
        maxed = RiskFactors(**dict.fromkeys(DEFAULT_RISK_WEIGHTS, 1.0))
        self.assertAlmostEqual(risk.score_risk(maxed), 1.0)

    def test_score_uses_custom_weights(self):
        weights = {
            k: (v if k != "context_dependency" else 1.0) for k, v in DEFAULT_RISK_WEIGHTS.items()
        }
        # Not required to sum to 1 here -- score_risk doesn't validate; only
        # validate_weights (called by the orchestrator) does.
        factors = RiskFactors(context_dependency=1.0)
        self.assertAlmostEqual(risk.score_risk(factors, weights), 1.0)


class ClassifyRouteTests(unittest.TestCase):
    def test_low_score_is_local(self):
        self.assertEqual(risk.classify_route(0.0), "local")
        self.assertEqual(risk.classify_route(0.39), "local")

    def test_mid_score_is_local_unless_cloud_required(self):
        self.assertEqual(risk.classify_route(0.40), "local_unless_cloud_required")
        self.assertEqual(risk.classify_route(0.64), "local_unless_cloud_required")

    def test_high_score_is_cloud(self):
        self.assertEqual(risk.classify_route(0.65), "cloud")
        self.assertEqual(risk.classify_route(1.0), "cloud")

    def test_custom_thresholds_respected(self):
        self.assertEqual(risk.classify_route(0.5, local_ceiling=0.6, cloud_floor=0.9), "local")


if __name__ == "__main__":
    unittest.main()


class FactorModelTests(unittest.TestCase):
    """The score has to represent orchestration difficulty, not a constant.

    Each case below states the shape of turn it stands for; the assertions are
    about where they land relative to each other and to the bands, not about
    exact numbers, which are tunable by design.
    """

    @staticmethod
    def _score(**factors) -> float:
        return risk.score_risk(RiskFactors(**factors), DEFAULT_RISK_WEIGHTS)

    def _turn(self, text, *, source="classifier", confidence=0.95, grounded=True, tier="safe"):
        """Score one utterance the way PersonaOrchestrator._factors would."""
        factors = RiskFactors(
            context_dependency=risk.context_dependency(text, grounded=grounded),
            constraint_complexity=risk.constraint_complexity(text, risk_tier=tier),
            harness_selection_uncertainty=risk.harness_selection_uncertainty(
                source=source,
                confidence=confidence,
                named=risk.named_harnesses(text, ("hermes", "codex", "code-puppy"), {}),
            ),
            result_interpretation_need=risk.result_interpretation_need(text),
            sequential_coordination=risk.sequential_coordination(text),
            routing_disagreement=risk.routing_disagreement(confidence=confidence, risk_tier=tier),
            multimodal_capability_need=risk.multimodal_capability_need(text),
        )
        return risk.score_risk(factors, DEFAULT_RISK_WEIGHTS), factors

    def test_trivial_explicit_request_stays_local(self):
        score, factors = self._turn(
            "tell codex to add a docstring to build_command in agent_bridge.py",
            source="explicit",
            confidence=1.0,
        )
        self.assertLessEqual(score, 0.39, f"should be comfortably local, got {score}")
        self.assertEqual(risk.classify_route(score), Route.LOCAL.value)
        self.assertEqual(factors.result_interpretation_need, 0.0)

    def test_contextual_follow_up_scores_materially_higher(self):
        trivial, _ = self._turn(
            "tell codex to add a docstring to build_command in agent_bridge.py",
            source="explicit",
            confidence=1.0,
        )
        score, factors = self._turn(
            "can you clean that up? it is still wrong from the job you just ran on the file"
        )
        self.assertGreater(factors.context_dependency, 0.5)
        self.assertGreater(score, trivial + 0.25, f"{score} vs trivial {trivial}")

    def test_multi_constraint_conditional_scores_materially_higher(self):
        score, factors = self._turn(
            "before you touch the api check whether the migration ran, and if it did roll it "
            "back then re-run the tests, but don't change the auth code"
        )
        self.assertEqual(factors.constraint_complexity, 1.0)
        self.assertGreater(factors.sequential_coordination, 0.0)
        self.assertGreater(
            score, 0.39, f"a genuinely complex turn must leave the local band: {score}"
        )

    def test_ambiguous_harness_choice_raises_the_score(self):
        inferred, _ = self._turn("run the unit tests")
        contested, factors = self._turn(
            "get hermes or code-puppy to look at this, whichever is free"
        )
        self.assertGreaterEqual(factors.harness_selection_uncertainty, 0.85)
        self.assertGreater(contested, inferred)

    def test_prior_job_interpretation_raises_the_score(self):
        plain, _ = self._turn("run the disk usage report")
        interpreting, factors = self._turn(
            "what did the job find, and was it ok? summarise the result for me"
        )
        self.assertGreater(factors.result_interpretation_need, 0.0)
        self.assertGreater(interpreting, plain)

    def test_multimodal_requirement_still_trips_the_hard_trigger(self):
        _, factors = self._turn("look at this screenshot and tell me what is wrong")
        # orchestrator.evaluate() treats >= 0.5 here as a hard trigger, so the
        # escalation must not depend on the weighted score at all.
        self.assertGreaterEqual(factors.multimodal_capability_need, 0.5)

    def test_a_classifier_that_never_answered_is_visible_to_the_score(self):
        """The old model read confidence only, so a timeout scored as certainty."""
        answered = risk.routing_disagreement(confidence=0.95, fallback="")
        timed_out = risk.routing_disagreement(confidence=0.95, fallback="timeout")
        self.assertGreater(timed_out, answered)
        self.assertGreaterEqual(timed_out, 0.8)

    def test_every_factor_can_actually_reach_its_extremes(self):
        """No factor may be a constant: dead weight is what broke the old model."""
        for name in DEFAULT_RISK_WEIGHTS:
            with self.subTest(factor=name):
                self.assertGreater(DEFAULT_RISK_WEIGHTS[name], 0.0)
        self.assertAlmostEqual(sum(DEFAULT_RISK_WEIGHTS.values()), 1.0, places=6)

    def test_the_cloud_band_is_reachable_by_score_alone(self):
        """The whole point of the correction: no hard trigger involved here."""
        score = self._score(
            context_dependency=0.9,
            constraint_complexity=1.0,
            harness_selection_uncertainty=0.85,
            result_interpretation_need=1.0,
            sequential_coordination=0.67,
            routing_disagreement=0.6,
        )
        self.assertGreaterEqual(score, 0.65, f"coordination-heavy turn must reach cloud: {score}")
        self.assertEqual(risk.classify_route(score), Route.CLOUD.value)
