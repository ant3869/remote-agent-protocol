"""Intent routing -- decide when a user utterance needs the tool agent.

Why this exists: keyword lists (voice_commands.py) are precise but can never
cover every phrasing, and the persona LLM cannot be trusted to volunteer a
``[[delegate: ...]]`` marker on its own. This module adds a semantic tier: a
dedicated, schema-constrained classification call to the local Ollama server
that judges the user's *intent*, not their wording.

Routing tiers, cheapest first -- the classifier is the net under the fast
paths, not a toll on every turn:

1. **explicit** -- "tell code puppy to X" (``voice_commands.parse_delegation``).
   Deterministic, free, always wins.
2. **gate** -- pure acknowledgments ("thank you", "okay cool") are chat by
   construction (``voice_commands.is_smalltalk``); no classifier call.
3. **noise** -- likely STT/VAD hallucination: a known stock phrase these
   models recite out of silence or room noise, or filler-stripping leaving no
   words at all (``voice_commands.looks_like_stt_noise``). Never a real
   request, so it never reaches a parser or the classifier.
4. **capability** -- vague references to a named-but-forgotten package/skill/
   tool (``voice_commands.parse_capability_request``). The one class of
   request a task rewrite reliably destroys, so the utterance ships verbatim
   inside an identify-then-install task, held for spoken confirmation.
5. **heuristic** -- the keyword net (``voice_commands.parse_implicit_task``).
   High precision, free; when it fires, dispatch immediately.
6. **semantic** -- the LLM intent classifier in this module, when enabled.
   Only utterances that reach this tier pay for it; on timeout or error the
   turn degrades to chat (the free tiers above have already had their say).
   A classifier verdict is further required to be *grounded*: its task/reason
   must share at least one real word with the utterance, or the decision is
   held for confirmation rather than trusted outright (see ``_grounding_gap``).

Downstream of all tiers, the persona's ``[[delegate: ...]]`` marker and the
broken-promise correction (session_processors) remain the last line of defense.

Every ``route()`` call returns a :class:`RoutingDecision` recording what was
decided, by which tier, with what confidence and risk, and why. The session
logs it, emits it to the GUI as a ``"routing"`` event, and keeps it in a
diagnostics ring buffer.
"""

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import aiohttp
from loguru import logger

from remote_agent_protocol import config as cfg
from remote_agent_protocol import voice_commands

# Capability taxonomy. Read-only categories dispatch even at middling
# confidence (a wrong lookup is harmless); mutating ones drop to the
# confirmation gate instead (a wrong file write is not).
READ_ONLY_CATEGORIES = ("live_information", "web_research", "places_or_navigation")
MUTATING_CATEGORIES = ("files_or_apps", "system_control", "communication", "other_action")
CATEGORIES = READ_ONLY_CATEGORIES + MUTATING_CATEGORIES

ACTION_DISPATCH = "dispatch"
ACTION_CONFIRM = "confirm"
ACTION_NONE = "none"

# Risk classification -- independent of action, so logs and the GUI can always
# explain WHY a decision needed confirming, even when the reason isn't a
# destructive verb. Precedence when several apply: destructive first (it's
# the one a wrong guess can't undo), then low_grounding, then ambiguous.
RISK_SAFE = "safe"
RISK_DESTRUCTIVE = "destructive"
RISK_LOW_GROUNDING = "low_grounding"
RISK_AMBIGUOUS = "ambiguous"

