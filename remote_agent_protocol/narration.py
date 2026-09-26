"""Generated narration for agent work -- spoken lines written fresh each time.

Canned status lines are what break the illusion of talking to someone: the
third time you hear "Still working on it", it is plainly a progress bar with
a voice. Every line here is generated instead, in the active persona's voice,
from what a job is *actually* doing.

Cost discipline, because this is a speech-first tool:

- It runs on the small classifier model `intent_router` already keeps
  resident (`NARRATION_MODEL`, defaulting to `INTENT_MODEL` at num_ctx 2048),
  never the large chat model answering the user -- so narration adds no VRAM
  and never queues behind a reply.
- Lines are one short sentence, and callers can prefetch during a job's dead
  time so the audio path finds the text already written.
- Missing the deadline is normal and cheap: a rotating phrase pool speaks
  instead. Speech never waits on generation.
"""

from __future__ import annotations

import random
import re
from collections import deque
from dataclasses import dataclass

import aiohttp
from loguru import logger

from remote_agent_protocol import config as cfg
from remote_agent_protocol import llm_endpoint

# A spoken sentence, not a paragraph. Anything longer is a model that ignored
# the instruction, and gets clipped rather than read aloud in full.
SPOKEN_MAX_CHARS = 180
# How far back repetition is checked. Long, because the thing that gives a
# session away is a phrasing resurfacing twenty minutes later, not twice in a
# row -- and progress lines are minutes apart, so a short window sees nothing.
RECENT_MEMORY = 40
# How many of those are quoted back to the model. Small, because they are
# prompt tokens on every line and the check below is the real enforcement.
PROMPT_RECENT = 4
# Vocabulary overlap above which a candidate counts as "we just said that".
_REPEAT_RATIO = 0.6
# Sentence-shape overlap, which catches a model reusing one frame with the
# nouns swapped -- "X is still poking at Y" forever.
_SHAPE_RATIO = 0.5

KIND_STARTED = "started"
KIND_WORKING = "working"
KIND_WAITING = "waiting"
KIND_DONE = "done"
KIND_FAILED = "failed"
KIND_IDLE = "idle"
KIND_CONSULT = "consult"


@dataclass(frozen=True)
class Moment:
    """What just happened, as facts -- never as pre-written prose.

    The narrator writes the sentence; callers only supply what is true. That
    split is the point: a caller that passes prose has already chosen the
    wording, which is how canned lines creep back in.
    """

    kind: str
    agent: str = ""
    task: str = ""
    detail: str = ""  # the agent's own words: current action, tool, or step
    elapsed_secs: float = 0.0
    note: str = ""  # anything else worth a mention (peer agent, failure kind)


