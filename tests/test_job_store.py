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
    started_at: str = "2026-07-05T02:44:01.643-05:00"
    finished_at: str = "2026-07-05T02:47:01.843-05:00"
    failure_kind: str = ""
    failure_detail: str = ""
    model_label: str = ""


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
        self.assertEqual(row["started_at"], "2026-07-05T02:44:01.643-05:00")
        self.assertEqual(row["finished_at"], "2026-07-05T02:47:01.843-05:00")
        self.assertEqual(row["failure_kind"], "")

    def test_missing_file_returns_empty(self):
        self.assertEqual(job_store.load_history("does-not-exist.json"), [])

    def test_corrupt_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "hist.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(job_store.load_history(path), [])

    def test_concurrent_appends_do_not_lose_jobs(self):
        import concurrent.futures

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "hist.json"

            def append_many(index):
                for i in range(20):
                    job_store.append_job(
                        path, job_store.job_to_row(FakeJob(f"job-{index}-{i}")), limit=200
                    )

            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(append_many, i) for i in range(10)]
                concurrent.futures.wait(futures)
            rows = job_store.load_history(path, limit=200)
            self.assertEqual(len(rows), 200)
            ids = {r["job_id"] for r in rows}
            self.assertEqual(len(ids), 200)

    def test_lines_are_bounded_on_disk(self):
        job = FakeJob("job-big")
        job.lines = [str(i) for i in range(200)]
        row = job_store.job_to_row(job)
        self.assertLessEqual(len(row["lines"]), 50)
        self.assertEqual(row["lines"][-1], "199")

    def test_clear_history_deletes_existing_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "hist.json"
            job_store.append_job(path, job_store.job_to_row(FakeJob("job-1")))
            self.assertTrue(job_store.clear_history(path))
            self.assertFalse(path.exists())
            self.assertEqual(job_store.load_history(path), [])

    def test_clear_history_missing_file_is_a_successful_no_op(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "does-not-exist.json"
            self.assertTrue(job_store.clear_history(path))

    def test_clear_history_reports_failure_on_oserror(self):
        import unittest.mock

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "hist.json"
            job_store.append_job(path, job_store.job_to_row(FakeJob("job-1")))
            with unittest.mock.patch.object(
                Path, "unlink", side_effect=OSError("disk unavailable")
            ):
                self.assertFalse(job_store.clear_history(path))
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