_CLASSIFIER_SYSTEM = """You route utterances for a voice assistant. The assistant persona can only \
talk: it has NO internet, NO files, NO apps, NO sensors, and its knowledge is \
frozen in the past. A separate tool agent on the user's computer does \
real-world work for it.

Classify ONE spoken utterance. Judge the user's GOAL, not their wording -- \
indirect, conversational, or oddly phrased requests count when only the tool \
agent could satisfy them.

intent:
- agent_task: the user wants information the persona cannot know (anything \
current, local, changing, or that must be looked up) or an action performed \
on a computer or in the world.
- chat: conversation, opinions, jokes, stories, timeless general knowledge, \
questions about the assistant itself, or remarks about work already done.

category (agent_task only, else "none"):
- live_information: weather, news, prices, scores, events, any current fact
- web_research: look something up, compare options, find out about a topic
- places_or_navigation: places, directions, distances, traffic, hours
- files_or_apps: create/read/edit files, open or control programs
- system_control: settings, devices, processes, installs
- communication: send a message, email, or notification
- other_action: any other real-world action

task: agent_task only -- ONE imperative instruction for the tool agent, \
keeping every concrete detail (names, places, quantities, timeframes). If \
the utterance mixes chat with a request, put only the request here. If the \
user describes a tool, package, or skill they cannot name ("there's a \
package that does X, I forgot the name"), the task is to IDENTIFY the likely \
candidate, check whether it is installed, and set it up -- keep their \
description and their uncertainty; NEVER flatten it into performing the end \
task itself.

confidence: 0.0-1.0 that intent is correct.
reason: one short sentence, using only words present in the utterance or its
plain meaning -- never invent a topic the utterance didn't raise.

These examples show the PATTERN, not text to reuse. Never copy an example's
task/confidence/reason for a different utterance -- if what you're given
doesn't clearly match one, write your own answer grounded in what was
actually said. If the utterance is silence, noise, a stray filler word, or
otherwise unintelligible, that is chat with confidence 0.1 or lower -- never
invent a task to fill the gap.

Examples:
"I wonder if I'll need an umbrella tomorrow" -> {"intent":"agent_task","category":"live_information","task":"Get tomorrow's rain forecast for the user's location","confidence":0.9,"reason":"Needs live weather data"}
"you know how files pile up in downloads... can something be done about that" -> {"intent":"agent_task","category":"files_or_apps","task":"Organize and clean up the user's downloads folder","confidence":0.8,"reason":"Describes a file cleanup goal indirectly"}
"tell me a story about a dragon" -> {"intent":"chat","category":"none","task":"","confidence":0.95,"reason":"Creative conversation"}
"did you finish that report thing" -> {"intent":"chat","category":"none","task":"","confidence":0.7,"reason":"Asks about prior work, not a new task"}
"there's some plugin that transcribes podcasts, no clue what it's called, can you set it up" -> {"intent":"agent_task","category":"system_control","task":"Identify the plugin the user means -- one that transcribes podcasts, exact name unknown to them -- check whether it is installed, and set it up if missing","confidence":0.85,"reason":"Install a capability described but not named"}
"um, uh, static, static, nothing really" -> {"intent":"chat","category":"none","task":"","confidence":0.1,"reason":"Unintelligible or noise-like audio, no discernible request"}
Respond with JSON only."""

# Ollama structured output: constraining generation to this schema guarantees
# parseable JSON even from small models.
_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["agent_task", "chat"]},
        "category": {"type": "string", "enum": [*CATEGORIES, "none"]},
        "task": {"type": "string"},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["intent", "category", "task", "confidence", "reason"],
}

_WARMUP_TIMEOUT_SECS = 30.0
_CODING_TASK_RE = re.compile(
    # Deliberately excludes the bare word "code": "validation code", "promo
    # code", "zip/area code" are not coding tasks and must not steal a job from
    # the default agent. Match real software-engineering signal instead.
    r"\b(?:bug|codebase|source code|code review|coding|module|python|"
    r"javascript|typescript|refactor|unit tests?|test suite|api client|"
    r"pull request|merge conflict|stack trace|compile|repo(?:sitory)?)\b",
    re.IGNORECASE,
)