# Degradation pools, used when generation misses its deadline -- which on a
# busy GPU is often, so these carry real weight rather than being a token
# safety net. Two rules learned the hard way: they have to be deep enough
# that rotation isn't obvious over an evening, and a "plain" variant has to
# exist for when nothing specific is known, because splicing a placeholder
# into a template produces exactly the robot voice this module exists to kill
# ("Still running: still going.").
_FALLBACKS: dict[str, dict[str, tuple[str, ...]]] = {
    KIND_STARTED: {
        "plain": (
            "{agent} is on it.",
            "Handed that to {agent}.",
            "{agent} just picked that up.",
            "Sent it over to {agent}.",
            "{agent}'s taking that one.",
            "That's with {agent} now.",
            "Passed it to {agent}.",
            "{agent} has it.",
        )
    },
    KIND_WORKING: {
        "plain": (
            "{agent} is still in there.",
            "Nothing back from {agent} yet.",
            "{agent}'s gone quiet on that one.",
            "Still waiting on {agent}.",
            "{agent} is taking its time.",
            "{agent} hasn't surfaced yet.",
            "That's still {agent}'s problem for the minute.",
            "No word from {agent} so far.",
        ),
        "detail": (
            "{agent} is onto {detail} now.",
            "{detail}, apparently.",
            "{agent}'s up to {detail}.",
            "Latest from {agent}: {detail}.",
            "It's {detail} at the moment.",
            "{agent} moved on to {detail}.",
            "Sounds like {detail}.",
            "{agent} says {detail}.",
        ),
    },
    KIND_WAITING: {
        "plain": (
            "{agent} needs you for something.",
            "{agent} is stuck and wants a decision.",
            "{agent}'s waiting on you.",
            "Something's blocking {agent}.",
            "{agent} won't go on without you.",
        ),
        "detail": (
            "{agent} needs you -- {detail}.",
            "{agent} is stuck: {detail}.",
            "{agent} wants a decision: {detail}.",
            "From {agent}: {detail}.",
            "{agent} is holding on {detail}.",
        ),
    },
    KIND_DONE: {
        "plain": (
            "{agent} is back.",
            "Word from {agent}.",
            "{agent} wrapped up.",
            "That's {agent} done.",
            "{agent} came back with this.",
            "Here's what {agent} got.",
            "Right, {agent}'s finished.",
            "{agent} turned something up.",
        )
    },
    KIND_FAILED: {
        "plain": (
            "{agent} hit a wall.",
            "That one died on {agent}.",
            "{agent} couldn't finish it.",
            "No luck from {agent}.",
            "{agent} gave up on that one.",
        ),
        "detail": (
            "{agent} hit a wall -- {detail}.",
            "That died on {agent}: {detail}.",
            "{agent} couldn't do it: {detail}.",
            "{agent} fell over on {detail}.",
        ),
    },
    KIND_IDLE: {
        "plain": (
            "That's everything wrapped up.",
            "All quiet again.",
            "Everyone's finished.",
            "Nothing left running.",
            "That's the lot.",
            "They're all done.",
            "Board's clear.",
            "Nothing else pending.",
        )
    },
    KIND_CONSULT: {
        "plain": (
            "{agent} is asking {note} about it.",
            "{agent} pulled {note} in.",
            "{agent} wants {note}'s read on this.",
            "{agent} and {note} are comparing notes.",
        )
    },
}

# Written for a 3B: examples carry it, rules alone don't. The facts block it
# receives is telegraphic ("HELPER: codex"), which on its own teaches a
# telegraphic register back, so the examples exist mostly to show the shape of
# the answer -- a person mentioning something, not a field report.
_SYSTEM = (
    "You are {persona}, a voice assistant talking with one person. {flavour}"
    "You mention, in passing, what a background helper is doing for them. "
    "Reply with one plain spoken sentence of at most 18 words, as speech to be "
    "read aloud. Work the specific details into it.\n\n"
    "Examples of the register:\n"
    "facts: HELPER: codex / RIGHT NOW: running the test suite\n"
    "you: Codex is running the tests now.\n"
    "facts: HELPER: hermes / RIGHT NOW: reading the auth module / RUNNING FOR: 3 minutes\n"
    "you: Hermes has been buried in the auth module for a few minutes.\n"
    "facts: HELPER: code-puppy / the helper failed\n"
    "you: Code Puppy gave up on that one, I'm afraid.\n\n"
    "Keep it plain: no markdown, no emoji, no quotes, no stage directions, no "
    "questions back. Say it differently from everything under RECENT."
)

_INTENT: dict[str, str] = {
    KIND_STARTED: "A helper just took on a task.",
    KIND_WORKING: "A helper is partway through a task.",
    KIND_WAITING: "A helper is stuck and needs the person to decide something.",
    KIND_DONE: "A helper just finished; you are only introducing the answer, which "
    "is read out straight after your sentence -- do not state the answer yourself.",
    KIND_FAILED: "A helper failed at its task.",
    KIND_IDLE: "Every background helper has finished; nothing is running.",
    KIND_CONSULT: "One helper is consulting another helper.",
}


