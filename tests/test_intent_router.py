import asyncio
import unittest
from dataclasses import asdict
from unittest.mock import AsyncMock, patch

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

    async def test_coding_keyword_task_selects_code_puppy(self):
        classify = FakeClassify(result=verdict(intent="chat", category="none", task=""))

        decision = await make_router(classify, auto_delegate=True).route(
            "write unit tests for this repository", "hermes"
        )

        self.assertEqual(decision.source, "heuristic")
        self.assertEqual(decision.agent, "code-puppy")

    async def test_word_code_in_non_coding_task_keeps_default_agent(self):
        # "validation code" / "promo code" must not steal the job from the
        # default agent just because the word "code" appears.
        classify = FakeClassify(result=verdict(intent="chat", category="none", task=""))

        decision = await make_router(classify, auto_delegate=True).route(
            "find my validation code in an email from usps", "hermes-yolo"
        )

        self.assertEqual(decision.agent, "hermes-yolo")

    async def test_classifier_coding_task_selects_code_puppy(self):
        classify = FakeClassify(
            result=verdict(
                category="files_or_apps",
                task="Refactor the Python module",
                conf=0.9,
            )
        )

        decision = await make_router(classify).route("this module could use a cleanup", "hermes")

        self.assertEqual(decision.source, "classifier")
        self.assertEqual(decision.agent, "code-puppy")

    async def test_capability_audit_short_circuits_the_classifier(self):
        classify = FakeClassify(exc=TimeoutError())
        decision = await self.route(
            classify,
            "check whether there are any useful skills we don't have installed",
        )

        self.assertEqual(decision.source, "heuristic")
        self.assertEqual(decision.action, "dispatch")
        self.assertEqual(classify.calls, [])

    async def test_capability_audit_question_short_circuits_the_classifier(self):
        classify = FakeClassify(exc=TimeoutError())
        decision = await self.route(
            classify,
            "are there any useful skills we don't have installed?",
        )

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

    async def test_default_warmup_has_a_cold_start_budget(self):
        classify = AsyncMock(return_value=verdict(intent="chat", category="none", task=""))
        with patch.object(intent_router, "classify_with_ollama", classify):
            await intent_router.IntentRouter(timeout_secs=0.05).warmup()

        self.assertEqual(classify.await_args.kwargs["timeout_secs"], 30.0)

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


class ExampleEchoRegressionTests(unittest.IsolatedAsyncioTestCase):
    """jess_runtime.log 2026-07-05 12:57 and 2026-07-06 01:02: the classifier
    recited a few-shot example's completion verbatim for an utterance that had
    nothing to do with it -- "delete the YouTube parse code" got dispatched as
    a weather lookup, and a garbled turn got dispatched as "Organize and clean
    up the user's downloads folder". Neither utterance shared a single word
    with the example that was echoed.
    """

    async def route(self, classify, text, **kwargs):
        return await make_router(classify, **kwargs).route(text, "code-puppy")

    async def test_verbatim_weather_example_rejected_for_unrelated_utterance(self):
        classify = FakeClassify(
            result=verdict(
                category="live_information",
                task="Get tomorrow's rain forecast for the user's location",
                conf=0.9,
                reason="Needs live weather data",
            )
        )
        decision = await self.route(
            classify,
            "have the bad computer delete the parse youtube request code left in the working tree",
        )

        self.assertEqual(decision.fallback, "invalid")
        self.assertEqual(decision.action, "none")

    async def test_verbatim_downloads_example_rejected_for_unrelated_utterance(self):
        classify = FakeClassify(
            result=verdict(
                category="files_or_apps",
                task="Organize and clean up the user's downloads folder",
                conf=0.8,
                reason="Describes a file cleanup goal indirectly",
            )
        )
        decision = await self.route(classify, "why did you do that and why")

        self.assertEqual(decision.fallback, "invalid")
        self.assertEqual(decision.action, "none")

    async def test_leaked_reason_alone_is_rejected_even_with_a_different_task(self):
        # 2026-07-06 01:02: task text differed from the example, but "reason"
        # still leaked the umbrella example's exact string verbatim.
        classify = FakeClassify(
            result=verdict(
                category="web_research",
                task="Look up the latest news on climate change in the last hour",
                conf=0.9,
                reason="Needs live weather data",
            )
        )
        decision = await self.route(classify, "why did you do that and why")

        self.assertEqual(decision.fallback, "invalid")
        self.assertEqual(decision.action, "none")

    async def test_genuine_weather_request_matching_the_example_still_dispatches(self):
        # The exact same verdict is legitimate when the utterance actually is
        # about weather/tomorrow -- only the unrelated case should be rejected.
        classify = FakeClassify(
            result=verdict(
                category="live_information",
                task="Get tomorrow's rain forecast for the user's location",
                conf=0.9,
                reason="Needs live weather data",
            )
        )
        decision = await self.route(classify, "will I need an umbrella tomorrow?")

        self.assertEqual(decision.source, "classifier")
        self.assertEqual(decision.action, "dispatch")
        self.assertEqual(decision.fallback, "")


