import asyncio
import sys
import unittest

from remote_agent_protocol import agent_bridge

MOCK_BACKEND = {"mock": ["{python}", "-u", "scripts/mock_agent.py", "{task}"]}


class PureHelperTests(unittest.TestCase):
    def test_clean_line_strips_ansi_and_control_chars(self):
        raw = "\x1b[32mhello\x1b[0m world\r\x1b[K"
        self.assertEqual(agent_bridge.clean_line(raw), "hello world")

    def test_build_command_substitutes_task_and_python(self):
        cmd = agent_bridge.build_command(["{python}", "run", "{task}"], "do a thing")
        self.assertEqual(cmd, [sys.executable, "run", "do a thing"])

    def test_status_protocol_is_appended_to_agent_task(self):
        task = agent_bridge.with_status_protocol("draw a dog")

        self.assertTrue(task.startswith("draw a dog\n\n"))
        self.assertIn("@@JESS_STATUS", task)
        self.assertIn('"state":"completed"', task)

    def test_machine_label_is_in_job_events(self):
        events: list[dict] = []

        async def scenario():
            bridge = agent_bridge.AgentBridge({}, events.append, machines={"openclaw": "Laptop"})
            await bridge.start("openclaw", "status")

        self._run_async(scenario())
        self.assertEqual(events[-1]["machine"], "Laptop")

    @staticmethod
    def _run_async(coro):
        return asyncio.run(coro)

    def test_summarize_output_uses_tail_and_truncates(self):
        lines = ["step 1", "", "step 2", "x" * 400]
        summary = agent_bridge.summarize_output(lines, max_lines=2, max_chars=50)
        self.assertTrue(summary.endswith("..."))
        self.assertLessEqual(len(summary), 50)

    def test_announcement_mentions_agent_and_status(self):
        job = agent_bridge.AgentJob(job_id="j", agent="mock", task="review repo")
        job.status = agent_bridge.STATUS_DONE
        job.lines = ["RESULT: all good"]
        text = agent_bridge.announcement(job)
        self.assertIn("mock", text)
        self.assertIn("finished", text)
        self.assertIn("all good", text)

    def test_announcement_for_failure(self):
        job = agent_bridge.AgentJob(job_id="j", agent="mock", task="boom")
        job.status = agent_bridge.STATUS_FAILED
        job.lines = ["ERROR: simulated"]
        self.assertIn("FAILED", agent_bridge.announcement(job))

    def test_structured_status_line_is_normalized(self):
        status = agent_bridge.parse_status_line(
            '@@JESS_STATUS {"state":"step_completed","action":"Drawing fur",'
            '"tool":"windows-mcp","step":2,"step_total":4,'
            '"last_completed_step":"Opened Paint"}'
        )

        self.assertEqual(
            status,
            {
                "state": "step_completed",
                "action": "Drawing fur",
                "tool": "windows-mcp",
                "step": 2,
                "step_total": 4,
                "last_completed_step": "Opened Paint",
            },
        )

    def test_code_puppy_tool_line_is_normalized(self):
        status = agent_bridge.infer_status("🔧 Calling windows_mcp... 12 token(s)")

        self.assertEqual(
            status,
            {
                "state": "tool_running",
                "action": "Running windows_mcp",
                "tool": "windows_mcp",
            },
        )

    def test_status_inference_does_not_treat_negated_words_as_state(self):
        self.assertIsNone(agent_bridge.infer_status("The task is not blocked; continuing."))


