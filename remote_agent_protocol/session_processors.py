"""Small Pipecat processors used by VoiceSession."""

from __future__ import annotations

import inspect
import re
import time
from collections.abc import Callable

from loguru import logger

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InputAudioRawFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    MetricsFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from remote_agent_protocol import config as cfg
from remote_agent_protocol import dashboard, multimodal_prompt, voice_commands
from remote_agent_protocol.avatar_audio import compute_pcm16_envelope

EventCallback = Callable[[dict], None]


class MicGate(FrameProcessor):
    """Drop mic audio while the bot is speaking, or while explicitly muted."""

    def __init__(self, muted: bool = False, **kwargs):
        """Initialize the gate.

        Args:
            muted: Initial mute state, so a rebuilt session honors the UI.
            **kwargs: Additional arguments passed to FrameProcessor.
        """
        super().__init__(**kwargs)
        self._bot_speaking = False
        self.muted = muted
        self.input_enabled = True

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Drop input audio while muted or while the bot is speaking."""
        await super().process_frame(frame, direction)
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
            await self.push_frame(frame, direction)
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            await self.push_frame(frame, direction)
        elif isinstance(frame, InputAudioRawFrame) and (
            self._bot_speaking or self.muted or not self.input_enabled
        ):
            return
        else:
            await self.push_frame(frame, direction)


# Whisper reliably hallucinates these stock phrases on silence or non-speech
# audio -- they saturate its training data from video captions/outros. When one
# is the ENTIRE utterance it is almost never a real turn, so responding to it
# (or worse, parsing it as a command) is pure noise. Longer utterances that
# merely contain the phrase ("thanks, now open the file") are left alone.
_STT_HALLUCINATION_PHRASES = frozenset(
    {
        "thank you",
        "thanks",
        "thank you very much",
        "thank you so much",
        "thanks for watching",
        "thank you for watching",
        "thanks for watching everyone",
        "please subscribe",
        "like and subscribe",
        "you",
        "bye",
        "bye bye",
        "okay",
        "so",
    }
)


def _normalize_utterance(text: str) -> str:
    """Lowercase, strip surrounding punctuation/space, and collapse whitespace."""
    return " ".join(re.sub(r"[^\w\s]", " ", text).lower().split())


def is_stt_hallucination(text: str) -> bool:
    """True if the whole utterance is a known Whisper silence-hallucination."""
    return _normalize_utterance(text) in _STT_HALLUCINATION_PHRASES


class STTNoiseFilter(FrameProcessor):
    """Drop transcriptions that are only a known Whisper silence-hallucination.

    Sits right after STT so the phantom text never reaches the transcript, the
    delegation parser, or the LLM -- it just disappears, as if the user had
    said nothing.
    """

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Swallow a hallucinated stock phrase; pass everything else through."""
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and is_stt_hallucination(frame.text):
            logger.info(f"Dropping STT hallucination: {frame.text!r}")
            return
        await self.push_frame(frame, direction)


