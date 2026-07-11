from __future__ import annotations

import subprocess
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


root = Path.cwd()
processor_path = root / "remote_agent_protocol/session_processors.py"
test_path = root / "tests/test_session_processors.py"

source = processor_path.read_text(encoding="utf-8")
source = replace_once(source, "import inspect\nimport re\n", "import inspect\nimport re\nimport time\n", "time import")
source = replace_once(
    source,
    "    TranscriptionFrame,\n    TTSSpeakFrame,\n    UserStoppedSpeakingFrame,",
    "    TranscriptionFrame,\n    TTSAudioRawFrame,\n    TTSSpeakFrame,\n    UserStartedSpeakingFrame,\n    UserStoppedSpeakingFrame,",
    "audio and user-start frame imports",
)
source = replace_once(
    source,
    "from remote_agent_protocol import dashboard, multimodal_prompt, voice_commands\n",
    "from remote_agent_protocol import dashboard, multimodal_prompt, voice_commands\nfrom remote_agent_protocol.avatar_audio import compute_pcm16_envelope\n",
    "avatar envelope import",
)
source = replace_once(
    source,
    "\n\nclass TranscriptTap(FrameProcessor):",
    '''

class AvatarAudioTap(FrameProcessor):
    """Observe outgoing TTS PCM without mutating or delaying audio frames."""

    def __init__(
        self,
        on_envelope,
        *,
        publish_interval_secs: float = 0.05,
        clock=time.monotonic,
        wall_clock=time.time,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._on_envelope = on_envelope
        self._publish_interval_secs = max(0.01, float(publish_interval_secs))
        self._clock = clock
        self._wall_clock = wall_clock
        self._last_publish = float("-inf")

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Publish a rate-limited envelope and always pass the original frame."""
        await super().process_frame(frame, direction)
        if (
            direction is FrameDirection.DOWNSTREAM
            and isinstance(frame, TTSAudioRawFrame)
            and self._on_envelope is not None
        ):
            now = self._clock()
            if now - self._last_publish >= self._publish_interval_secs:
                envelope = compute_pcm16_envelope(
                    frame.audio,
                    frame.sample_rate,
                    frame.num_channels,
                    timestamp=self._wall_clock(),
                )
                try:
                    self._on_envelope(envelope)
                except Exception as exc:
                    logger.warning(f"Avatar audio envelope callback failed: {exc}")
                self._last_publish = now
        await self.push_frame(frame, direction)


class TranscriptTap(FrameProcessor):''',
    "AvatarAudioTap insertion",
)
source = replace_once(
    source,
    "        elif isinstance(frame, UserStoppedSpeakingFrame):\n            self._emit({\"type\": \"turn\", \"event\": \"user_stopped\"})",
    "        elif isinstance(frame, UserStartedSpeakingFrame):\n            self._emit({\"type\": \"turn\", \"event\": \"user_started\"})\n        elif isinstance(frame, UserStoppedSpeakingFrame):\n            self._emit({\"type\": \"turn\", \"event\": \"user_stopped\"})",
    "user started telemetry",
)
processor_path.write_text(source, encoding="utf-8")

tests = test_path.read_text(encoding="utf-8")
tests = replace_once(
    tests,
    "    TranscriptionFrame,\n    TTSSpeakFrame,\n    UserStoppedSpeakingFrame,",
    "    TranscriptionFrame,\n    TTSAudioRawFrame,\n    TTSSpeakFrame,\n    UserStartedSpeakingFrame,\n    UserStoppedSpeakingFrame,",
    "test frame imports",
)
tests = replace_once(
    tests,
    "from remote_agent_protocol.session_processors import (\n    DelegationTap,",
    "from remote_agent_protocol.session_processors import (\n    AvatarAudioTap,\n    DelegationTap,",
    "test processor import",
)
tests = replace_once(
    tests,
    "\n\nclass TranscriptTapRoleTests(unittest.IsolatedAsyncioTestCase):",
    '''

class AvatarAudioTapTests(unittest.IsolatedAsyncioTestCase):
    async def test_tts_audio_passes_through_unchanged_and_emits_envelope(self):
        events = []
        frame = TTSAudioRawFrame(
            audio=b"\xff\x7f" * 80,
            sample_rate=24000,
            num_channels=1,
        )
        tap = AvatarAudioTap(
            events.append,
            publish_interval_secs=0.05,
            clock=lambda: 1.0,
            wall_clock=lambda: 10.0,
        )

        frames, _ = await run_test(tap, frames_to_send=[frame])

        self.assertIs(frames[0], frame)
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0].voiced)
        self.assertEqual(events[0].timestamp, 10.0)

    async def test_rate_limit_drops_intermediate_envelopes(self):
        events = []
        ticks = iter([0.0, 0.01, 0.06])
        tap = AvatarAudioTap(events.append, clock=lambda: next(ticks), wall_clock=lambda: 20.0)
        frames = [
            TTSAudioRawFrame(audio=b"\x00\x20" * 40, sample_rate=24000, num_channels=1)
            for _ in range(3)
        ]

        await run_test(tap, frames_to_send=frames)

        self.assertEqual(len(events), 2)

    async def test_callback_failure_does_not_block_audio(self):
        frame = TTSAudioRawFrame(audio=b"\x00\x20" * 40, sample_rate=24000, num_channels=1)

        def fail(_envelope):
            raise RuntimeError("telemetry unavailable")

        frames, _ = await run_test(AvatarAudioTap(fail), frames_to_send=[frame])

        self.assertIs(frames[0], frame)


class TranscriptTapUserStartTests(unittest.IsolatedAsyncioTestCase):
    async def test_telemetry_tap_reports_user_started(self):
        events = []

        await run_test(
            TranscriptTap(events.append, role="telemetry"),
            frames_to_send=[UserStartedSpeakingFrame(), UserStoppedSpeakingFrame()],
        )

        self.assertEqual(
            events,
            [
                {"type": "turn", "event": "user_started"},
                {"type": "turn", "event": "user_stopped"},
            ],
        )


class TranscriptTapRoleTests(unittest.IsolatedAsyncioTestCase):''',
    "processor tests insertion",
)
test_path.write_text(tests, encoding="utf-8")

subprocess.run(
    ["python", "-m", "ruff", "format", str(processor_path), str(test_path)],
    check=True,
)
subprocess.run(
    [
        "python",
        "-m",
        "pytest",
        "tests/test_avatar_audio.py",
        "tests/test_session_processors.py",
        "-q",
        "--disable-warnings",
        "--maxfail=1",
    ],
    check=True,
)

Path(__file__).unlink()
subprocess.run(
    [
        "git",
        "add",
        "remote_agent_protocol/session_processors.py",
        "tests/test_session_processors.py",
        ".github/avatar_tasks/task3.py",
    ],
    check=True,
)
subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
subprocess.run(
    ["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
    check=True,
)
subprocess.run(["git", "commit", "-m", "feat(avatar): observe TTS audio and user speech"], check=True)
subprocess.run(["git", "push", "origin", "HEAD:feature/animated-butler-avatar"], check=True)
print("TASK 3 DONE: audio tap and user-start telemetry tests passed")
