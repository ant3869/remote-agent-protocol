import unittest

from remote_agent_protocol import agent_bridge


class AgentFollowUpTests(unittest.TestCase):
    def test_extracts_explicit_follow_up_question(self):
        job = agent_bridge.AgentJob(
            job_id="job-1",
            agent="code-puppy",
            task="fix the repo",
            status=agent_bridge.STATUS_DONE,
            lines=[
                "I inspected the project.",
                "FOLLOW_UP_QUESTION: Which test command should I treat as canonical?",
            ],
        )

        self.assertEqual(
            agent_bridge.follow_up_question(job),
            "Which test command should I treat as canonical?",
        )

    def test_extracts_plain_question_from_tail(self):
        job = agent_bridge.AgentJob(
            job_id="job-2",
            agent="hermes-yolo",
            task="book a thing",
            status=agent_bridge.STATUS_DONE,
            lines=["I need one detail before continuing: what date do you want?"],
        )

        self.assertEqual(
            agent_bridge.follow_up_question(job),
            "I need one detail before continuing: what date do you want?",
        )

    def test_extracts_multiple_follow_up_questions_in_order(self):
        job = agent_bridge.AgentJob(
            job_id="job-multi",
            agent="code-puppy",
            task="finish setup",
            status=agent_bridge.STATUS_DONE,
            lines=[
                "QUESTION: Which folder should I use?",
                "FOLLOW_UP_QUESTION: Should I create a virtualenv?",
                "Clarifying question: Do you want me to commit afterward?",
            ],
        )

        self.assertEqual(
            agent_bridge.follow_up_questions(job),
            [
                "Which folder should I use?",
                "Should I create a virtualenv?",
                "Do you want me to commit afterward?",
            ],
        )

    def test_splits_multiple_questions_from_one_final_line(self):
        job = agent_bridge.AgentJob(
            job_id="job-one-line",
            agent="hermes-yolo",
            task="book travel",
            status=agent_bridge.STATUS_DONE,
            lines=[
                "I need a couple details: what date should I use? What budget range should I target?"
            ],
        )

        self.assertEqual(
            agent_bridge.follow_up_questions(job),
            [
                "I need a couple details: what date should I use?",
                "What budget range should I target?",
            ],
        )

    def test_ignores_failed_jobs_and_non_questions(self):
        failed = agent_bridge.AgentJob(
            job_id="job-3",
            agent="mock",
            task="explode",
            status=agent_bridge.STATUS_FAILED,
            lines=["Why did this fail? because kaboom"],
        )
        done = agent_bridge.AgentJob(
            job_id="job-4",
            agent="mock",
            task="summarize",
            status=agent_bridge.STATUS_DONE,
            lines=["All done. No input needed."],
        )

        self.assertIsNone(agent_bridge.follow_up_question(failed))
        self.assertIsNone(agent_bridge.follow_up_question(done))


if __name__ == "__main__":
    unittest.main()
