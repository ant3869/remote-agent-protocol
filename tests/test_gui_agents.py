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


if __name__ == "__main__":
    unittest.main()
