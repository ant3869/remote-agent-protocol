import unittest

from remote_agent_protocol import config, memory

# Every one-shot bracketed instruction the app can inject into the context.
# If a new template is added to config.py it must be covered by
# EPHEMERAL_PROMPT_PREFIXES or it piles up in jess_memory.json forever.
_INJECTED_PROMPTS = (
    config.DELEGATION_ACK_PROMPT.format(agent="hermes", task="write a file"),
    config.AGENT_UPDATE_PROMPT.format(update="the task finished"),
    # Retired template; the prefix stays covered so old memory files still strip it.
    "[A background agent needs the user's input. Ask this out loud: Which folder?]",
    config.DELEGATION_CONFIRM_PROMPT.format(agent="hermes-yolo", task="delete stuff"),
    config.AGENT_CONFIRM_APPROVED_PROMPT.format(agent="hermes-yolo", task="delete stuff"),
    config.AGENT_CONFIRM_DENIED_PROMPT.format(agent="hermes-yolo", task="delete stuff"),
)


class EphemeralPrefixCoverageTests(unittest.TestCase):
    def test_every_injected_template_is_covered_by_a_prefix(self):
        for prompt in _INJECTED_PROMPTS:
            with self.subTest(prompt=prompt[:40]):
                self.assertTrue(
                    prompt.startswith(config.EPHEMERAL_PROMPT_PREFIXES),
                    f"no EPHEMERAL_PROMPT_PREFIXES entry covers: {prompt[:60]}",
                )

    def test_strip_removes_all_injected_prompts_but_keeps_conversation(self):
        messages = [{"role": "user", "content": "hello"}]
        messages += [{"role": "user", "content": p} for p in _INJECTED_PROMPTS]
        messages += [{"role": "assistant", "content": "done, babe"}]

        cleaned = memory.strip_ephemeral(messages, drop_prefixes=config.EPHEMERAL_PROMPT_PREFIXES)

        self.assertEqual([m["content"] for m in cleaned], ["hello", "done, babe"])


class StripEphemeralTests(unittest.TestCase):
    def test_drops_agent_update_prompts_by_prefix(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "[Background task update -- relay this: done]"},
            {"role": "assistant", "content": "The task finished, babe."},
        ]

        cleaned = memory.strip_ephemeral(messages, drop_prefixes=("[Background task update",))

        self.assertEqual(
            [m["content"] for m in cleaned],
            ["hello", "The task finished, babe."],
        )

    def test_existing_exact_and_system_prefix_behaviour_unchanged(self):
        messages = [
            {"role": "system", "content": "Here's what I remember about you: stuff"},
            {"role": "user", "content": "kickoff line"},
            {"role": "user", "content": "real message"},
        ]

        cleaned = memory.strip_ephemeral(
            messages,
            system_prefixes=("Here's what I remember",),
            drop_contents=("kickoff line",),
        )

        self.assertEqual([m["content"] for m in cleaned], ["real message"])


if __name__ == "__main__":
    unittest.main()