class BridgeLifecycleTests(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_successful_job_emits_started_output_finished(self):
        events: list[dict] = []
        finished: list[agent_bridge.AgentJob] = []

        async def scenario():
            async def on_finished(job):
                finished.append(job)

            bridge = agent_bridge.AgentBridge(MOCK_BACKEND, events.append, on_finished)
            job_id = await bridge.start("mock", "say hello")
            for _ in range(200):  # up to ~10s, normally well under 1s
                if any(e["event"] == "finished" for e in events):
                    break
                await asyncio.sleep(0.05)
            return bridge.get(job_id)

        job = self._run(scenario())
        kinds = [e["event"] for e in events]
        self.assertIn("started", kinds)
        self.assertIn("output", kinds)
        self.assertIn("finished", kinds)
        self.assertEqual(job.status, agent_bridge.STATUS_DONE)
        self.assertEqual(len(finished), 1)
        finished_evt = next(e for e in events if e["event"] == "finished")
        self.assertGreaterEqual(finished_evt["secs"], 0.0)

    def test_failing_job_reports_failed(self):
        events: list[dict] = []

        async def scenario():
            bridge = agent_bridge.AgentBridge(MOCK_BACKEND, events.append)
            job_id = await bridge.start("mock", "fail please")
            for _ in range(200):
                if any(e["event"] == "finished" for e in events):
                    break
                await asyncio.sleep(0.05)
            return bridge.get(job_id)

        job = self._run(scenario())
        self.assertEqual(job.status, agent_bridge.STATUS_FAILED)

    def test_unknown_backend_fails_fast(self):
        events: list[dict] = []

        async def scenario():
            bridge = agent_bridge.AgentBridge(MOCK_BACKEND, events.append)
            job_id = await bridge.start("hermes-9000", "anything")
            return bridge.get(job_id)

        job = self._run(scenario())
        self.assertEqual(job.status, agent_bridge.STATUS_FAILED)
        self.assertEqual(events[-1]["event"], "finished")

    def test_unknown_backend_runs_finished_callback(self):
        finished: list[str] = []

        async def scenario():
            async def on_finished(job):
                finished.append(job.job_id)

            bridge = agent_bridge.AgentBridge({}, lambda _event: None, on_finished)
            return await bridge.start("missing", "anything")

        job_id = self._run(scenario())
        self.assertEqual(finished, [job_id])

    def test_cancel_kills_running_job(self):
        events: list[dict] = []

        async def scenario():
            bridge = agent_bridge.AgentBridge(MOCK_BACKEND, events.append)
            job_id = await bridge.start("mock", "sleep:5 long thing")
            await asyncio.sleep(0.3)  # let it start
            await bridge.cancel(job_id)
            for _ in range(100):
                if any(e["event"] == "finished" for e in events):
                    break
                await asyncio.sleep(0.05)
            return bridge.get(job_id)

        job = self._run(scenario())
        self.assertEqual(job.status, agent_bridge.STATUS_CANCELLED)

    def test_shutdown_reaps_live_jobs_before_loop_close(self):
        """Regression: a job still running at exit must be stopped and reaped.

        Without shutdown(), the subprocess transport is only closed by __del__
        after the loop is gone -- the Windows proactor 'I/O operation on closed
        pipe' crash reported when quitting the app mid-job.
        """
        events: list[dict] = []

        async def scenario():
            bridge = agent_bridge.AgentBridge(MOCK_BACKEND, events.append, kill_grace_secs=0.5)
            job_id = await bridge.start("mock", "sleep:30 never finishes")
            await asyncio.sleep(0.3)  # let the child actually start
            await bridge.shutdown()
            self.assertEqual(bridge._procs, {})
            self.assertEqual(bridge._tasks, set())
            return bridge.get(job_id)

        job = self._run(scenario())
        self.assertEqual(job.status, agent_bridge.STATUS_CANCELLED)
        self.assertIn("finished", [e["event"] for e in events])

    def test_shutdown_with_no_jobs_is_a_no_op(self):
        async def scenario():
            bridge = agent_bridge.AgentBridge(MOCK_BACKEND, lambda _e: None)
            await bridge.shutdown()

        self._run(scenario())

    def test_timeout_stops_a_long_job(self):
        events: list[dict] = []

        async def scenario():
            bridge = agent_bridge.AgentBridge(MOCK_BACKEND, events.append, timeout_secs=0.5)
            job_id = await bridge.start("mock", "sleep:5 long thing")
            for _ in range(100):
                if any(e["event"] == "finished" for e in events):
                    break
                await asyncio.sleep(0.05)
            return bridge.get(job_id)

        job = self._run(scenario())
        self.assertEqual(job.status, agent_bridge.STATUS_FAILED)
        self.assertTrue(any("timeout" in line.lower() for line in job.lines))

    def test_persist_hook_fires_on_completion(self):
        persisted: list[str] = []

        async def scenario():
            async def on_persist(job):
                persisted.append(job.job_id)

            bridge = agent_bridge.AgentBridge(MOCK_BACKEND, lambda _e: None, on_persist=on_persist)
            job_id = await bridge.start("mock", "hi")
            for _ in range(200):
                if persisted:
                    break
                await asyncio.sleep(0.05)
            return job_id

        job_id = self._run(scenario())
        self.assertIn(job_id, persisted)

    def test_completion_marker_finishes_a_stuck_process(self):
        events: list[dict] = []
        script = (
            "import time; "
            'print(\'@@JESS_STATUS {"state":"completed","summary":"Dog drawn"}\', '
            "flush=True); time.sleep(60)"
        )

        async def scenario():
            bridge = agent_bridge.AgentBridge(
                {"mock": ["{python}", "-u", "-c", script]},
                events.append,
                completion_grace_secs=0.05,
            )
            job_id = await bridge.start("mock", "draw a dog")
            for _ in range(100):
                if any(event["event"] == "finished" for event in events):
                    break
                await asyncio.sleep(0.02)
            return bridge.get(job_id)

        job = self._run(scenario())
        self.assertEqual(job.status, agent_bridge.STATUS_DONE)
        self.assertEqual(job.summary, "Dog drawn")
        self.assertEqual(job.action, "Dog drawn")
        self.assertEqual(sum(event["event"] == "finished" for event in events), 1)

    def test_silent_long_job_emits_progress_heartbeat(self):
        events: list[dict] = []
        script = "import time; time.sleep(0.2)"

        async def scenario():
            bridge = agent_bridge.AgentBridge(
                {"mock": ["{python}", "-u", "-c", script]},
                events.append,
                progress_interval_secs=0.05,
            )
            await bridge.start("mock", "wait quietly")
            for _ in range(50):
                if any(event["event"] == "progress" for event in events):
                    break
                await asyncio.sleep(0.01)
            for _ in range(50):
                if any(event["event"] == "finished" for event in events):
                    break
                await asyncio.sleep(0.01)

        self._run(scenario())
        progress = next(event for event in events if event["event"] == "progress")
        self.assertEqual(progress["state"], "in_progress")
        self.assertEqual(progress["action"], "Still working")

    def test_heartbeat_preserves_the_last_meaningful_action(self):
        events: list[dict] = []
        script = (
            "import time; "
            'print(\'@@JESS_STATUS {"state":"tool_running",'
            '"action":"Opening Paint","tool":"desktop"}\', flush=True); '
            "time.sleep(0.15)"
        )

        async def scenario():
            bridge = agent_bridge.AgentBridge(
                {"mock": ["{python}", "-u", "-c", script]},
                events.append,
                progress_interval_secs=0.05,
            )
            await bridge.start("mock", "paint")
            for _ in range(50):
                if any(event["event"] == "finished" for event in events):
                    break
                await asyncio.sleep(0.01)

        self._run(scenario())
        heartbeats = [
            event
            for event in events
            if event["event"] == "progress" and event["state"] == "in_progress"
        ]
        self.assertTrue(heartbeats)
        self.assertEqual(heartbeats[0]["action"], "Opening Paint")


if __name__ == "__main__":
    unittest.main()