class SttNoiseTierTests(unittest.IsolatedAsyncioTestCase):
    """Item 1: STT/VAD hallucinations must never reach the classifier or dispatch."""

    async def route(self, classify, text, **kwargs):
        return await make_router(classify, **kwargs).route(text, "code-puppy")

    async def test_hallucinated_outro_never_pays_for_the_classifier(self):
        classify = FakeClassify(
            result=verdict(category="system_control", task="Enable video watching", conf=0.9)
        )
        decision = await self.route(classify, "thank you for watching")

        self.assertEqual(decision.source, "noise")
        self.assertEqual(decision.action, "none")
        self.assertEqual(decision.fallback, "noise")
        self.assertEqual(classify.calls, [])

    async def test_content_free_utterance_is_noise(self):
        classify = FakeClassify(result=verdict())
        decision = await self.route(classify, "...")

        self.assertEqual(decision.source, "noise")
        self.assertEqual(decision.action, "none")
        self.assertEqual(classify.calls, [])


class GroundingGapTests(unittest.IsolatedAsyncioTestCase):
    """Item 2: a general grounding guard, not just exact example echoes -- a
    classifier task/reason that shares no real word with the transcript must
    never auto-dispatch, even at high confidence and even without reciting a
    memorized example verbatim."""

    async def route(self, classify, text, **kwargs):
        return await make_router(classify, **kwargs).route(text, "code-puppy")

    async def test_high_confidence_unrelated_task_is_held_not_dispatched(self):
        classify = FakeClassify(
            result=verdict(
                category="files_or_apps",
                task="Reorganize the photo archive by date",
                conf=0.95,
                reason="User wants their photo library sorted",
            )
        )
        decision = await self.route(classify, "why did the music stop playing")

        self.assertEqual(decision.action, "confirm")
        self.assertFalse(decision.grounded)
        self.assertEqual(decision.risk, intent_router.RISK_LOW_GROUNDING)

    async def test_high_confidence_readonly_unrelated_task_still_holds(self):
        # Read-only categories normally auto-dispatch in the uncertain band and
        # even more so when confident -- grounding must override that.
        classify = FakeClassify(
            result=verdict(
                category="live_information",
                task="Get the football score for the user's team",
                conf=0.95,
                reason="User wants a sports update",
            )
        )
        decision = await self.route(classify, "what a weird noise that was")

        self.assertEqual(decision.action, "confirm")
        self.assertFalse(decision.grounded)

    async def test_grounded_task_with_sparse_verdict_text_still_dispatches(self):
        # Guards against false positives: a terse/placeholder-ish task+reason
        # (nothing to compare) must not be treated as ungrounded.
        classify = FakeClassify(result=verdict(conf=0.6))
        decision = await self.route(classify, "hmm what's happening out there")

        self.assertEqual(decision.action, "dispatch")
        self.assertTrue(decision.grounded)


