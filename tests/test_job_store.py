import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path

from remote_agent_protocol import job_store


@dataclass
class FakeJob:
    job_id: str
    agent: str = "mock"
    machine: str = "local"
    task: str = "do a thing"
    status: str = "done"
    secs: float | None = 1.2
    lines: list = field(default_factory=lambda: ["a", "b"])
    state: str = "completed"
    action: str = "Finished drawing"
    tool: str = "windows-mcp"
    step: int | None = 4
    step_total: int | None = 4
    last_completed_step: str = "Added color"
    summary: str = "Dog drawn"


class JobStoreTests(unittest.TestCase):
    def test_row_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "hist.json"
            job_store.append_job(path, job_store.job_to_row(FakeJob("job-1")))
            rows = job_store.load_history(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["job_id"], "job-1")
            self.assertEqual(rows[0]["status"], "done")

    def test_limit_trims_to_newest(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "hist.json"
            for i in range(5):
                job_store.append_job(path, job_store.job_to_row(FakeJob(f"job-{i}")), limit=3)
            rows = job_store.load_history(path, limit=3)
            self.assertEqual([r["job_id"] for r in rows], ["job-2", "job-3", "job-4"])

    def test_rich_status_survives_persistence(self):
        row = job_store.job_to_row(FakeJob("job-rich"))

        self.assertEqual(row["state"], "completed")
        self.assertEqual(row["tool"], "windows-mcp")
        self.assertEqual(row["last_completed_step"], "Added color")
        self.assertEqual(row["summary"], "Dog drawn")

    def test_missing_file_returns_empty(self):
        self.assertEqual(job_store.load_history("does-not-exist.json"), [])

    def test_corrupt_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "hist.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(job_store.load_history(path), [])

    def test_lines_are_bounded_on_disk(self):
        job = FakeJob("job-big")
        job.lines = [str(i) for i in range(200)]
        row = job_store.job_to_row(job)
        self.assertLessEqual(len(row["lines"]), 50)
        self.assertEqual(row["lines"][-1], "199")


if __name__ == "__main__":
    unittest.main()
