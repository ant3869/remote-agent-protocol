import unittest
from unittest import mock

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    InputAudioRawFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.tests.utils import SleepFrame, run_test
from remote_agent_protocol import config, wake_word

SETTINGS = wake_word.WakeWordSettings(
    enabled=True, model="hey_jarvis", threshold=0.5, active_window_secs=5.0
)


def audio_frame(samples: int = wake_word.CHUNK_SAMPLES, rate: int = 16000):
    return InputAudioRawFrame(audio=b"\x00" * (samples * 2), sample_rate=rate, num_channels=1)


class FakeDetector:
    """Scripted detector: returns queued scores, then 0.0 forever."""

    def __init__(self, scores=()):
        self.scores = list(scores)
        self.resets = 0

    def predict(self, chunk):
        return {"hey_jarvis": self.scores.pop(0) if self.scores else 0.0}

    def reset(self):
        self.resets += 1


class MultiFakeDetector(FakeDetector):
    def predict(self, chunk):
        return self.scores.pop(0) if self.scores else {}


class WakeWordConfigTests(unittest.TestCase):
    def test_wake_word_disabled_by_default_config(self):
        self.assertFalse(config.WAKE_WORD_ENABLED)
        self.assertEqual(config.WAKE_WORD_ENGINE, "openwakeword")
        self.assertEqual(config.WAKE_WORD_ACTIVE_WINDOW_SECS, 3.0)

    def test_settings_from_config_mirrors_config_values(self):
        settings = wake_word.settings_from_config(config)

        self.assertEqual(settings.enabled, config.WAKE_WORD_ENABLED)
        self.assertEqual(settings.model, config.WAKE_WORD_MODEL)
        self.assertEqual(settings.threshold, config.WAKE_WORD_THRESHOLD)
        self.assertEqual(settings.active_window_secs, config.WAKE_WORD_ACTIVE_WINDOW_SECS)

    def test_status_reports_disabled_without_importing_engine(self):
        settings = wake_word.WakeWordSettings(enabled=False, engine="openwakeword")

        status = wake_word.preflight(settings)

        self.assertFalse(status.enabled)
        self.assertTrue(status.ready)
        self.assertEqual(status.message, "wake word disabled")

    def test_status_reports_missing_engine_when_enabled(self):
        settings = wake_word.WakeWordSettings(enabled=True, engine="openwakeword")
        with mock.patch(
            "remote_agent_protocol.wake_word.importlib.util.find_spec", return_value=None
        ):
            status = wake_word.preflight(settings)

        self.assertTrue(status.enabled)
        self.assertFalse(status.ready)
        self.assertIn("openwakeword is not installed", status.message)

    def test_status_ready_says_it_gates_the_mic(self):
        settings = wake_word.WakeWordSettings(enabled=True, engine="openwakeword")
        with mock.patch(
            "remote_agent_protocol.wake_word.importlib.util.find_spec", return_value=object()
        ):
            status = wake_word.preflight(settings)

        self.assertTrue(status.ready)
        self.assertIn("mic gated", status.message)
        self.assertNotIn("preview", status.message)

    def test_local_models_map_to_matching_personas_and_skip_missing_models(self):
        targets = wake_word.resolve_targets(
            {"hey_jarvis": "Jarvis", "hey_jess": "Jess"},
            available_models={"hey_jarvis"},
            persona_names={"Jess", "Jarvis"},
            threshold=0.5,
        )

        self.assertEqual(
            targets,
            (wake_word.WakeWordTarget("hey_jarvis", "Jarvis", 0.5),),
        )

    def test_discovery_matches_model_name_to_persona(self):
        targets = wake_word.resolve_targets(
            {},
            available_models={"hey_jarvis"},
            persona_names={"Jess", "Jarvis"},
            threshold=0.5,
        )

        self.assertEqual(targets[0].persona, "Jarvis")

    def test_discovers_repo_wake_models(self):
        models = wake_word.discover_local_models()

        self.assertIn("hey_luna", models)
        self.assertIn("alice", models)

    def test_config_falls_back_to_repo_model_when_default_missing(self):
        class Cfg:
            WAKE_WORD_ENABLED = True
            WAKE_WORD_ENGINE = "openwakeword"
            WAKE_WORD_MODEL = "missing_model"
            WAKE_WORD_THRESHOLD = 0.5
            WAKE_WORD_ACTIVE_WINDOW_SECS = 12
            WAKE_WORD_PERSONAS = {}
            WAKE_WORD_SWITCH_TIMEOUT_SECS = 0.5

        settings = wake_word.settings_from_config(Cfg)

        self.assertTrue(settings.effective_targets[0].model_path.endswith(".onnx"))


class WakeWordGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_armed_gate_drops_audio(self):
        gate = wake_word.WakeWordGate(SETTINGS, detector_factory=lambda _s: FakeDetector())

        await run_test(
            gate,
            frames_to_send=[audio_frame(), audio_frame()],
            expected_down_frames=[],
        )

    async def test_trigger_opens_the_gate_for_following_audio(self):
        events: list[dict] = []
        detector = FakeDetector(scores=[0.9])
        gate = wake_word.WakeWordGate(
            SETTINGS, detector_factory=lambda _s: detector, on_event=events.append
        )

        await run_test(
            gate,
            frames_to_send=[audio_frame(), audio_frame()],
            expected_down_frames=[InputAudioRawFrame],
        )

        self.assertEqual([e["state"] for e in events], ["armed", "awake"])
        self.assertEqual(events[-1]["phase"], "wake_word_detected")
        self.assertEqual(events[-1]["model"], "hey_jarvis")
        self.assertEqual(events[-1]["window_secs"], SETTINGS.active_window_secs)
        self.assertGreater(events[-1]["remaining_secs"], 0)
        self.assertEqual(detector.resets, 1)  # trigger resets the model buffer

    async def test_wake_score_event_is_plain_float(self):
        class ScalarScore:
            def __float__(self):
                return 0.81

            def __ge__(self, _other):
                return True

        events: list[dict] = []
        detector = FakeDetector(scores=[ScalarScore()])
        gate = wake_word.WakeWordGate(
            SETTINGS, detector_factory=lambda _s: detector, on_event=events.append
        )

        await run_test(
            gate,
            frames_to_send=[audio_frame(), audio_frame()],
            expected_down_frames=[InputAudioRawFrame],
        )

        self.assertEqual(events[-1]["score"], 0.81)
        self.assertIs(type(events[-1]["score"]), float)

    async def test_window_expiry_rearms_and_drops_again(self):
        events: list[dict] = []
        settings = wake_word.WakeWordSettings(
            enabled=True, model="hey_jarvis", threshold=0.5, active_window_secs=0.2
        )
        gate = wake_word.WakeWordGate(
            settings,
            detector_factory=lambda _s: FakeDetector(scores=[0.9]),
            on_event=events.append,
        )

        await run_test(
            gate,
            frames_to_send=[
                audio_frame(),  # wake trigger (dropped)
                audio_frame(),  # passes while awake
                SleepFrame(0.5),
                audio_frame(),  # window lapsed -> dropped again
            ],
            expected_down_frames=[InputAudioRawFrame],
        )

        self.assertEqual([e["state"] for e in events], ["armed", "awake", "armed"])

    async def test_window_never_lapses_mid_utterance(self):
        settings = wake_word.WakeWordSettings(
            enabled=True, model="hey_jarvis", threshold=0.5, active_window_secs=0.5
        )
        gate = wake_word.WakeWordGate(
            settings, detector_factory=lambda _s: FakeDetector(scores=[0.9])
        )

        await run_test(
            gate,
            frames_to_send=[
                audio_frame(),  # wake trigger (dropped)
                UserStartedSpeakingFrame(),
                SleepFrame(1.0),  # well past the 0.5s window...
                audio_frame(),  # ...but the user is still mid-sentence
            ],
            expected_down_frames=[UserStartedSpeakingFrame, InputAudioRawFrame],
        )

    async def test_finished_user_turn_refreshes_the_window(self):
        settings = wake_word.WakeWordSettings(
            enabled=True, model="hey_jarvis", threshold=0.5, active_window_secs=1.0
        )
        gate = wake_word.WakeWordGate(
            settings, detector_factory=lambda _s: FakeDetector(scores=[0.9])
        )

        await run_test(
            gate,
            frames_to_send=[
                audio_frame(),  # wake trigger (dropped)
                UserStartedSpeakingFrame(),
                SleepFrame(0.6),
                UserStoppedSpeakingFrame(),  # finished turn -> fresh 1.0s window
                SleepFrame(0.6),  # 1.2s since trigger: stale window would be shut
                audio_frame(),
            ],
            expected_down_frames=[
                UserStartedSpeakingFrame,
                UserStoppedSpeakingFrame,
                InputAudioRawFrame,
            ],
        )

    async def test_bot_reply_opens_the_window_without_wake_phrase(self):
        gate = wake_word.WakeWordGate(SETTINGS, detector_factory=lambda _s: FakeDetector())

        await run_test(
            gate,
            frames_to_send=[BotStoppedSpeakingFrame(), audio_frame()],
            expected_down_frames=[BotStoppedSpeakingFrame, InputAudioRawFrame],
        )

    async def test_detector_failure_falls_back_to_always_listening(self):
        events: list[dict] = []

        def broken_factory(_settings):
            raise RuntimeError("no model")

        gate = wake_word.WakeWordGate(
            SETTINGS, detector_factory=broken_factory, on_event=events.append
        )

        await run_test(
            gate,
            frames_to_send=[audio_frame()],
            expected_down_frames=[InputAudioRawFrame],
        )

        self.assertEqual([e["state"] for e in events], ["bypass"])

    async def test_unsupported_audio_format_passes_through(self):
        gate = wake_word.WakeWordGate(SETTINGS, detector_factory=lambda _s: FakeDetector())

        await run_test(
            gate,
            frames_to_send=[audio_frame(rate=8000)],
            expected_down_frames=[InputAudioRawFrame],
        )

    async def test_highest_confidence_wake_switches_persona_before_audio_passes(self):
        order = []
        settings = wake_word.WakeWordSettings(
            enabled=True,
            targets=(
                wake_word.WakeWordTarget("hey_jarvis", "Jarvis", 0.5),
                wake_word.WakeWordTarget("hey_jess", "Jess", 0.5),
            ),
        )
        detector = MultiFakeDetector(scores=[{"hey_jarvis": 0.7, "hey_jess": 0.9}, {}])

        async def switch(persona):
            order.append(("switch", persona))

        gate = wake_word.WakeWordGate(
            settings,
            detector_factory=lambda _s: detector,
            on_persona=switch,
            on_event=lambda event: order.append((event["state"], event.get("persona"))),
        )

        await run_test(
            gate,
            frames_to_send=[audio_frame(), audio_frame()],
            expected_down_frames=[InputAudioRawFrame],
        )

        self.assertLess(order.index(("switch", "Jess")), order.index(("awake", "Jess")))

    async def test_equal_scores_choose_configuration_order(self):
        selected = []
        settings = wake_word.WakeWordSettings(
            enabled=True,
            targets=(
                wake_word.WakeWordTarget("hey_jarvis", "Jarvis", 0.5),
                wake_word.WakeWordTarget("hey_jess", "Jess", 0.5),
            ),
        )
        gate = wake_word.WakeWordGate(
            settings,
            detector_factory=lambda _s: MultiFakeDetector(
                scores=[{"hey_jarvis": 0.8, "hey_jess": 0.8}]
            ),
            on_persona=lambda persona: selected.append(persona),
        )

        await run_test(gate, frames_to_send=[audio_frame()], expected_down_frames=[])

        self.assertEqual(selected, ["Jarvis"])

    async def test_failed_persona_switch_keeps_gate_armed(self):
        events = []
        settings = wake_word.WakeWordSettings(
            enabled=True,
            targets=(wake_word.WakeWordTarget("hey_jarvis", "Jarvis", 0.5),),
        )

        async def fail(_persona):
            raise RuntimeError("voice unavailable")

        gate = wake_word.WakeWordGate(
            settings,
            detector_factory=lambda _s: MultiFakeDetector(scores=[{"hey_jarvis": 0.9}, {}]),
            on_persona=fail,
            on_event=events.append,
        )

        await run_test(
            gate,
            frames_to_send=[audio_frame(), audio_frame()],
            expected_down_frames=[],
        )

        self.assertFalse(gate.awake)
        self.assertIn("switch_failed", [event["state"] for event in events])

    async def test_different_wake_word_switches_during_open_window(self):
        selected = []
        settings = wake_word.WakeWordSettings(
            enabled=True,
            targets=(
                wake_word.WakeWordTarget("hey_jarvis", "Jarvis", 0.5),
                wake_word.WakeWordTarget("hey_jess", "Jess", 0.5),
            ),
        )
        gate = wake_word.WakeWordGate(
            settings,
            detector_factory=lambda _s: MultiFakeDetector(
                scores=[{"hey_jarvis": 0.9}, {"hey_jess": 0.9}, {}]
            ),
            on_persona=lambda persona: selected.append(persona),
        )

        await run_test(
            gate,
            frames_to_send=[audio_frame(), audio_frame(), audio_frame()],
            expected_down_frames=[InputAudioRawFrame],
        )

        self.assertEqual(selected, ["Jarvis", "Jess"])


if __name__ == "__main__":
    unittest.main()
