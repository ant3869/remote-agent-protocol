"""orchestration/telemetry.py -- never fabricates a metric it has no rows for."""

import tempfile
import unittest
from pathlib import Path

from remote_agent_protocol.orchestration.telemetry import TelemetryEvent, TelemetryRecorder


class TelemetryRecorderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "telemetry.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def test_summary_is_all_none_with_no_rows(self):
        recorder = TelemetryRecorder(self.path)
        summary = recorder.summary()
        self.assertTrue(all(value is None for value in summary.values()))

    def test_route_stage_events_without_latency_leave_outcome_stats_none(self):
        recorder = TelemetryRecorder(self.path)
        recorder.record(TelemetryEvent(stage="route", route="local"))
        summary = recorder.summary()
        self.assertTrue(all(value is None for value in summary.values()))

    def test_route_latency_is_broken_down_by_route(self):
        recorder = TelemetryRecorder(self.path)
        recorder.record(TelemetryEvent(stage="route", route="local", route_latency_ms=5.0))
        recorder.record(TelemetryEvent(stage="route", route="local", route_latency_ms=15.0))
        recorder.record(TelemetryEvent(stage="route", route="cloud", route_latency_ms=400.0))
        summary = recorder.summary()
        self.assertEqual(summary["avg_local_route_latency_ms"], 10.0)
        self.assertEqual(summary["avg_cloud_route_latency_ms"], 400.0)
        # No completed job yet -- outcome-only stats stay unknown, not zero/fabricated.
        self.assertIsNone(summary["local_resolution_pct"])

    def test_route_event_persists_reasoning_fields_for_inspection(self):
        recorder = TelemetryRecorder(self.path)
        recorder.record(
            TelemetryEvent(
                stage="route",
                route="cloud",
                harness="code-puppy",
                original_harness="hermes",
                risk_score=0.7,
                risk_factors={"intent_ambiguity": 0.5, "context_dependency": 0.2},
                reason="classifier judged this a real-world task -- cloud reasoning selected 'code-puppy'",
                route_latency_ms=250.0,
                wrong_harness_corrected=True,
            )
        )
        row = recorder._rows()[0]
        self.assertEqual(row["original_harness"], "hermes")
        self.assertEqual(row["harness"], "code-puppy")
        self.assertEqual(row["risk_factors"]["intent_ambiguity"], 0.5)
        self.assertIn("cloud reasoning selected", row["reason"])
        self.assertEqual(row["route_latency_ms"], 250.0)
        self.assertTrue(row["wrong_harness_corrected"])

    def test_local_and_cloud_resolution_percentages(self):
        recorder = TelemetryRecorder(self.path)
        recorder.record(TelemetryEvent(stage="outcome", route="local", outcome="success"))
        recorder.record(TelemetryEvent(stage="outcome", route="local", outcome="success"))
        recorder.record(TelemetryEvent(stage="outcome", route="cloud", outcome="failed"))
        summary = recorder.summary()
        self.assertAlmostEqual(summary["local_resolution_pct"], 66.7)
        self.assertAlmostEqual(summary["cloud_escalation_pct"], 33.3)
        self.assertEqual(summary["local_success_pct"], 100.0)
        self.assertEqual(summary["cloud_success_pct"], 0.0)

    def test_duplicate_blocked_counts_as_unnecessary_delegation(self):
        recorder = TelemetryRecorder(self.path)
        recorder.record(
            TelemetryEvent(stage="outcome", outcome="cancelled", duplicate_blocked=True)
        )
        recorder.record(TelemetryEvent(stage="outcome", outcome="success"))
        summary = recorder.summary()
        self.assertEqual(summary["unnecessary_delegation_pct"], 50.0)

    def test_avg_latency_only_over_rows_that_reported_it(self):
        recorder = TelemetryRecorder(self.path)
        recorder.record(TelemetryEvent(stage="outcome", outcome="success", latency_ms=100.0))
        recorder.record(TelemetryEvent(stage="outcome", outcome="success", latency_ms=300.0))
        recorder.record(TelemetryEvent(stage="outcome", outcome="success"))  # no latency
        self.assertEqual(recorder.summary()["avg_latency_ms"], 200.0)

    def test_malformed_line_is_skipped_not_fatal(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("not json\n", encoding="utf-8")
        recorder = TelemetryRecorder(self.path)
        recorder.record(TelemetryEvent(stage="outcome", outcome="success"))
        summary = recorder.summary()
        self.assertEqual(summary["local_resolution_pct"], 0.0)  # one valid row, route=""


if __name__ == "__main__":
    unittest.main()