# Mirrors the "Examples:" block in _CLASSIFIER_SYSTEM -- kept as data (not
# parsed from the prompt string) so a leaked example can be recognized
# programmatically. See _example_echo: jess_runtime.log 2026-07-05 12:57 and
# 2026-07-06 01:02 both show the small local classifier reciting one of these
# completions verbatim (exact task/confidence/reason) for utterances that had
# nothing to do with weather or downloads -- its fallback when an utterance
# gives it too weak a signal to classify (often a VAD/STT misfire on noise or
# silence) is to regurgitate a memorized example instead of a low-confidence
# verdict.
_EXAMPLES: tuple[tuple[str, dict], ...] = (
    (
        "I wonder if I'll need an umbrella tomorrow",
        {
            "intent": "agent_task",
            "category": "live_information",
            "task": "Get tomorrow's rain forecast for the user's location",
            "confidence": 0.9,
            "reason": "Needs live weather data",
        },
    ),
    (
        "you know how files pile up in downloads... can something be done about that",
        {
            "intent": "agent_task",
            "category": "files_or_apps",
            "task": "Organize and clean up the user's downloads folder",
            "confidence": 0.8,
            "reason": "Describes a file cleanup goal indirectly",
        },
    ),
    (
        "tell me a story about a dragon",
        {
            "intent": "chat",
            "category": "none",
            "task": "",
            "confidence": 0.95,
            "reason": "Creative conversation",
        },
    ),
    (
        "did you finish that report thing",
        {
            "intent": "chat",
            "category": "none",
            "task": "",
            "confidence": 0.7,
            "reason": "Asks about prior work, not a new task",
        },
    ),
    (
        "there's some plugin that transcribes podcasts, no clue what it's "
        "called, can you set it up",
        {
            "intent": "agent_task",
            "category": "system_control",
            "task": (
                "Identify the plugin the user means -- one that transcribes "
                "podcasts, exact name unknown to them -- check whether it is "
                "installed, and set it up if missing"
            ),
            "confidence": 0.85,
            "reason": "Install a capability described but not named",
        },
    ),
    (
        "um, uh, static, static, nothing really",
        {
            "intent": "chat",
            "category": "none",
            "task": "",
            "confidence": 0.1,
            "reason": "Unintelligible or noise-like audio, no discernible request",
        },
    ),
)

# Common short words filtered out of the grounding check below so they can't
# create a false "this utterance resembles the example" match on their own.
_STOPWORDS = {
    "that",
    "this",
    "with",
    "from",
    "have",
    "there",
    "about",
    "what",
    "when",
    "where",
    "does",
    "user",
    "users",
    "your",
    "their",
    "been",
    "just",
    "want",
    "wants",
    "need",
    "needs",
    "make",
    "sure",
    "tell",
    "real",
    "world",
    "please",
    "could",
    "would",
    "should",
    "into",
    "some",
    "computer",
    "agent",
    "task",
    "action",
}


def _content_words(text: str) -> set[str]:
    return {
        w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 4 and w not in _STOPWORDS
    }


def _example_echo(verdict: dict, text: str) -> str | None:
    """Detect a few-shot example's completion recited for an unrelated utterance.

    Matches on an exact ``task`` or ``reason`` string (the model doesn't
    partially misremember an example, it reproduces a whole field verbatim),
    then checks whether the live utterance shares any real word with the
    example's OWN trigger phrase. If it does, the model is plausibly
    classifying a genuinely similar request and the verdict is kept. If it
    doesn't, the completion has nothing to do with what was said -- return
    the leaked example's utterance so the caller can discard the verdict.
    """
    incoming_words = _content_words(text)
    for example_text, example_verdict in _EXAMPLES:
        same_task = bool(verdict["task"]) and verdict["task"] == example_verdict["task"]
        same_reason = bool(verdict["reason"]) and verdict["reason"] == example_verdict["reason"]
        if not (same_task or same_reason):
            continue
        if incoming_words & _content_words(example_text):
            continue
        return example_text
    return None


def _grounding_gap(task: str, reason: str, text: str) -> str | None:
    """Detect a classifier task/reason with no real connection to the utterance.

    A weaker, general-purpose companion to :func:`_example_echo`: even when
    the model isn't reciting a memorized example verbatim, it can still
    invent a task unrelated to what was actually said (a noisy, garbled, or
    ambiguous transcript is the usual trigger). Only fires when the task or
    reason actually contains a content word to compare against -- a verdict
    too terse to judge (e.g. a bare "do it") gets the benefit of the doubt
    rather than a false positive, since there's nothing there to contradict
    the utterance.
    """
    verdict_words = _content_words(task) | _content_words(reason)
    if not verdict_words:
        return None
    if verdict_words & _content_words(text):
        return None
    return "classifier task/reason shares no word with the transcript"