class ManualPromptDraftTap(FrameProcessor):
    """Hold completed STT transcripts only when shared context is active."""

    def __init__(self, enabled, on_draft, **kwargs):
        """Initialize the draft tap.

        Args:
            enabled: Callable returning whether this transcript should be held.
            on_draft: Callable receiving ``(text, intent)`` for draft updates.
            **kwargs: Additional arguments passed to FrameProcessor.
        """
        super().__init__(**kwargs)
        self._enabled = enabled
        self._on_draft = on_draft

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Convert user transcripts to draft events instead of LLM turns."""
        await super().process_frame(frame, direction)
        if (
            isinstance(frame, TranscriptionFrame)
            and frame.text.strip()
            and self._should_hold(frame.text)
        ):
            text = frame.text.strip()
            self._on_draft(text, multimodal_prompt.send_intent(text))
            return
        await self.push_frame(frame, direction)

    def _should_hold(self, text: str) -> bool:
        try:
            return bool(self._enabled(text))
        except TypeError:
            return bool(self._enabled())


def resolve_delegation(text: str, default_backend: str | None = None) -> tuple[str, str] | None:
    """Return (backend, task) for explicit or implicit delegations, else None."""
    parsed = voice_commands.parse_delegation(text, cfg.AGENT_BACKENDS, cfg.AGENT_SPOKEN_ALIASES)
    if parsed is None and cfg.AGENT_AUTO_DELEGATE:
        task = voice_commands.parse_implicit_task(text)
        if task is not None:
            parsed = (default_backend or cfg.AGENT_DEFAULT_BACKEND, task)
    return parsed


class DelegationTap(FrameProcessor):
    """Rewrite spoken delegation commands into truthful LLM acknowledgements.

    Also intercepts a spoken reply to a *pending confirmation* ("yes" / "cancel")
    before it can be parsed as a fresh command, so a held job is approved or
    dropped rather than misread.
    """

    def __init__(
        self,
        on_delegate,
        resolve,
        confirm_check=None,
        context_refresh=None,
        control_check=None,
        **kwargs,
    ):
        """Initialize the tap.

        Args:
            on_delegate: ``(agent, task) -> ack str``; dispatches or holds a job.
            resolve: ``(text) -> (agent, task) | None`` delegation parser; may
                be async (the intent router classifies over the network).
            confirm_check: ``(text) -> ack str | None``; resolves a pending
                confirmation from a spoken yes/no.
            context_refresh: ``() -> Frame | None``; a settings frame pushed
                ahead of each user utterance (e.g. a fresh system instruction
                carrying the current date/time).
            control_check: ``(text) -> ack str | None``; may be async and
                intercepts model-switch/retry commands before delegation.
            **kwargs: Additional arguments passed to FrameProcessor.
        """
        super().__init__(**kwargs)
        self._on_delegate = on_delegate
        self._resolve = resolve
        self._confirm_check = confirm_check
        self._context_refresh = context_refresh
        self._control_check = control_check

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Swap recognized delegation commands for truthful LLM instructions."""
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and frame.text.strip():
            if self._context_refresh is not None:
                refresh = self._context_refresh()
                if refresh is not None:
                    await self.push_frame(refresh, direction)
            ack = self._control_check(frame.text) if self._control_check else None
            if inspect.isawaitable(ack):
                ack = await ack
            if ack is None:
                ack = self._confirm_check(frame.text) if self._confirm_check else None
            if ack is not None:
                frame.text = ack
            else:
                parsed = self._resolve(frame.text)
                if inspect.isawaitable(parsed):
                    parsed = await parsed
                if parsed is not None:
                    agent, task = parsed
                    logger.info(f"Voice delegation -> [{agent}] {task}")
                    frame.text = self._on_delegate(agent, task)
        await self.push_frame(frame, direction)


# The LLM requests a delegation by embedding this marker in its reply text.
_MARKER_RE = re.compile(r"\[\[\s*delegate\s*:\s*(.+?)\s*\]\]", re.IGNORECASE | re.DOTALL)
_MARKER_HEAD = "[[delegate:"

# Fabricated-delegation guard: a reply that names the tool agent alongside a
# dispatch-style verb is promising work -- without a marker in the same reply,
# that work will never happen.
_PROMISE_VERB_RE = re.compile(
    r"\b(?:dispatch|summon|task|instruct|consult|deploy|engag|send|sending|"
    r"check|verif|quer|fetch|initiat|relay|activat|updat|run|running|working|look|ask)\w*\b",
    re.IGNORECASE,
)


def is_placeholder_task(task: str) -> bool:
    """True if a marker parroted a prompt example rather than a real task."""
    placeholders = {p.lower() for p in cfg.DELEGATION_PLACEHOLDER_TASKS}
    stripped = task.strip(" .!?'\"").lower()
    return not stripped or stripped in placeholders


def looks_like_delegation_promise(text: str) -> bool:
    """True if ``text`` claims agent work is happening or about to happen.

    Pairs a dispatch-style verb with any known agent noun (configured nouns,
    backend names, spoken aliases). Used only on responses that carried no
    ``[[delegate: ...]]`` marker, where such a claim is necessarily false.
    """
    if not _PROMISE_VERB_RE.search(text):
        return False
    nouns = set(cfg.AGENT_PROMISE_NOUNS) | set(cfg.AGENT_BACKENDS) | set(cfg.AGENT_SPOKEN_ALIASES)
    nouns.discard("mock")  # too common a word to treat as an agent reference
    pattern = "|".join(re.escape(noun) for noun in sorted(nouns))
    return bool(re.search(rf"\b(?:{pattern})\b", text, re.IGNORECASE))


