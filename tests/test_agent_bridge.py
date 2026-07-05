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

    def test_build_command_inserts_model_override_after_executable(self):
        cmd = agent_bridge.build_command(
            ["code-puppy", "-p", "{task}"],
            "do a thing",
            extra_args=["--model", "chatgpt-gpt-5.5"],
        )
        self.assertEqual(
            cmd,
            ["code-puppy", "--model", "chatgpt-gpt-5.5", "-p", "do a thing"],
        )

    def test_provider_failure_detection_distinguishes_quota_and_rate_limit(self):
        self.assertEqual(
            agent_bridge.detect_provider_failure("usage_limit_reached: plan exhausted"),
            "quota",
        )
        self.assertEqual(
            agent_bridge.detect_provider_failure("429 too many requests"),
            "rate_limit",
        )
        self.assertIsNone(agent_bridge.detect_provider_failure("finished normally"))

    def test_model_override_uses_exact_configured_target(self):
        bridge = agent_bridge.AgentBridge(
            {},
            lambda _event: None,
            model_targets={
                "code-puppy": {
                    "openai": {
                        "label": "OpenAI GPT-5.5",
                        "args": ["--model", "chatgpt-gpt-5.5"],
                    }
                }
            },
        )

        label = bridge.set_model_override("code-puppy", "openai")

        self.assertEqual(label, "OpenAI GPT-5.5")
        self.assertEqual(bridge._model_overrides["code-puppy"], ["--model", "chatgpt-gpt-5.5"])

    def test_scope_preamble_is_prepended_with_cwd(self):
        wrapped = agent_bridge.with_scope(
            "do a thing", "C:/sandbox", "[Scope: workspace is {cwd}, hands off.]"
        )
        self.assertTrue(wrapped.startswith("[Scope: workspace is C:/sandbox, hands off.]"))
        self.assertTrue(wrapped.endswith("do a thing"))

    def test_empty_scope_preamble_leaves_task_untouched(self):
        self.assertEqual(agent_bridge.with_scope("do a thing", "C:/sandbox", ""), "do a thing")

    def test_missing_cwd_is_named_unspecified_in_scope(self):
        wrapped = agent_bridge.with_scope("t", None, "cwd={cwd}")
        self.assertIn("cwd=unspecified", wrapped)

    def test_explicit_cwd_wins_over_workspace_default(self):
        self.assertEqual(agent_bridge.resolve_cwd("C:/somewhere", "C:/workspace"), "C:/somewhere")

    def test_no_workspace_configured_keeps_legacy_inherit(self):
        self.assertIsNone(agent_bridge.resolve_cwd(None, None))

    def test_default_cwd_is_the_workspace_and_it_is_created(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            workspace = str(Path(tmp) / "agent_workspace")
            resolved = agent_bridge.resolve_cwd(None, workspace)
            self.assertEqual(resolved, workspace)
            self.assertTrue(Path(workspace).is_dir())

    def test_announcement_warns_when_job_modified_host_source(self):
        job = agent_bridge.AgentJob("job-1", "code-puppy", "enable a thing")
        job.status = agent_bridge.STATUS_DONE
        job.summary = "did stuff"
        job.host_modified = True
        text = agent_bridge.announcement(job)
        self.assertIn("modified my own source", text)

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

    def test_echoed_protocol_examples_are_not_real_statuses(self):
        # Agent CLIs echo the prompt (including our protocol examples) back to
        # stdout; the completed example must not terminate the job instantly.
        self.assertIsNone(
            agent_bridge.parse_status_line(
                '@@JESS_STATUS {"state":"in_progress","action":"short current action"}'
            )
        )
        self.assertIsNone(
            agent_bridge.parse_status_line(
                '@@JESS_STATUS {"state":"completed","summary":"short result"}'
            )
        )

    def test_error_tail_finds_agent_crash_in_last_lines(self):
        lines = [
            "Executing prompt: put a file on my desktop",
            "⚡ Streaming interrupted, auto-retrying in 1s... (attempt 1/3)",
            "❌ Streaming failed after 3 attempts",
            "Unexpected error: status_code: 429, model_name: gpt-5.5, body: {'type':",
            "'usage_limit_reached', 'message': 'The usage limit has been reached'}",
        ]
        tail = agent_bridge.error_tail(lines)
        self.assertIsNotNone(tail)
        self.assertIn("usage_limit_reached", tail)

    def test_error_tail_ignores_recovered_mid_job_errors(self):
        # An early failure the agent worked around must not fail the job.
        lines = ["Unexpected error: transient"] + [f"step {i} ok" for i in range(10)] + ["Done"]
        self.assertIsNone(agent_bridge.error_tail(lines))


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
        self.assertRegex(finished_evt["started_at"], r"^\d{4}-\d{2}-\d{2}T")
        self.assertRegex(finished_evt["finished_at"], r"^\d{4}-\d{2}-\d{2}T")

    def test_host_repo_modification_is_flagged_on_finish(self):
        events: list[dict] = []

        async def scenario():
            bridge = agent_bridge.AgentBridge(MOCK_BACKEND, events.append, host_repo="unused")
            snapshots = iter(["", " M remote_agent_protocol/session.py\n"])

            async def fake_snapshot():
                return next(snapshots, None)

            bridge._host_snapshot = fake_snapshot
            job_id = await bridge.start("mock", "say hello")
            for _ in range(200):
                if any(e["event"] == "finished" for e in events):
                    break
                await asyncio.sleep(0.05)
            return bridge.get(job_id)

        job = self._run(scenario())
        self.assertTrue(job.host_modified)
        finished_evt = next(e for e in events if e["event"] == "finished")
        self.assertTrue(finished_evt["host_modified"])
        self.assertIn("modified my own source", agent_bridge.announcement(job))

    def test_untouched_host_repo_does_not_flag_the_job(self):
        events: list[dict] = []

        async def scenario():
            bridge = agent_bridge.AgentBridge(MOCK_BACKEND, events.append, host_repo="unused")

            async def fake_snapshot():
                return " M some_file.py\n"  # same before and after

            bridge._host_snapshot = fake_snapshot
            job_id = await bridge.start("mock", "say hello")
            for _ in range(200):
                if any(e["event"] == "finished" for e in events):
                    break
                await asyncio.sleep(0.05)
            return bridge.get(job_id)

        job = self._run(scenario())
        self.assertFalse(job.host_modified)
        self.assertNotIn("modified my own source", agent_bridge.announcement(job))

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

    def test_usage_limit_fails_immediately_and_explains_recovery(self):
        events: list[dict] = []
        script = (
            "import time; "
            "print('usage_limit_reached: The usage limit has been reached', flush=True); "
            "time.sleep(10)"
        )

        async def scenario():
            bridge = agent_bridge.AgentBridge(
                {"mock": ["{python}", "-u", "-c", script]},
                events.append,
                timeout_secs=8,
                kill_grace_secs=0.2,
            )
            started = asyncio.get_running_loop().time()
            job_id = await bridge.start("mock", "search")
            for _ in range(100):
                if any(e["event"] == "finished" for e in events):
                    break
                await asyncio.sleep(0.05)
            return bridge.get(job_id), asyncio.get_running_loop().time() - started

        job, elapsed = self._run(scenario())
        self.assertLess(elapsed, 2)
        self.assertEqual(job.failure_kind, "quota")
        self.assertEqual(job.status, agent_bridge.STATUS_FAILED)
        self.assertIn("usage", agent_bridge.announcement(job).lower())
        self.assertIn("switch", agent_bridge.announcement(job).lower())

    def test_zero_exit_with_error_tail_reports_failed(self):
        # Regression: code-puppy hit a 429 usage limit, printed the error, and
        # exited 0 -- the job was announced as finished with the raw error JSON
        # as its "result". It must be FAILED with the error line as summary.
        events: list[dict] = []
        script = (
            "print('Executing prompt: put a file on my desktop'); "
            "print('Unexpected error: status_code: 500, model_name: gpt-5.5, "
            "body: provider_failure')"
        )

        async def scenario():
            bridge = agent_bridge.AgentBridge(
                {"mock": ["{python}", "-u", "-c", script]}, events.append
            )
            job_id = await bridge.start("mock", "put a file on my desktop")
            for _ in range(200):
                if any(e["event"] == "finished" for e in events):
                    break
                await asyncio.sleep(0.05)
            return bridge.get(job_id)

        job = self._run(scenario())
        self.assertEqual(job.returncode, 0)
        self.assertEqual(job.status, agent_bridge.STATUS_FAILED)
        self.assertIn("500", job.summary)
        self.assertIn("FAILED", agent_bridge.announcement(job))

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