# Pronouns whose referent lives in the prior conversation, not the utterance.
_ANAPHORA_WORDS = frozenset(
    {"it", "its", "it's", "that", "this", "they", "them", "those", "these", "one"}
)


def _is_anaphoric(text: str) -> bool:
    """True when the utterance leans on a pronoun with no antecedent of its own.

    "What is it supposed to do", "how does that work", "is this the one" all
    refer to something established earlier in the conversation. The stateless
    classifier can't see that history, so any task it invents is a guess at the
    referent ("a program called 'it'"). Paired with an ungrounded verdict this
    is a reliable misroute signal: hand the turn back to the LLM, which does
    have the conversation (and any staged agent results) to resolve the pronoun.

    Fires only when the pronoun stands essentially alone -- the utterance names
    no substantive referent of its own. "tidy up those old log files" carries a
    real object ("log files"), so it is a genuine indirect request and is left
    for the usual mutating-task confirmation, not discarded.
    """
    if not (set(re.findall(r"[a-z']+", text.lower())) & _ANAPHORA_WORDS):
        return False
    return len(_content_words(text) - _ANAPHORA_WORDS) <= 1


def _classify_risk(agent: str, task: str, *, grounded: bool, confident: bool) -> str:
    """Classify WHY a decision might need a human in the loop, for logs/GUI.

    Destructive outranks the rest -- a wrong destructive guess is the one that
    can't be undone by asking again. Grounding comes next: an ungrounded task
    is suspect regardless of how confidently it was stated. Only after both
    are clear does plain confidence ambiguity matter.
    """
    if voice_commands.requires_confirmation(
        agent,
        task,
        destructive_words=cfg.AGENT_DESTRUCTIVE_WORDS,
    ):
        return RISK_DESTRUCTIVE
    if not grounded:
        return RISK_LOW_GROUNDING
    if not confident:
        return RISK_AMBIGUOUS
    return RISK_SAFE


@dataclass
class RoutingDecision:
    """One routed utterance: what was decided, by which tier, and why."""

    text: str
    action: str = ACTION_NONE  # dispatch | confirm | none
    intent: str = "chat"  # agent_task | chat
    category: str = ""  # taxonomy value, or "" for chat
    requirement: str = "none"  # required | optional | none
    confidence: float = 0.0
    task: str = ""
    agent: str = ""
    reason: str = ""
    source: str = "none"  # explicit | gate | noise | capability | heuristic | classifier | none
    fallback: str = ""  # "" | disabled | timeout | error | invalid | noise
    grounded: bool = True  # False when a classifier task/reason doesn't match the transcript
    risk: str = RISK_SAFE  # safe | destructive | low_grounding | ambiguous
    elapsed_ms: int = field(default=0)