def _could_become_marker(tail: str) -> bool:
    """True if ``tail`` is a (possibly partial) prefix of a delegation marker."""
    head_index = 0
    for ch in tail.lower():
        if head_index >= len(_MARKER_HEAD):
            return True  # full head seen; the closing ]] just hasn't arrived
        if ch == _MARKER_HEAD[head_index]:
            head_index += 1
        elif ch in " \t\n" and _MARKER_HEAD[head_index] != "[":
            continue  # tolerate spaces like "[[ delegate :"
        else:
            return False
    return True


class LLMDelegateTap(FrameProcessor):
    """Dispatch ``[[delegate: task]]`` markers the LLM embeds in its replies.

    Sits right after the LLM so the marker never reaches TTS or the GUI
    transcript: streamed text is buffered just enough to strip complete
    markers (and hold back partial ones spanning token boundaries), everything
    else flows through untouched. Each unique task fires ``on_delegate`` once
    per response; actual dispatch stays in session code.
    """

    def __init__(self, on_delegate, on_response=None, **kwargs):
        """Initialize the tap.

        Args:
            on_delegate: ``(task) -> None``; called once per unique task.
            on_response: ``(text, dispatched) -> None``; called at the end of
                each response with its full text and whether any marker
                dispatched, so the session can catch replies that promise
                agent work without actually requesting it.
            **kwargs: Additional arguments passed to FrameProcessor.
        """
        super().__init__(**kwargs)
        self._on_delegate = on_delegate
        self._on_response = on_response
        self._buffer = ""
        self._response_text = ""
        self._dispatched: set[str] = set()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Strip delegation markers out of streamed LLM text; pass the rest."""
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMFullResponseStartFrame):
            self._buffer = ""
            self._response_text = ""
            self._dispatched = set()
            await self.push_frame(frame, direction)
        elif isinstance(frame, LLMTextFrame):
            self._buffer += frame.text
            self._response_text += frame.text
            text = self._drain(final=False)
            if text:
                await self.push_frame(LLMTextFrame(text=text), direction)
        elif isinstance(frame, LLMFullResponseEndFrame):
            text = self._drain(final=True)
            if text:
                await self.push_frame(LLMTextFrame(text=text), direction)
            await self.push_frame(frame, direction)
            if self._on_response is not None:
                try:
                    self._on_response(self._response_text, bool(self._dispatched))
                except Exception as exc:  # the guard must never break the pipeline
                    logger.warning(f"LLMDelegateTap on_response raised: {exc}")
        else:
            await self.push_frame(frame, direction)

    def _drain(self, *, final: bool) -> str:
        """Consume complete markers, then return the text safe to emit now."""
        while True:
            match = _MARKER_RE.search(self._buffer)
            if match is None:
                break
            task = " ".join(match.group(1).split())
            if is_placeholder_task(task):
                logger.warning(f"Ignoring placeholder delegation marker: {task!r}")
            elif task and task.lower() not in self._dispatched:
                self._dispatched.add(task.lower())
                logger.info(f"LLM delegation marker -> {task}")
                self._on_delegate(task)
            self._buffer = self._buffer[: match.start()] + self._buffer[match.end() :]

        hold = self._hold_index()
        if final:
            # Anything still marker-shaped at end of response is malformed --
            # drop it rather than speak half a marker aloud.
            if hold < len(self._buffer):
                logger.warning(f"Dropping unterminated delegation marker: {self._buffer[hold:]!r}")
            text, self._buffer = self._buffer[:hold], ""
            return text
        text, self._buffer = self._buffer[:hold], self._buffer[hold:]
        return text

    def _hold_index(self) -> int:
        """Index up to which the buffer can be emitted without losing a marker."""
        for index, ch in enumerate(self._buffer):
            if ch == "[" and _could_become_marker(self._buffer[index:]):
                return index
        return len(self._buffer)


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
        self._monotonic = clock
        self._wall_time = wall_clock
        self._last_publish = float("-inf")

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Publish a rate-limited envelope and always pass the original frame."""
        await super().process_frame(frame, direction)
        if (
            direction is FrameDirection.DOWNSTREAM
            and isinstance(frame, TTSAudioRawFrame)
            and self._on_envelope is not None
        ):
            now = self._monotonic()
            if now - self._last_publish >= self._publish_interval_secs:
                envelope = compute_pcm16_envelope(
                    frame.audio,
                    frame.sample_rate,
                    frame.num_channels,
                    timestamp=self._wall_time(),
                )
                try:
                    self._on_envelope(envelope)
                except Exception as exc:
                    logger.warning(f"Avatar audio envelope callback failed: {exc}")
                self._last_publish = now
        await self.push_frame(frame, direction)


