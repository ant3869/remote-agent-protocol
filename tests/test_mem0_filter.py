import unittest

from remote_agent_protocol import config as cfg
from remote_agent_protocol import mem0_setup


class Mem0FilterTests(unittest.TestCase):
    def test_keeps_user_identity_and_preference_facts(self):
        messages = [
            {"role": "assistant", "content": "I'm Jess and I'm spicy."},
            {"role": "user", "content": "My name is Ant."},
            {"role": "user", "content": "I prefer fast, short replies."},
        ]

        filtered = mem0_setup.filter_messages_for_storage(messages)

        self.assertEqual(
            [m["content"] for m in filtered], ["My name is Ant.", "I prefer fast, short replies."]
        )

    def test_drops_assistant_quotes_and_kickoff_prompts(self):
        messages = [
            {"role": "assistant", "content": "Look who decided to grace me with his presence."},
            {"role": "user", "content": cfg.KICKOFF_FIRST},
            {"role": "user", "content": cfg.KICKOFF_RETURNING},
        ]

        self.assertEqual(mem0_setup.filter_messages_for_storage(messages), [])

    def test_drops_low_value_chatter(self):
        messages = [
            {"role": "user", "content": "lol"},
            {"role": "user", "content": "what?"},
            {"role": "user", "content": "tell me a joke"},
        ]

        self.assertEqual(mem0_setup.filter_messages_for_storage(messages), [])

    def test_keeps_common_durable_user_patterns(self):
        messages = [
            {"role": "user", "content": "I live in Dallas."},
            {"role": "user", "content": "Remember that my GPU is an RTX 5060 Ti."},
            {"role": "user", "content": "My dog is named Pixel."},
        ]

        filtered = mem0_setup.filter_messages_for_storage(messages)

        self.assertEqual(len(filtered), 3)

    def test_keeps_short_marker_facts(self):
        # Short but genuine facts must survive -- the durable marker is the
        # signal, not the length. (Regression: an 8-char floor dropped these.)
        messages = [
            {"role": "user", "content": "I'm 40."},
            {"role": "user", "content": "Call me Ant."},
        ]

        filtered = mem0_setup.filter_messages_for_storage(messages)

        self.assertEqual([m["content"] for m in filtered], ["I'm 40.", "Call me Ant."])


if __name__ == "__main__":
    unittest.main()