async def classify_with_ollama(
    text: str,
    *,
    host: str,
    model: str,
    timeout_secs: float,
) -> dict:
    """One schema-constrained classification call against local Ollama."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _CLASSIFIER_SYSTEM},
            {"role": "user", "content": text},
        ],
        "stream": False,
        "format": _RESPONSE_SCHEMA,
        # num_ctx caps the KV-cache the classifier reserves. Its whole prompt
        # (system contract + one short utterance) plus the 250-token reply is
        # well under 2K tokens, but models default to a much larger context and
        # reserve VRAM to match -- enough that the classifier can no longer sit
        # resident beside the voice model on a 16GB GPU, so Ollama reloads one
        # each turn and the classifier times out. Capping it shrinks the
        # footprint (qwen2.5:3b: 3.1GB -> 2.3GB loaded) so both stay resident.
        "options": {"temperature": 0, "num_predict": 250, "num_ctx": 2048},
        # Disable the hidden reasoning monologue. A thinking-capable classifier
        # model otherwise spends the whole num_predict budget in its "thinking"
        # channel and returns empty content (done_reason=length), so json.loads
        # fails and every turn silently degrades to chat. Ollama accepts this
        # flag as a no-op on non-thinking models, so it is always safe to send.
        "think": False,
        # Keep the (small) classifier model resident between turns; a cold
        # reload would eat the whole latency budget.
        "keep_alive": "30m",
    }
    timeout = aiohttp.ClientTimeout(total=timeout_secs)
    async with aiohttp.ClientSession(timeout=timeout) as http:
        async with http.post(f"{host}/api/chat", json=payload) as resp:
            resp.raise_for_status()
            data = await resp.json()
    return json.loads(data["message"]["content"])


def _normalize_verdict(raw: object) -> dict | None:
    """Validate and clamp a raw classifier reply; None if unusable."""
    if not isinstance(raw, dict):
        return None
    intent = raw.get("intent")
    if intent not in {"agent_task", "chat"}:
        return None
    try:
        confidence = min(max(float(raw.get("confidence", 0.0)), 0.0), 1.0)
    except (TypeError, ValueError):
        return None
    category = raw.get("category")
    if category not in CATEGORIES:
        category = "other_action" if intent == "agent_task" else "none"
    return {
        "intent": intent,
        "category": category,
        "task": str(raw.get("task") or "").strip(),
        "confidence": confidence,
        "reason": str(raw.get("reason") or "").strip()[:200],
    }


def _clean_task(task: str, utterance: str) -> str:
    """Prefer the classifier's rewritten task, but never a parroted placeholder."""
    stripped = task.strip(" .!?'\"")
    placeholders = {p.lower() for p in cfg.DELEGATION_PLACEHOLDER_TASKS}
    if not stripped or stripped.lower() in placeholders:
        return utterance
    return task


def _select_backend(task: str, default_backend: str, category: str) -> str:
    """Prefer the configured coding agent for concrete codebase work."""
    if (
        category in {"files_or_apps", "other_action"}
        and "code-puppy" in cfg.AGENT_BACKENDS
        and _CODING_TASK_RE.search(task)
    ):
        return "code-puppy"
    return default_backend