class RiskClassificationTests(unittest.IsolatedAsyncioTestCase):
    """Item 7: every decision carries a risk classification independent of
    plain dispatch/confirm, so logs/GUI can explain WHY confirmation is (or
    isn't) required."""

    async def route(self, classify, text, **kwargs):
        return await make_router(classify, **kwargs).route(text, "hermes")

    async def test_destructive_classifier_task_is_flagged_destructive(self):
        classify = FakeClassify(
            result=verdict(
                category="files_or_apps",
                task="Delete the files in the downloads folder",
                conf=0.95,
                reason="User asked to delete their downloads",
            )
        )
        decision = await self.route(classify, "delete the files in my downloads folder")

        self.assertEqual(decision.risk, intent_router.RISK_DESTRUCTIVE)

    async def test_confident_grounded_safe_task_is_safe(self):
        classify = FakeClassify(
            result=verdict(
                category="live_information",
                task="Get tomorrow's weather forecast",
                conf=0.95,
                reason="User asked about tomorrow's weather",
            )
        )
        decision = await self.route(classify, "what's the weather like tomorrow")

        self.assertEqual(decision.risk, intent_router.RISK_SAFE)
        self.assertEqual(decision.action, "dispatch")

    async def test_ambiguous_but_grounded_mutating_task_is_ambiguous(self):
        classify = FakeClassify(
            result=verdict(
                category="files_or_apps",
                task="Organize the downloads folder",
                conf=0.6,
                reason="User wants downloads organized",
            )
        )
        decision = await self.route(classify, "can you do something about my downloads folder")

        self.assertEqual(decision.risk, intent_router.RISK_AMBIGUOUS)
        self.assertEqual(decision.action, "confirm")

    async def test_explicit_destructive_delegation_is_flagged_destructive(self):
        classify = FakeClassify(result=verdict())
        decision = await self.route(classify, "tell hermes to delete the old logs")

        self.assertEqual(decision.source, "explicit")
        self.assertEqual(decision.risk, intent_router.RISK_DESTRUCTIVE)


class ValidBehaviorRegressionTests(unittest.IsolatedAsyncioTestCase):
    """Item 3: hardening must not break legitimate requests."""

    async def route(self, classify, text, **kwargs):
        return await make_router(classify, **kwargs).route(text, "hermes")

    async def test_weather_request_dispatches(self):
        classify = FakeClassify(result=verdict())
        decision = await self.route(classify, "check the weather for me")

        self.assertEqual(decision.action, "dispatch")

    async def test_climate_news_lookup_dispatches_via_classifier(self):
        classify = FakeClassify(
            result=verdict(
                category="web_research",
                task="Look up the latest news on climate change",
                conf=0.9,
                reason="User asked for climate change news",
            )
        )
        decision = await self.route(classify, "what's new with climate change lately")

        self.assertEqual(decision.action, "dispatch")
        self.assertTrue(decision.grounded)

    async def test_organize_downloads_dispatches(self):
        classify = FakeClassify(result=verdict())
        decision = await self.route(classify, "organize my downloads folder")

        self.assertEqual(decision.action, "dispatch")
        self.assertEqual(decision.risk, intent_router.RISK_SAFE)

    async def test_delete_downloads_is_flagged_destructive_for_the_session_gate(self):
        # The heuristic tier itself always reports "dispatch" (it's the free,
        # high-precision net); the actual hold-for-confirmation on destructive
        # tasks happens downstream in session._delegate_ack_ex, which checks
        # voice_commands.requires_confirmation on every dispatch regardless of
        # tier. The router's job here is only to make the risk visible.
        classify = FakeClassify(result=verdict())
        decision = await self.route(classify, "delete the files in my downloads folder")

        self.assertEqual(decision.action, "dispatch")
        self.assertEqual(decision.source, "heuristic")
        self.assertEqual(decision.risk, intent_router.RISK_DESTRUCTIVE)


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
