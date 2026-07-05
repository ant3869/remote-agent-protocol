import asyncio
import unittest
from dataclasses import asdict

from remote_agent_protocol import intent_router


def verdict(intent="agent_task", category="live_information", task="do it", conf=0.9, reason="r"):
    return {
        "intent": intent,
        "category": category,
        "task": task,
        "confidence": conf,
        "reason": reason,
    }


class FakeClassify:
    """Injectable classifier: canned result, error, or artificial delay."""

    def __init__(self, result=None, exc=None, delay=0.0):
        self.result = result
        self.exc = exc
        self.delay = delay
        self.calls: list[str] = []

    async def __call__(self, text: str) -> dict:
        self.calls.append(text)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        return self.result


def make_router(classify, **kwargs):
    kwargs.setdefault("enabled", True)
    kwargs.setdefault("timeout_secs", 1.0)
    kwargs.setdefault("dispatch_confidence", 0.75)
    kwargs.setdefault("confirm_confidence", 0.5)
    kwargs.setdefault("auto_delegate", True)
    return intent_router.IntentRouter(classify=classify, **kwargs)


class IntentRouterPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def route(self, classify, text, **kwargs):
        return await make_router(classify, **kwargs).route(text, "code-puppy")

    async def test_explicit_command_short_circuits_the_classifier(self):
        classify = FakeClassify(result=verdict())
        decision = await self.route(classify, "tell code puppy to open steam")

        self.assertEqual(decision.source, "explicit")
        self.assertEqual(decision.action, "dispatch")
        self.assertEqual(decision.agent, "code-puppy")
        self.assertEqual(decision.task, "open steam")
        self.assertEqual(classify.calls, [])  # tier 1 never pays for tier 2

    async def test_confident_lookup_dispatches_via_classifier(self):
        classify = FakeClassify(
            result=verdict(task="Get the storm forecast for Bentonville", conf=0.92)
        )
        decision = await self.route(classify, "any chance the sky falls on Bentonville soon?")

        self.assertEqual(decision.source, "classifier")
        self.assertEqual(decision.action, "dispatch")
        self.assertEqual(decision.requirement, "required")
        self.assertEqual(decision.task, "Get the storm forecast for Bentonville")

    async def test_uncertain_readonly_lookup_still_dispatches(self):
        classify = FakeClassify(result=verdict(conf=0.6))
        decision = await self.route(classify, "hmm what's happening out there")

        self.assertEqual(decision.action, "dispatch")
        self.assertEqual(decision.requirement, "optional")

    async def test_uncertain_mutating_task_asks_first(self):
        classify = FakeClassify(
            result=verdict(category="files_or_apps", task="Delete old logs", conf=0.6)
        )
        decision = await self.route(classify, "maybe tidy up those old log things?")

        self.assertEqual(decision.action, "confirm")
        self.assertEqual(decision.requirement, "optional")

    async def test_low_confidence_stays_chat(self):
        classify = FakeClassify(result=verdict(conf=0.3))
        decision = await self.route(classify, "the sky sure is doing things")

        self.assertEqual(decision.action, "none")
        self.assertEqual(decision.intent, "chat")

    async def test_chat_verdict_stays_chat_with_classifier_reason(self):
        classify = FakeClassify(
            result=verdict(intent="chat", category="none", task="", reason="small talk")
        )
        decision = await self.route(classify, "you're funny sometimes")

        self.assertEqual(decision.action, "none")
        self.assertEqual(decision.source, "classifier")
        self.assertEqual(decision.reason, "small talk")

    # -- regression: jess_runtime.log 2026-07-05 12:35 -- the vague YouTube-
    # -- skill request was rewritten into a generic "enable YouTube" task ----
    async def test_vague_capability_reference_never_reaches_the_classifier(self):
        classify = FakeClassify(
            result=verdict(
                category="system_control",
                task="Enable YouTube video watching on the computer",
                conf=0.9,
            )
        )
        text = (
            "there's a skill or a package that helps agents watch YouTube videos "
            "I don't remember what it's called but can you make sure the "
            "batcomputer has that"
        )
        decision = await self.route(classify, text)

        self.assertEqual(decision.source, "capability")
        self.assertEqual(decision.action, "confirm")  # wrong install mutates state
        self.assertEqual(decision.category, "system_control")
        self.assertIn(text, decision.task)  # the utterance survives verbatim
        self.assertIn("identify", decision.task.lower())
        self.assertNotIn("Enable YouTube video watching", decision.task)
        self.assertEqual(classify.calls, [])

    async def test_capability_tier_fires_even_with_auto_delegate_off(self):
        # It always asks for confirmation, so it is deliberately not gated
        # by the heuristic tier's auto_delegate switch.
        classify = FakeClassify(result=verdict())
        decision = await self.route(
            classify,
            "there's a plugin for that, I forgot the name, set it up",
            auto_delegate=False,
        )

        self.assertEqual(decision.source, "capability")
        self.assertEqual(decision.action, "confirm")
        self.assertEqual(classify.calls, [])

    async def test_named_capability_request_skips_the_capability_tier(self):
        classify = FakeClassify(result=verdict(intent="chat", category="none", task=""))
        decision = await self.route(classify, "install yt-dlp for me please")

        self.assertNotEqual(decision.source, "capability")

    async def test_keyword_net_short_circuits_the_classifier(self):
        classify = FakeClassify(result=verdict(intent="chat", category="none", task="", conf=0.9))
        decision = await self.route(classify, "check the news for anything about AI")

        self.assertEqual(decision.source, "heuristic")
        self.assertEqual(decision.action, "dispatch")
        self.assertEqual(classify.calls, [])

    async def test_keyword_match_never_pays_for_classifier_error(self):
        classify = FakeClassify(exc=RuntimeError("ollama down"))
        decision = await self.route(classify, "check the news for anything about AI")

        self.assertEqual(decision.source, "heuristic")
        self.assertEqual(decision.action, "dispatch")
        self.assertEqual(classify.calls, [])

    async def test_classifier_timeout_keeps_unmatched_utterance_in_chat(self):
        classify = FakeClassify(result=verdict(), delay=1.0)
        decision = await self.route(
            classify, "any chance the sky falls on Bentonville soon?", timeout_secs=0.05
        )

        self.assertEqual(decision.fallback, "timeout")
        self.assertEqual(decision.action, "none")

    async def test_classifier_error_with_no_keyword_match_stays_chat(self):
        classify = FakeClassify(exc=RuntimeError("ollama down"))
        decision = await self.route(classify, "you're funny sometimes")

        self.assertEqual(decision.action, "none")
        self.assertEqual(decision.source, "none")
        self.assertEqual(decision.fallback, "error")

    async def test_invalid_verdict_is_rejected(self):
        classify = FakeClassify(result={"intent": "banana"})
        decision = await self.route(classify, "any chance the sky falls on Bentonville soon?")

        self.assertEqual(decision.fallback, "invalid")
        self.assertEqual(decision.action, "none")

    async def test_placeholder_task_from_classifier_uses_the_utterance(self):
        classify = FakeClassify(result=verdict(task="clear task description", conf=0.9))
        decision = await self.route(classify, "how muggy is it outside right now")

        self.assertEqual(decision.task, "how muggy is it outside right now")

    async def test_disabled_router_uses_keywords_only(self):
        classify = FakeClassify(result=verdict())
        decision = await self.route(classify, "check the news for anything about AI", enabled=False)

        self.assertEqual(decision.source, "heuristic")
        self.assertEqual(classify.calls, [])

    async def test_warmup_preloads_enabled_classifier(self):
        classify = FakeClassify(result=verdict(intent="chat", category="none", task=""))

        await make_router(classify).warmup()

        self.assertEqual(classify.calls, ["hello"])

    async def test_decision_is_fully_serializable_for_events(self):
        classify = FakeClassify(result=verdict())
        decision = await self.route(classify, "what's it like outside")

        row = asdict(decision)
        for key in (
            "text",
            "action",
            "intent",
            "category",
            "requirement",
            "confidence",
            "task",
            "agent",
            "reason",
            "source",
            "fallback",
            "elapsed_ms",
        ):
            self.assertIn(key, row)


class VerdictNormalizationTests(unittest.TestCase):
    def test_confidence_is_clamped(self):
        norm = intent_router._normalize_verdict(verdict(conf=7))
        self.assertEqual(norm["confidence"], 1.0)

    def test_unknown_category_defaults_by_intent(self):
        norm = intent_router._normalize_verdict(verdict(category="quantum"))
        self.assertEqual(norm["category"], "other_action")
        norm = intent_router._normalize_verdict(
            verdict(intent="chat", category="quantum", conf=0.5)
        )
        self.assertEqual(norm["category"], "none")

    def test_garbage_is_rejected(self):
        self.assertIsNone(intent_router._normalize_verdict("nope"))
        self.assertIsNone(intent_router._normalize_verdict({"intent": "maybe"}))
        self.assertIsNone(intent_router._normalize_verdict(verdict(conf="high")))


if __name__ == "__main__":
    unittest.main()