class IntentRouter:
    """Tiered utterance router; every decision is fully explained."""

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        classify: Callable[[str], Awaitable[dict]] | None = None,
        timeout_secs: float | None = None,
        dispatch_confidence: float | None = None,
        confirm_confidence: float | None = None,
        auto_delegate: bool | None = None,
    ):
        """Initialize the router.

        Args:
            enabled: Overrides ``INTENT_ROUTER_ENABLED`` (the semantic tier).
            classify: ``async (text) -> raw verdict dict``; injectable for
                tests, defaults to :func:`classify_with_ollama`.
            timeout_secs: Classifier budget before falling back to keywords.
            dispatch_confidence: Confidence at/above which a task dispatches.
            confirm_confidence: Floor of the uncertain band; inside the band
                read-only tasks dispatch and mutating tasks ask first.
            auto_delegate: Overrides ``AGENT_AUTO_DELEGATE`` (heuristic tier).
        """
        self._enabled = cfg.INTENT_ROUTER_ENABLED if enabled is None else enabled
        self._timeout = cfg.INTENT_TIMEOUT_SECS if timeout_secs is None else timeout_secs
        self._dispatch_conf = (
            cfg.INTENT_DISPATCH_CONFIDENCE if dispatch_confidence is None else dispatch_confidence
        )
        self._confirm_conf = (
            cfg.INTENT_CONFIRM_CONFIDENCE if confirm_confidence is None else confirm_confidence
        )
        self._auto_delegate = cfg.AGENT_AUTO_DELEGATE if auto_delegate is None else auto_delegate
        self._uses_default_classifier = classify is None
        self._classify = classify or self._default_classify

    @staticmethod
    async def _default_classify(text: str) -> dict:
        return await classify_with_ollama(
            text,
            host=cfg.OLLAMA_HOST,
            model=cfg.INTENT_MODEL,
            timeout_secs=cfg.INTENT_TIMEOUT_SECS,
        )

    async def warmup(self) -> None:
        """Preload the classifier model so the first real turn pays nothing.

        Fire-and-forget at session start. Cold model loading gets a larger
        budget than live turns so it can actually make the classifier resident.
        """
        if not self._enabled:
            return
        try:
            if self._uses_default_classifier:
                await classify_with_ollama(
                    "hello",
                    host=cfg.OLLAMA_HOST,
                    model=cfg.INTENT_MODEL,
                    timeout_secs=max(_WARMUP_TIMEOUT_SECS, self._timeout),
                )
            else:
                await self._classify("hello")
            logger.info("Intent classifier warmed up")
        except Exception as exc:
            logger.warning(f"Intent classifier warmup failed: {exc}")

    async def route(self, text: str, default_backend: str) -> RoutingDecision:
        """Decide what to do with one user utterance, with full provenance."""
        t0 = time.perf_counter()
        decision = await self._route(text, default_backend)
        decision.elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return decision

    async def _route(self, text: str, default_backend: str) -> RoutingDecision:
        # Tier 1: the user named the agent -- deterministic, always wins.
        parsed = voice_commands.parse_delegation(text, cfg.AGENT_BACKENDS, cfg.AGENT_SPOKEN_ALIASES)
        if parsed is not None:
            backend, task = parsed
            return RoutingDecision(
                text=text,
                action=ACTION_DISPATCH,
                intent="agent_task",
                category="other_action",
                requirement="required",
                confidence=1.0,
                task=task,
                agent=backend,
                reason="user addressed the agent by name",
                source="explicit",
                risk=_classify_risk(backend, task, grounded=True, confident=True),
            )

        # Tier 2: pure acknowledgments are chat by construction -- the most
        # common voice turns ("thank you", "okay") never pay for a classifier.
        if voice_commands.is_smalltalk(text):
            return RoutingDecision(
                text=text,
                confidence=1.0,
                reason="short acknowledgment or reaction",
                source="gate",
            )

        # Tier 3: likely STT/VAD noise -- a stock hallucinated phrase these
        # models recite out of silence/room noise, or a clipped/interrupted
        # turn that left no real words at all. Never a real request, so it's
        # rejected before it can reach a parser or the classifier budget.
        if voice_commands.looks_like_stt_noise(text):
            logger.info(f"Routing[noise] rejecting likely STT/VAD hallucination: {text!r}")
            return RoutingDecision(
                text=text,
                reason="looks like STT/VAD noise or hallucination, not a real request",
                source="noise",
                fallback="noise",
            )

        # Tier 4: a vague reference to a named capability ("there's a package
        # for X, I forgot the name, make sure we have it"). Any rewrite loses
        # the point -- the user means a specific installable thing -- so the
        # utterance ships verbatim inside an identify-then-install task.
        # Always held for confirmation: the referent is uncertain by
        # construction, and a wrong install mutates the system. Not gated by
        # auto_delegate for the same reason -- it never runs without a spoken
        # yes.
        described = voice_commands.parse_capability_request(text)
        if described is not None:
            capability_task = cfg.VAGUE_CAPABILITY_TASK_TEMPLATE.format(utterance=described)
            return RoutingDecision(
                text=text,
                action=ACTION_CONFIRM,
                intent="agent_task",
                category="system_control",
                requirement="required",
                confidence=0.9,
                task=capability_task,
                agent=default_backend,
                reason="user described a package/skill/tool without its exact name",
                source="capability",
                risk=_classify_risk(
                    default_backend, capability_task, grounded=True, confident=True
                ),
            )

        # Tier 5: the keyword net -- free and high-precision; when it fires,
        # dispatch without spending the classifier budget.
        task = voice_commands.parse_implicit_task(text) if self._auto_delegate else None
        if task is not None:
            agent = _select_backend(task, default_backend, "other_action")
            return RoutingDecision(
                text=text,
                action=ACTION_DISPATCH,
                intent="agent_task",
                category="other_action",
                requirement="required",
                confidence=0.9,
                task=task,
                agent=agent,
                reason="keyword net matched a real-world request",
                source="heuristic",
                risk=_classify_risk(agent, task, grounded=True, confident=True),
            )

        # Tier 6: semantic classification -- only for utterances the free
        # tiers above could not place.
        verdict: dict | None = None
        fallback = ""
        if not self._enabled:
            fallback = "disabled"
        else:
            try:
                raw = await asyncio.wait_for(self._classify(text), self._timeout + 0.5)
                verdict = _normalize_verdict(raw)
                if verdict is None:
                    fallback = "invalid"
                    logger.warning(f"Intent classifier returned unusable verdict: {raw!r}")
                else:
                    leaked = _example_echo(verdict, text)
                    if leaked is not None:
                        fallback = "invalid"
                        logger.warning(
                            f"Intent classifier echoed the {leaked!r} example verbatim for "
                            f"unrelated utterance {text!r}; discarding verdict {verdict!r}"
                        )
                        verdict = None
            except TimeoutError:
                fallback = "timeout"
                logger.warning(f"Intent classifier timed out after {self._timeout:.1f}s")
            except Exception as exc:
                fallback = "error"
                logger.warning(f"Intent classifier failed ({exc}); treating as chat")

        if (
            verdict is not None
            and verdict["intent"] == "agent_task"
            and verdict["confidence"] >= self._confirm_conf
        ):
            confident = verdict["confidence"] >= self._dispatch_conf
            read_only = verdict["category"] in READ_ONLY_CATEGORIES
            task = _clean_task(verdict["task"], text)
            agent = _select_backend(task, default_backend, verdict["category"])
            ungrounded = _grounding_gap(task, verdict["reason"], text)
            if ungrounded is None:
                return RoutingDecision(
                    text=text,
                    # In the uncertain band: lookups are harmless so dispatch
                    # anyway; anything that could change state asks first.
                    action=ACTION_DISPATCH if (confident or read_only) else ACTION_CONFIRM,
                    intent="agent_task",
                    category=verdict["category"],
                    requirement="required" if confident else "optional",
                    confidence=verdict["confidence"],
                    task=task,
                    agent=agent,
                    reason=verdict["reason"] or "classifier judged this a real-world task",
                    source="classifier",
                    fallback=fallback,
                    grounded=True,
                    risk=_classify_risk(agent, task, grounded=True, confident=confident),
                )
            # Ungrounded: the classifier invented a task with no topical link to
            # what was said. An anaphoric follow-up ("what is it supposed to do",
            # "how does that work") is the clearest case -- the referent lives in
            # the prior conversation the stateless classifier can't see, so the
            # task is a guess at a pronoun ("a program called 'it'"). Defer to
            # the LLM, which has the conversation and any staged agent results.
            if _is_anaphoric(text):
                logger.warning(
                    f"Discarding ungrounded anaphoric follow-up for {text!r}: {task!r} "
                    f"({ungrounded}) -- deferring to the LLM which has conversation context"
                )
                verdict = None
            # A read-only lookup the user never raised (weather, directions,
            # scores -- the small model regurgitating a few-shot example) is pure
            # noise, so discard it silently rather than asking. An ungrounded
            # *mutating* task still gets a confirmation checkpoint: it may be a
            # genuine indirect request ("tidy up those old logs" -> "Delete old
            # logs"), and a state change is worth one question.
            elif read_only:
                logger.warning(
                    f"Discarding ungrounded read-only task for {text!r}: {task!r} ({ungrounded})"
                )
                verdict = None
            else:
                logger.warning(
                    f"Holding ungrounded mutating task for {text!r}: {task!r} ({ungrounded})"
                )
                return RoutingDecision(
                    text=text,
                    action=ACTION_CONFIRM,
                    intent="agent_task",
                    category=verdict["category"],
                    requirement="optional",
                    confidence=verdict["confidence"],
                    task=task,
                    agent=agent,
                    reason=(
                        f"{verdict['reason'] or 'classifier judged this a real-world task'} "
                        f"-- held: {ungrounded}"
                    ),
                    source="classifier",
                    fallback=fallback,
                    grounded=False,
                    risk=_classify_risk(agent, task, grounded=False, confident=confident),
                )

        # Chat: nothing dispatches; the marker + promise guard still watch.
        if verdict is not None:
            reason = verdict["reason"] or "classifier judged this conversation"
            confidence = verdict["confidence"]
            source = "classifier"
        else:
            reason = "no routing tier matched"
            confidence = 0.0
            source = "none"
        return RoutingDecision(
            text=text,
            reason=reason,
            confidence=confidence,
            source=source,
            fallback=fallback,
        )
