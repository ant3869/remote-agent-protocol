"""orchestration/concurrency.py -- global/per-harness caps, duplicate detection.

Reads AgentBridge.active_jobs() as the sole source of truth (via a fake
bridge here) -- there is no separate job registry to drift from it.
"""

import unittest
from dataclasses import dataclass, field

from remote_agent_protocol.orchestration.concurrency import ConcurrencyGuard, normalize_task


@dataclass
class _FakeJob:
    agent: str
    task: str
    job_id: str = "job-1"


@dataclass
class _FakeBridge:
    jobs: list = field(default_factory=list)

    def active_jobs(self):
        return self.jobs


class NormalizeTaskTests(unittest.TestCase):
    def test_folds_whitespace_and_case(self):
        self.assertEqual(normalize_task("  Check   the Weather  "), "check the weather")


class ConcurrencyGuardTests(unittest.TestCase):
    def test_admits_when_under_every_cap(self):
        guard = ConcurrencyGuard(bridge=_FakeBridge(), global_cap=2, harness_cap=1)
        allowed, reason = guard.admit("hermes", "check the weather")
        self.assertTrue(allowed)
        self.assertEqual(reason, "")

    def test_denies_over_global_cap(self):
        bridge = _FakeBridge(jobs=[_FakeJob("hermes", "a"), _FakeJob("code-puppy", "b")])
        guard = ConcurrencyGuard(bridge=bridge, global_cap=2, harness_cap=2)
        allowed, reason = guard.admit("codex", "c")
        self.assertFalse(allowed)
        self.assertIn("2 job(s) running", reason)

    def test_denies_over_per_harness_cap(self):
        bridge = _FakeBridge(jobs=[_FakeJob("hermes", "a")])
        guard = ConcurrencyGuard(bridge=bridge, global_cap=5, harness_cap=1)
        allowed, reason = guard.admit("hermes", "b")
        self.assertFalse(allowed)
        self.assertIn("'hermes'", reason)

    def test_denies_semantically_duplicate_task(self):
        bridge = _FakeBridge(jobs=[_FakeJob("hermes", "Check the weather", job_id="job-7")])
        guard = ConcurrencyGuard(bridge=bridge, global_cap=5, harness_cap=5)
        allowed, reason = guard.admit("hermes", "  check   THE weather ")
        self.assertFalse(allowed)
        self.assertIn("job-7", reason)

    def test_same_task_different_wording_is_not_flagged_as_duplicate(self):
        bridge = _FakeBridge(jobs=[_FakeJob("hermes", "Check the weather")])
        guard = ConcurrencyGuard(bridge=bridge, global_cap=5, harness_cap=5)
        allowed, _ = guard.admit("hermes", "Look up tomorrow's forecast")
        self.assertTrue(allowed)

    def test_rejects_nonpositive_caps(self):
        with self.assertRaises(ValueError):
            ConcurrencyGuard(bridge=_FakeBridge(), global_cap=0, harness_cap=1)


if __name__ == "__main__":
    unittest.main()