def _clean_line(raw: str) -> str:
    """Strip a small model's habitual decorations down to one spoken sentence."""
    text = " ".join(str(raw or "").split())
    text = re.sub(r"^[\"'`*_\s]+|[\"'`*_\s]+$", "", text)
    # "Jess:" / "Narrator:" prefixes are a stock small-model tic.
    text = re.sub(r"^[A-Za-z][\w .'-]{0,24}:\s*", "", text)
    text = re.sub(r"[*_`#]+", "", text)
    # Keep the first sentence or two; a wall of text is not a spoken aside.
    parts = re.split(r"(?<=[.!?])\s+", text)
    text = " ".join(parts[:2]).strip()
    if len(text) > SPOKEN_MAX_CHARS:
        text = text[:SPOKEN_MAX_CHARS].rsplit(" ", 1)[0].rstrip(",.;:-") + "."
    return text


def _words(text: str, drop: str = "") -> list[str]:
    """Lowercased words, minus the agent's name.

    The name appears in nearly every line, so leaving it in inflates the
    overlap between two otherwise unrelated sentences and gets good lines
    rejected -- which lands straight on the shallower fallback pools.
    """
    lowered = text.lower()
    for piece in re.split(r"[-_\s]+", drop.lower()):
        if len(piece) > 2:
            lowered = lowered.replace(piece, " ")
    return re.findall(r"[a-z']+", lowered)


def _tokens(text: str, drop: str = "") -> set[str]:
    return {word for word in _words(text, drop) if len(word) > 3}


def _shape(text: str, drop: str = "") -> set[tuple[str, str]]:
    """Adjacent word pairs -- the frame of a sentence, not its subject.

    Vocabulary overlap alone misses a model that has settled on one frame and
    only swaps the nouns, which is the way generated narration usually starts
    sounding canned.
    """
    words = _words(text, drop)
    return set(zip(words, words[1:]))


def _too_similar(candidate: str, recent: list[str], agent: str = "") -> bool:
    """Whether a candidate restates -- or merely re-uses the frame of -- a recent line."""
    new_tokens = _tokens(candidate, agent)
    new_shape = _shape(candidate, agent)
    if not new_tokens:
        return True
    for line in recent:
        old_tokens = _tokens(line, agent)
        if (
            old_tokens
            and len(new_tokens & old_tokens) / min(len(new_tokens), len(old_tokens))
            >= _REPEAT_RATIO
        ):
            return True
        old_shape = _shape(line, agent)
        if (
            old_shape
            and new_shape
            and len(new_shape & old_shape) / min(len(new_shape), len(old_shape)) >= _SHAPE_RATIO
        ):
            return True
    return False