class TranscriptTap(FrameProcessor):
    """Pass-through observer that reports pipeline activity as GUI events.

    Three instances sit at different pipeline positions and many frames flow
    through more than one of them, so each instance owns a disjoint slice of
    the events (``role``) and nothing is reported twice:

    - ``"user"`` (after STT): raw user transcriptions, pre-delegation.
    - ``"assistant"`` (after the LLM): aggregated assistant replies.
    - ``"telemetry"`` (after the output transport): metrics plus speaking/turn
      markers. This is the only spot every ``MetricsFrame`` -- including the
      TTS service's own -- passes exactly once.
    """

    def __init__(self, on_event: EventCallback | None, role: str = "telemetry", **kwargs):
        """Initialize the tap.

        Args:
            on_event: Callback receiving plain event dicts for the GUI.
            role: Which event slice this instance owns -- "user", "assistant",
                or "telemetry" (see class docstring).
            **kwargs: Additional arguments passed to FrameProcessor.
        """
        super().__init__(**kwargs)
        self._on_event = on_event
        self._role = role
        self._llm_buffer: list[str] = []

    def _emit(self, event: dict) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(event)
        except Exception as exc:
            logger.warning(f"TranscriptTap on_event raised: {exc}")

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Emit this role's events for interesting frames; pass everything on."""
        await super().process_frame(frame, direction)
        if self._role == "user":
            if isinstance(frame, TranscriptionFrame) and frame.text.strip():
                self._emit({"type": "transcript", "role": "user", "text": frame.text.strip()})
        elif self._role == "assistant":
            if isinstance(frame, LLMTextFrame):
                self._llm_buffer.append(frame.text)
            elif isinstance(frame, LLMFullResponseEndFrame):
                self._emit_assistant_text()
            elif isinstance(frame, TTSSpeakFrame) and frame.text.strip():
                # Injected speech -- agent status updates (started, still
                # working, finished, handoff) -- bypasses the LLM, so it never
                # produces LLMTextFrames. Mirror it to the transcript here or it
                # would be spoken aloud but never shown on screen.
                self._emit({"type": "transcript", "role": "assistant", "text": frame.text.strip()})
        elif isinstance(frame, MetricsFrame):
            self._emit_metrics(frame)
        elif isinstance(frame, UserStartedSpeakingFrame):
            self._emit({"type": "turn", "event": "user_started"})
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._emit({"type": "turn", "event": "user_stopped"})
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._emit({"type": "speaking", "value": True})
            self._emit({"type": "turn", "event": "bot_started"})
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._emit({"type": "speaking", "value": False})
        await self.push_frame(frame, direction)

    def _emit_metrics(self, frame: MetricsFrame) -> None:
        for metric in frame.data:
            kind = _metric_kind(metric)
            if kind is None:
                continue
            event = dashboard.metric_event(metric, kind)
            if event is not None:
                self._emit(event)

    def _emit_assistant_text(self) -> None:
        text = "".join(self._llm_buffer).strip()
        self._llm_buffer.clear()
        if text:
            self._emit({"type": "transcript", "role": "assistant", "text": text})


def _metric_kind(metric) -> str | None:
    name = type(metric).__name__
    if name == "TTFBMetricsData":
        return "ttfb"
    if name == "ProcessingMetricsData":
        return "processing"
    return None
