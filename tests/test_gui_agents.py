import unittest

from remote_agent_protocol.gui import agent_stream_line
from remote_agent_protocol.gui_agents import AgentsPanel


class FakeSession:
    """Stand-in for VoiceSession that only tracks clear_agent_history calls."""

    def __init__(self, clear_succeeds: bool = True):
        self.clear_succeeds = clear_succeeds
        self.clear_calls = 0

    def clear_agent_history(self) -> bool:
        self.clear_calls += 1
        return self.clear_succeeds


class AgentsPanelStatusTests(unittest.TestCase):
    def setUp(self):
        self.messages = []
        self.panel = AgentsPanel.__new__(AgentsPanel)
        self.panel._jobs = {}
        self.panel._order = []
        self.panel._window = None
        self.panel._append_sys = self.messages.append
        self.panel._session = FakeSession()

    def test_progress_event_tracks_rich_status(self):
        self.panel.handle_event(
            {
                "job_id": "job-1",
                "agent": "code-puppy",
                "machine": "Main PC",
                "task": "draw a dog",
                "status": "running",
                "event": "progress",
                "state": "tool_running",
                "action": "Drawing the dog outline",
                "tool": "windows-mcp",
                "step": 2,
                "step_total": 4,
                "last_completed_step": "Opened Paint",
            }
        )

        job = self.panel._jobs["job-1"]
        self.assertEqual(job["tool"], "windows-mcp")
        self.assertEqual(job["last_completed_step"], "Opened Paint")
        self.assertIn("TOOL RUNNING", self.panel._line_for(job))
        self.assertIn("Drawing the dog outline", self.panel.active_summary())

    def test_finished_event_captures_result_and_terminal_status(self):
        self.panel.handle_event(
            {
                "job_id": "job-1",
                "agent": "hermes-yolo",
                "machine": "Main PC",
                "task": "check my last 10 emails",
                "status": "running",
                "event": "progress",
                "state": "in_progress",
                "action": "Still working",
            }
        )
        self.panel.handle_event(
            {
                "job_id": "job-1",
                "agent": "hermes-yolo",
                "task": "check my last 10 emails",
                "status": "done",
                "event": "finished",
                "state": "completed",
                "summary": "Fetched the last 10 emails",
                "result": "1. Security alert\n2. CI failure",
                "secs": 51.7,
            }
        )

        job = self.panel._jobs["job-1"]
        # The row must reflect the terminal state -- a long job that finished
        # should not read as still IN PROGRESS.
        self.assertEqual(job["status"], "done")
        self.assertIn("✓", self.panel._line_for(job))
        # The substantive answer is retained for the detail pane / re-speak.
        self.assertEqual(job["result"], "1. Security alert\n2. CI failure")
        self.assertEqual(job["summary"], "Fetched the last 10 emails")

    def test_line_for_tolerates_null_state_from_legacy_history(self):
        # Legacy history rows persist state/action as null. The row renderer must
        # not crash on them (regression: .replace() on None killed the GUI pump).
        job = {
            "agent": "hermes-yolo",
            "machine": "Main PC",
            "task": "put a file on my desktop",
            "status": "done",
            "state": None,
            "action": None,
        }
        line = self.panel._line_for(job)
        self.assertIn("DONE", line)
        self.assertIn("put a file on my desktop", line)
        # active_summary skips terminal jobs, but must also survive a null state.
        self.panel._jobs["j"] = dict(job, status="running", state=None)
        self.panel._order = ["j"]
        self.assertIn("RUNNING", self.panel.active_summary())

    def test_clear_finished_deletes_persisted_history_once(self):
        self.panel._jobs = {"job-1": {"status": "done"}}
        self.panel._order = ["job-1"]

        self.panel._clear_finished()

        self.assertEqual(self.panel._session.clear_calls, 1)
        self.assertEqual(self.panel._order, [])
        self.assertEqual(self.messages, [])

    def test_clear_finished_warns_on_disk_failure_without_restoring_rows(self):
        self.panel._session = FakeSession(clear_succeeds=False)
        self.panel._jobs = {"job-1": {"status": "done"}}
        self.panel._order = ["job-1"]

        self.panel._clear_finished()

        self.assertEqual(self.panel._session.clear_calls, 1)
        # The user asked to hide these rows; a disk failure must not bring them back.
        self.assertEqual(self.panel._order, [])
        self.assertEqual(len(self.messages), 1)
        self.assertIn("history", self.messages[0].lower())

    def test_panel_no_longer_writes_progress_to_transcript(self):
        # Progress narration is streamed into the conversation by the GUI now,
        # not the panel -- the panel must not also emit it (would double up).
        self.panel.handle_event(
            {
                "job_id": "j1",
                "agent": "code-puppy",
                "event": "progress",
                "importance": "milestone",
                "state": "step_completed",
                "action": "Opened Paint",
            }
        )
        self.assertEqual(self.messages, [])


class AgentStreamLineTests(unittest.TestCase):
    def test_progress_event_becomes_a_feed_line(self):
        self.assertEqual(
            agent_stream_line(
                {"event": "progress", "agent": "code-puppy", "action": "checking the window"}
            ),
            "code-puppy: checking the window",
        )

    def test_progress_without_action_falls_back_to_state(self):
        self.assertEqual(
            agent_stream_line({"event": "progress", "agent": "hermes", "state": "tool_running"}),
            "hermes: tool_running",
        )

    def test_non_progress_events_are_skipped(self):
        for event in ("started", "finished", "output"):
            self.assertIsNone(agent_stream_line({"event": event, "agent": "x", "action": "y"}))

    def test_blank_action_is_skipped(self):
        self.assertIsNone(agent_stream_line({"event": "progress", "agent": "x", "action": "  "}))


if __name__ == "__main__":
    unittest.main()
