import unittest

from remote_agent_protocol.gui_agents import AgentsPanel


class AgentsPanelStatusTests(unittest.TestCase):
    def setUp(self):
        self.messages = []
        self.panel = AgentsPanel.__new__(AgentsPanel)
        self.panel._jobs = {}
        self.panel._order = []
        self.panel._window = None
        self.panel._append_sys = self.messages.append

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


if __name__ == "__main__":
    unittest.main()