class Narrator:
    """Writes the spoken line for a Moment, or degrades to a varied fallback."""

    def __init__(
        self,
        persona_name: str,
        personality: str = "",
        *,
        host: str | None = None,
        model: str | None = None,
        timeout_secs: float | None = None,
        enabled: bool | None = None,
    ):
        """Initialize the narrator.

        Args:
            persona_name: Display name of the character speaking.
            personality: The persona's character prompt, trimmed for flavour.
            host: Ollama host; defaults to the configured one.
            model: Generation model; defaults to the resident classifier model.
            timeout_secs: Hard deadline before falling back to a phrase pool.
            enabled: Force generation on/off. Defaults to OFF -- see
                :meth:`enable`.
        """
        # An explicit host/model always wins over a role assignment: a caller
        # that names a specific box (tests pointing at a dead address, an
        # embedder wiring in its own model) means exactly that box, not
        # whatever the operator has assigned to the narration role.
        self._host_overridden = host is not None or model is not None
        self._host = (host or cfg.OLLAMA_HOST).rstrip("/")
        self._model = model or cfg.NARRATION_MODEL
        self._timeout = cfg.NARRATION_TIMEOUT_SECS if timeout_secs is None else timeout_secs
        self._enabled = False if enabled is None else bool(enabled)
        self._persona_name = persona_name
        self._flavour = self._trim_flavour(personality)
        self._recent: deque[str] = deque(maxlen=RECENT_MEMORY)
        # Per kind, so a short pool can't be "used up" by an unrelated kind
        # and reset straight into repeating itself.
        self._pool_history: dict[str, deque[str]] = {}
        # key -> (what it was written about, the line). The signature is what
        # stops a line written for one situation being spoken about another.
        self._prefetched: dict[str, tuple[tuple[str, str], str]] = {}
        self._rng = random.Random()

    @staticmethod
    def _trim_flavour(personality: str) -> str:
        """One short clause of character, not the whole persona prompt.

        The full prompt is hundreds of tokens aimed at a chat model; feeding
        it to a 3B for a one-line aside mostly buys latency.
        """
        text = " ".join(str(personality or "").split())
        if not text:
            return ""
        first = re.split(r"(?<=[.!?])\s+", text)[0]
        return f"{first[:180].rstrip()} "

    def enable(self, value: bool = True) -> None:
        """Switch generation on for a live session, or off.

        A narrator starts inert so that merely *constructing* a session --
        which tests do constantly -- never reaches for the network. The
        running session turns it on at startup, alongside the other warmups,
        by which point the classifier model it shares is already resident.
        """
        self._enabled = bool(value) and cfg.NARRATION_ENABLED

    def set_persona(self, persona_name: str, personality: str = "") -> None:
        """Switch the character the narration is written in."""
        if persona_name == self._persona_name:
            return
        self._persona_name = persona_name
        self._flavour = self._trim_flavour(personality)
        # A new character should not inherit the old one's phrasings.
        self._recent.clear()
        self._prefetched.clear()

    def remember(self, line: str) -> None:
        """Record a line as spoken so the next one is written differently."""
        if line:
            self._recent.append(line)

    def fallback(self, moment: Moment) -> str:
        """A varied stock line, for when generation misses its deadline."""
        pools = _FALLBACKS.get(moment.kind) or _FALLBACKS[KIND_WORKING]
        detail = moment.detail.strip().rstrip(".")
        pool = pools["detail"] if detail and "detail" in pools else pools["plain"]
        history = self._pool_history.setdefault(moment.kind, deque(maxlen=len(pool) - 1))
        unused = [line for line in pool if line not in history] or list(pool)
        template = self._rng.choice(unused)
        history.append(template)
        return _clean_line(
            template.format(
                agent=moment.agent or "The agent",
                task=moment.task or "the task",
                detail=detail,
                note=moment.note or "the other one",
            )
        )

    @staticmethod
    def _signature(moment: Moment) -> tuple[str, str]:
        """What a line was written about, so it isn't spoken about something else."""
        return moment.kind, moment.detail.strip()

    async def prefetch(self, key: str, moment: Moment) -> None:
        """Write a line ahead of time so speaking it later costs nothing.

        Progress narration is the case that matters: a running job has dead
        time, and spending it here means the audio path never waits.
        """
        if not self._enabled:
            return
        existing = self._prefetched.get(key)
        if existing and existing[0] == self._signature(moment):
            return
        line = await self._generate(moment)
        if line:
            self._prefetched[key] = (self._signature(moment), line)

    def take_prefetched(self, key: str, moment: Moment | None = None) -> str | None:
        """Consume a prefetched line, but only if it was written about *this*.

        A job that was quietly working when the line was written may be
        waiting on the user by the time it is spoken; saying the old line then
        is how a stray "still working" ends up answering a question nobody
        understood being asked.
        """
        entry = self._prefetched.get(key)
        if entry is None:
            return None
        if moment is not None and entry[0] != self._signature(moment):
            return None
        del self._prefetched[key]
        return entry[1]

    def drop_prefetched(self, key: str) -> None:
        """Discard a prefetched line that is no longer true."""
        self._prefetched.pop(key, None)

    async def line(self, moment: Moment, *, key: str | None = None) -> str:
        """The spoken line for this moment: prefetched, generated, or stock."""
        recent = list(self._recent)
        if key:
            ready = self.take_prefetched(key, moment)
            if ready and not _too_similar(ready, recent, moment.agent):
                self.remember(ready)
                return ready
        generated = await self._generate(moment) if self._enabled else ""
        if generated and not _too_similar(generated, recent, moment.agent):
            self.remember(generated)
            return generated
        spoken = self.fallback(moment)
        self.remember(spoken)
        return spoken

    def _facts(self, moment: Moment) -> str:
        rows = [_INTENT.get(moment.kind, _INTENT[KIND_WORKING])]
        if moment.agent:
            rows.append(f"HELPER: {moment.agent}")
        if moment.task:
            rows.append(f"TASK: {moment.task[:160]}")
        if moment.detail:
            rows.append(f"RIGHT NOW: {moment.detail[:200]}")
        if moment.note:
            rows.append(f"ALSO: {moment.note[:120]}")
        if moment.elapsed_secs >= 60:
            rows.append(f"RUNNING FOR: {int(moment.elapsed_secs // 60)} minutes")
        if self._recent:
            recent = "; ".join(list(self._recent)[-PROMPT_RECENT:])
            rows.append(f"RECENT (say it differently): {recent}")
        return "\n".join(rows)

    async def _generate(self, moment: Moment) -> str:
        """One short completion, or "" on any miss.

        Resolves through the narration role assignment when one exists and
        no explicit host/model was given to this narrator; otherwise this is
        exactly today's behavior: the small resident classifier model, over
        Ollama's native chat API.
        """
        if not self._host_overridden:
            endpoint = llm_endpoint.role_endpoint(llm_endpoint.NARRATION)
            if endpoint is not None:
                return await self._generate_via_endpoint(endpoint, moment)
        return await self._generate_local(moment)

    def _system_and_user_messages(self, moment: Moment) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": _SYSTEM.format(persona=self._persona_name, flavour=self._flavour),
            },
            {"role": "user", "content": self._facts(moment)},
        ]

    async def _generate_local(self, moment: Moment) -> str:
        """One short completion on the small resident model, or "" on any miss."""
        payload = {
            "model": self._model,
            "messages": self._system_and_user_messages(moment),
            "stream": False,
            # Same VRAM discipline as the classifier: a small context so this
            # model stays resident beside the voice model instead of forcing
            # Ollama to evict one of them every turn.
            "options": {
                "temperature": 0.9,
                "top_p": 0.95,
                "num_predict": 48,
                "num_ctx": 2048,
            },
            "think": False,
            "keep_alive": cfg.NARRATION_KEEP_ALIVE,
        }
        timeout = aiohttp.ClientTimeout(total=self._timeout)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as http:
                async with http.post(f"{self._host}/api/chat", json=payload) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
            return _clean_line(data["message"]["content"])
        except (TimeoutError, aiohttp.ClientError, KeyError, ValueError) as e:
            logger.debug(f"Narration fell back to a stock line: {e}")
            return ""

    async def _generate_via_endpoint(self, endpoint: llm_endpoint.Endpoint, moment: Moment) -> str:
        """One short OpenAI-compatible completion against an assigned role endpoint."""
        payload = {
            "model": endpoint.model,
            "messages": self._system_and_user_messages(moment),
            "max_tokens": 48,
            "temperature": 0.9,
            "top_p": 0.95,
        }
        timeout = aiohttp.ClientTimeout(total=self._timeout)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as http:
                async with http.post(
                    endpoint.chat_url, json=payload, headers=endpoint.headers
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
            line = _clean_line(data["choices"][0]["message"]["content"])
            if line:
                llm_endpoint.record_answer(llm_endpoint.NARRATION, endpoint)
            return line
        except (TimeoutError, aiohttp.ClientError, KeyError, IndexError, ValueError) as e:
            logger.debug(f"Narration fell back to a stock line: {e}")
            return ""
