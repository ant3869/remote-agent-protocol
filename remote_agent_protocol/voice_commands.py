"""Voice command parsing -- deterministic delegation triggers.

Why this exists: the LLM will happily *say* "sure, I'll send that to Hermes"
while dispatching absolutely nothing (see gui_boot.log, 17:27, the Great File
That Never Was). Task dispatch must be code, not vibes. This module spots
imperative delegation phrases in the user's transcript BEFORE the LLM sees
them, so the session can actually start the job.

Pure functions only -- no pipecat imports, fully unit-testable.
"""

import functools
import re

# Leading fluff we tolerate before the actual command verb.
_FILLERS = (
    "hey jess",
    "jess",
    "okay",
    "ok",
    "so",
    "please",
    "can you",
    "could you",
    "would you",
    "will you",
)

# Imperative verbs that start a delegation. Question forms ("did you ask...",
# "is hermes...") never survive filler-stripping into one of these, which is
# exactly how we avoid false positives on chatter ABOUT agents.
_VERBS = ("ask", "tell", "have", "get", "use")

_TRAILING_PUNCTUATION = ".!?,;: "

# Implicit delegation: no agent named, but the request is clearly "go DO a
# thing in the real world", not chat. Two tiers keep false positives out:
#   * standalone verbs -- inherently imply web/system action, no keyword needed
#   * action verbs     -- only delegate when paired with a real-world keyword
# so "write me a poem" stays with Jess but "write a file on my desktop" ships.
_STANDALONE_VERBS = (
    "search for",
    "search the web",
    "search online",
    "look up",
    "google",
    "browse",
    "download",
    "install",
    "uninstall",
    "back up",
    "backup",
)
_ACTION_VERBS = (
    "write",
    "create",
    "make",
    "save",
    "put",
    "find",
    "check",
    "delete",
    "remove",
    "move",
    "copy",
    "rename",
    "organize",
    "organise",
    "clean",
    "run",
    "execute",
    "open",
    "list",
    "search",
)
# Live-data tier: information that is ALWAYS stale in the model (it has no
# internet), so any request framed around these nouns must delegate -- even
# question forms, which the question guard would otherwise keep as chat.
# ("Give me the storm forecast for Bentonville" got a confidently invented
# forecast, jess_runtime.log 2026-07-04 23:26. Never again.) Past-tense
# openers ("did you check the weather") still stay chat: they ask about
# history, not for data.
_LIVE_DATA_KEYWORDS = (
    "weather",
    "forecast",
    "temperature",
    "humidity",
    "news",
    "headline",
    "price",
    "stock",
    "traffic",
)
_LIVE_REQUEST_WORDS = (
    "give",
    "get",
    "tell",
    "show",
    "check",
    "find",
    "look",
    "fetch",
    "pull",
    "grab",
    "read",
    "update",
    "what",
    "what's",
    "whats",
    "how",
    "how's",
    "hows",
    "when",
    "where",
)
_PAST_QUESTION_STARTS = ("did", "was", "were", "has", "had", "have", "does", "do", "am")

# The persona has no access to anything -- no internet, no files, no accounts
# -- so any question about access ("do you have access to my email?") can
# only be answered by the tool agent checking real state, never by the
# model guessing. Unlike the live-data tier, this ignores _PAST_QUESTION_STARTS
# entirely: "do you have access..." and "have you got access..." both open
# with words in that list, but neither is asking about history.
# (jess_runtime.log 2026-07-06 01:57 -- answered from the model's own head
# instead of checking. Never again.)
_ACCESS_WORDS = ("access", "accessing", "accessed", "accesses")

# Singular forms only -- matching strips a trailing "s" from each word, so
# "repositories"/"files"/"downloads" all land. (Lesson learned the hard way:
# "Find trending GitHub repositories" sailed straight past the old list and
# Jess INVENTED five repos. See gui_boot.log 18:29. Never again.)
# Nouns that mean an installable/enablable capability, not an end task.
_CAPABILITY_NOUNS = (
    "skill",
    "package",
    "plugin",
    "plug-in",
    "tool",
    "library",
    "extension",
    "addon",
    "add-on",
    "module",
    "dependency",
    "mcp",
    "cli",
    "utility",
    "app",
    "application",
    "program",
)
_CAPABILITY_STATE_KEYWORDS = ("installed", "uninstalled", "missing", "enabled", "disabled")

_TASK_KEYWORDS = (
    "file",
    "folder",
    "desktop",
    "drive",
    "disk",
    "email",
    "inbox",
    "mail",
    "web",
    "internet",
    "online",
    "website",
    "browser",
    "download",
    "document",
    "repo",
    "repository",
    "repositorie",
    "github",
    "gitlab",
    "git",
    "code",
    "script",
    *_CAPABILITY_NOUNS,
    "computer",
    "pc",
    "system",
    "schedule",
    "calendar",
    "zip",
    "url",
    "link",
    "site",
    "page",
    "news",
    "weather",
    "trending",
    "stock",
    "price",
    # Live/location lookups -- "find the closest fireworks stand" is a
    # real-world task, not chat, even though nothing filesystem-y is named.
    "direction",
    "map",
    "route",
    "address",
    "location",
    "place",
    "nearby",
    "closest",
    "nearest",
    "store",
    "shop",
    "restaurant",
    "cafe",
    "station",
    "hotel",
    "flight",
    "ticket",
)


def _strip_fillers(lowered: str) -> str:
    changed = True
    while changed:
        changed = False
        for filler in _FILLERS:
            if lowered.startswith(filler + " ") or lowered.startswith(filler + ","):
                lowered = lowered[len(filler) :].lstrip(", ")
                changed = True
    return lowered


def parse_delegation(
    text: str,
    backends: dict,
    aliases: dict[str, str],
) -> tuple[str, str] | None:
    """Return (backend_name, task) if ``text`` is a spoken delegation command.

    Grammar (case-insensitive):
        [filler...] <ask|tell|have|get> <agent-alias> [to] <task>

    Returns None for anything else -- questions about agents, chit-chat,
    unknown agents, or empty tasks all fall through to the normal LLM path.
    """
    lowered = _strip_fillers(text.strip().lower().rstrip(_TRAILING_PUNCTUATION))

    # Direct address is common in speech ("Code Puppy, write the tests"). The
    # comma is deliberate: without it, status chatter such as "Hermes is still
    # working" would become an accidental command.
    for alias in sorted(aliases, key=len, reverse=True):
        prefix = f"{alias},"
        if not lowered.startswith(prefix):
            continue
        backend = aliases[alias]
        task = lowered[len(prefix) :].strip(_TRAILING_PUNCTUATION)
        if re.match(r"^(?:are|is|did|have) you\b", task) and re.search(
            r"\b(?:working|done|finished|complete)\b", task
        ):
            return None
        if backend in backends and task:
            return backend, task
        return None

    verb = next((v for v in _VERBS if lowered.startswith(v + " ")), None)
    if verb is None:
        return None
    rest = lowered[len(verb) :].lstrip()

    # Longest alias first so "hermes yolo" wins over "hermes".
    for alias in sorted(aliases, key=len, reverse=True):
        if rest == alias or rest.startswith(alias + " "):
            backend = aliases[alias]
            if backend not in backends:
                return None
            task = rest[len(alias) :].strip()
            if task == "to":
                task = ""
            elif task.startswith("to "):
                task = task[3:]
            task = task.strip(_TRAILING_PUNCTUATION)
            return (backend, task) if task else None
    return None


_MODEL_PROVIDER_ALIASES = {"openai": "openai", "open ai": "openai"}


def parse_model_switch(text: str, aliases: dict[str, str]) -> tuple[str | None, str, bool] | None:
    """Parse a spoken provider/model switch, optionally naming an agent."""
    lowered = _strip_fillers(text.strip().lower().rstrip(_TRAILING_PUNCTUATION))
    if not re.search(r"\b(?:change|switch|use)\b", lowered):
        return None
    provider = next(
        (
            canonical
            for spoken, canonical in _MODEL_PROVIDER_ALIASES.items()
            if re.search(rf"\b{re.escape(spoken)}\b", lowered)
        ),
        None,
    )
    if provider is None:
        return None
    agent = next(
        (
            aliases[alias]
            for alias in sorted(aliases, key=len, reverse=True)
            if re.search(rf"\b{re.escape(alias)}\b", lowered)
        ),
        None,
    )
    retry = bool(re.search(r"\b(?:retry|rerun|try again|run again)\b", lowered))
    return agent, provider, retry


def is_retry_request(text: str) -> bool:
    """True for a short command to rerun the last recoverable agent task."""
    lowered = _strip_fillers(text.strip().lower().rstrip(_TRAILING_PUNCTUATION))
    return bool(
        re.fullmatch(
            r"(?:retry|rerun|try|run)(?: (?:that|the))?(?: (?:task|request))?(?: again)?",
            lowered,
        )
    )


def parse_agent_control(
    text: str, backends: dict, aliases: dict[str, str]
) -> tuple[str, str | None] | None:
    """Parse local commands that inspect or change agent orchestration."""
    lowered = _strip_fillers(text.strip().lower().rstrip(_TRAILING_PUNCTUATION))
    if lowered in {"list agents", "list my agents", "what agents are available"}:
        return "list", None
    if lowered in {
        "what is my default agent",
        "what's my default agent",
        "which agent is default",
    }:
        return "get_default", None
    match = re.fullmatch(r"(?:make|set|use) (.+?) (?:as )?my default agent", lowered)
    if match:
        backend = aliases.get(match.group(1))
        if backend in backends:
            return "set_default", backend
    return None


def parse_agent_cancel(text: str, aliases: dict[str, str]) -> tuple[str | None, bool] | None:
    """Parse a command to cancel active delegated work, optionally by agent."""
    lowered = _strip_fillers(text.strip().lower().rstrip(_TRAILING_PUNCTUATION))
    if not re.match(r"^(?:cancel|stop|abort|end)\b", lowered):
        return None
    agent = next(
        (
            aliases[alias]
            for alias in sorted(aliases, key=len, reverse=True)
            if re.search(rf"\b{re.escape(alias)}\b", lowered)
        ),
        None,
    )
    if agent is None and not re.search(r"\b(?:agent|job|task)s?\b", lowered):
        return None
    return agent, bool(re.search(r"\b(?:all|every)\b", lowered))


def parse_task_correction(text: str) -> str | None:
    """Return a correction aimed at the newest pending or active agent task."""
    lowered = text.strip().lower().rstrip(_TRAILING_PUNCTUATION)
    prefixes = ("wait", "actually", "instead", "no")
    prefix = next(
        (
            item
            for item in prefixes
            if lowered == item or lowered.startswith((item + " ", item + ","))
        ),
        None,
    )
    if prefix is None:
        return None
    correction = lowered[len(prefix) :].lstrip(", ")
    if prefix == "no" and correction.startswith(("i don't", "i dont", "that's", "thats")):
        return None
    directive_words = ("use", "do", "make", "change", "switch", "instead", "don't", "do not")
    if not correction or not any(word in correction for word in directive_words):
        return None
    return correction


# First words that mean "question / statement about the past", never a command.
# Checked AFTER filler-stripping, so "can you find..." still delegates while
# "did you find..." stays chat.
_QUESTION_STARTS = (
    "did",
    "do",
    "does",
    "is",
    "are",
    "was",
    "were",
    "am",
    "what",
    "what's",
    "whats",
    "when",
    "where",
    "who",
    "why",
    "how",
    "which",
    "has",
    "have",
    "had",
    "should",
    "would",
    "could",
    "will",
)


def parse_implicit_task(text: str) -> str | None:
    """Return the task text if ``text`` is an unaddressed real-world command.

    Verbs and keywords are matched ANYWHERE in the utterance (word-boundary
    safe), because spoken commands come with endless preambles -- "go and find
    me...", "I want you to check..." (the old starts-with approach missed
    "Go and find me the top 5 trending github repos", gui_boot.log 18:39).
    A question-word guard on the FIRST word keeps chat as chat:
    "did you write the file yet?" -> None. Live-data requests are the one
    exception -- "what's the weather?" is a lookup, not a conversation.
    """
    lowered = _strip_fillers(text.strip().lower().rstrip(_TRAILING_PUNCTUATION))
    if not lowered:
        return None
    first = lowered.split(" ", 1)[0]

    # A statement about the user's own future work isn't an instruction to an
    # agent, even when it contains action/task keywords.
    if first in {"i", "i'm", "im"} and "going to" in lowered:
        return None

    padded = f" {lowered.replace(',', ' ').replace('.', ' ')} "
    words: set[str] = set()
    for w in padded.split():
        words.add(w)  # raw form -- keeps irregulars like "news" intact
        if w.endswith("s"):
            words.add(w[:-1])  # naive singular -- catches files/repos/etc.

    # Current machine capability state is real-world data even when phrased as
    # a question ("are any useful skills missing?"). It never belongs to chat.
    if words & set(_CAPABILITY_NOUNS) and words & set(_CAPABILITY_STATE_KEYWORDS):
        return lowered

    if words & set(_ACCESS_WORDS):
        return lowered

    if (
        first not in _PAST_QUESTION_STARTS
        and words & set(_LIVE_DATA_KEYWORDS)
        and words & set(_LIVE_REQUEST_WORDS)
    ):
        return lowered

    if first in _QUESTION_STARTS:
        return None
    if any(f" {phrase} " in padded for phrase in _STANDALONE_VERBS):
        return lowered
    if words & set(_ACTION_VERBS) and words & set(_TASK_KEYWORDS):
        return lowered
    return None


# ---------------------------------------------------------------------------
# Vague capability references -- "there's a package that does X, I don't
# remember what it's called, make sure we have it". The user means a specific
# installable thing they can't name; any rewrite into a plain action task
# destroys exactly that. (The YouTube-skill request became the generic task
# "Enable YouTube video watching on the computer" and the agent went off
# editing this repo for five minutes, jess_runtime.log 2026-07-05 12:35.
# Never again.)
# ---------------------------------------------------------------------------

# Phrases that signal the user cannot produce the exact name.
_NAME_UNCERTAINTY_PHRASES = (
    "don't remember",
    "dont remember",
    "can't remember",
    "cant remember",
    "don't recall",
    "dont recall",
    "can't recall",
    "cant recall",
    "forgot the name",
    "forget the name",
    "forgot its name",
    "forgot what",
    "forget what",
    "what it's called",
    "what its called",
    "what it was called",
    "what's it called",
    "whats it called",
    "not sure what",
    "not sure of the name",
    "no idea what",
    "no clue what",
    "i think it's called",
    "i think its called",
    "might be called",
    "called something like",
)


def parse_capability_request(text: str) -> str | None:
    """Return the utterance verbatim if it names a capability only by description.

    Fires only when BOTH signals are present: a capability noun ("package",
    "skill", "plugin", ...) and a name-uncertainty phrase ("I forgot the
    name", "not sure what it's called", ...). Requests that name the thing
    outright ("install yt-dlp") don't match and take the normal routing path;
    neither does chat that is merely forgetful ("I saw a movie, can't
    remember what it's called").
    """
    stripped = text.strip()
    lowered = _strip_fillers(stripped.lower().rstrip(_TRAILING_PUNCTUATION))
    if not lowered:
        return None
    if not any(phrase in lowered for phrase in _NAME_UNCERTAINTY_PHRASES):
        return None
    words: set[str] = set()
    for w in lowered.replace(",", " ").replace(".", " ").split():
        words.add(w)
        if w.endswith("s"):
            words.add(w[:-1])  # naive singular, same trick as the keyword net
    if words & set(_CAPABILITY_NOUNS):
        return stripped
    return None


# Words that make up pure acknowledgments and reactions. An utterance of up to
# three of these ("thank you", "okay cool", "haha nice") is small talk by
# construction -- the intent router skips its classifier call entirely, which
# keeps the most common voice turns on the fast path.
_SMALLTALK_WORDS = {
    "thanks",
    "thank",
    "you",
    "okay",
    "ok",
    "cool",
    "nice",
    "great",
    "awesome",
    "sweet",
    "perfect",
    "yeah",
    "yep",
    "yes",
    "no",
    "nope",
    "nah",
    "sure",
    "haha",
    "lol",
    "wow",
    "whoa",
    "hi",
    "hello",
    "hey",
    "bye",
    "goodbye",
    "goodnight",
    "morning",
    "huh",
    "what",
    "why",
    "really",
    "seriously",
    "right",
    "alright",
    "fine",
    "good",
    "sorry",
    "oops",
    "hmm",
    "oh",
    "ah",
}


def is_smalltalk(text: str) -> bool:
    """True for short pure acknowledgments/reactions ("thank you", "what?").

    Deliberately conservative: at most three words and EVERY word must be in
    the small-talk lexicon, so "thank you for the forecast" and "okay do the
    thing" never match and still reach the real routing tiers.
    """
    lowered = _strip_fillers(text.strip().lower().rstrip(_TRAILING_PUNCTUATION))
    words = _WORD_RE.findall(lowered)
    return 0 < len(words) <= 3 and all(word in _SMALLTALK_WORDS for word in words)


# Stock phrases Whisper (and similar STT models) are known to hallucinate out
# of silence, room noise, or a clipped/interrupted turn -- captioning-corpus
# artifacts (video outros, subscribe prompts) rather than anything the user
# said. Matched against the FULL utterance (after filler-stripping), never a
# substring, so a genuine "thanks for watching that show with me" is untouched.
_STT_HALLUCINATION_PHRASES = {
    "thank you",
    "thank you for watching",
    "thanks for watching",
    "thank you so much for watching",
    "please subscribe",
    "subscribe",  # "please subscribe" minus the stripped "please" filler
    "don't forget to subscribe",
    "like and subscribe",
    "subscribe to my channel",
    "see you in the next video",
    "see you next time",
    "thanks for listening",
    "bye bye",
    "the end",
    "you",
}


def looks_like_stt_noise(text: str) -> bool:
    """True for a transcript that is very likely STT noise, not real speech.

    Two independent signals, either of which means there was nothing to act
    on: the whole utterance is one of a known set of hallucinated stock
    phrases (STT models recite these out of silence/room noise instead of
    returning nothing), or filler-stripping left no actual words at all (pure
    punctuation/breath sounds). Checked before the utterance ever reaches the
    intent classifier or any delegation parser -- there is no reading of
    "nothing" that should become a task.
    """
    lowered = _strip_fillers(text.strip().lower().rstrip(_TRAILING_PUNCTUATION))
    if not lowered:
        return True
    if lowered in _STT_HALLUCINATION_PHRASES:
        return True
    return not _WORD_RE.findall(lowered)


# Instructional / research framing around a destructive verb. "How to delete an
# account", "search the web for how to uninstall X", "what happens if I wipe the
# disk" are read-only lookups -- the agent researches, it does not destroy -- so
# a destructive word appearing inside one must NOT force a confirmation.
_INFORMATIONAL_LOOKUP_RE = re.compile(
    r"\b(?:how\s+to|how\s+(?:do|can|would|should|might)\s+(?:i|you|we)|"
    r"what\s+(?:happens|would\s+happen)\s+if|ways?\s+to|"
    r"guide\s+(?:to|on|for)|tutorial\s+(?:on|for)|learn\s+(?:how\s+)?to|"
    r"instructions?\s+(?:to|for|on|about))\b",
    re.IGNORECASE,
)


@functools.lru_cache(maxsize=8)
def _destructive_pattern(destructive_words: tuple[str, ...]) -> re.Pattern | None:
    """Compile the destructive vocabulary into one whole-word matcher.

    Matches each verb as a whole word with common inflections
    (delete/deletes/deleted/deleting) so imperative, past, and plural forms all
    trip -- but never a word that merely embeds the stem (skill, storm, dropbox,
    installer). Multi-word entries ("shut down") allow flexible whitespace.
    """
    parts = [re.escape(word.strip().lower()).replace(r"\ ", r"\s+") for word in destructive_words]
    parts = [part for part in parts if part]
    if not parts:
        return None
    return re.compile(r"\b(?:" + "|".join(parts) + r")(?:s|es|ed|d|ing)?\b", re.IGNORECASE)


def requires_confirmation(
    backend: str,
    task: str,
    *,
    destructive_words: tuple[str, ...] = (),
) -> bool:
    """Whether an auto-parsed delegation must be confirmed before it runs.

    Picking a backend IS the risk acknowledgment -- an elevated backend (e.g.
    ``hermes-yolo``, which auto-approves its own shell/file writes) no longer
    forces confirmation on top of that. The one remaining trigger is a
    *destructive* task: its text names a ``destructive_words`` verb like
    "delete"/"format"/"uninstall", regardless of which backend runs it --
    a false "needs confirming" costs one spoken sentence; a false "just do
    it" can wipe a folder.

    Two refinements keep this both safe and un-annoying:

    * Whole-word matching (see :func:`_destructive_pattern`) means a data-loss
      verb like "empty the recycle bin" or "kill the process" now trips, while
      lookalikes ("skill", "installer", "dropbox") no longer produce a spurious
      gate.
    * An *instructional* request about a destructive action ("how to delete an
      account") is a read-only lookup and is not gated -- unless the utterance
      itself opens with the destructive imperative ("delete everything, here's
      how"), which is a real command.
    """
    pattern = _destructive_pattern(tuple(destructive_words))
    if pattern is None:
        return False
    lowered = (task or "").lower().strip()
    if not pattern.search(lowered):
        return False
    first_word = lowered.split(" ", 1)[0] if lowered else ""
    opens_with_destructive = bool(pattern.match(first_word))
    if _INFORMATIONAL_LOOKUP_RE.search(lowered) and not opens_with_destructive:
        return False
    return True


# Short replies that resolve a pending confirmation. Denials are checked FIRST
# and win ties ("okay no" -> deny), because the safe bias is to NOT run.
_DENY_WORDS = {
    "no",
    "nope",
    "cancel",
    "cancelled",
    "canceled",
    "stop",
    "deny",
    "denied",
    "abort",
    "aborted",
}
_DENY_PHRASES = ("don't", "do not", "never mind", "nevermind", "forget it")
_APPROVE_WORDS = {
    "yes",
    "yeah",
    "yep",
    "yup",
    "sure",
    "confirm",
    "confirmed",
    "approve",
    "approved",
    "proceed",
    "okay",
    "ok",
}
_APPROVE_PHRASES = ("do it", "go ahead", "please do", "sounds good", "go for it")
_APPROVE_OVERRIDES = ("no, do it", "no do it", "no, do that", "no do that", "want you to do that")
_WORD_RE = re.compile(r"[a-z']+")


def classify_confirmation_reply(text: str) -> str | None:
    """Classify a short reply to a pending confirmation.

    Returns ``"approve"``, ``"deny"``, or ``None`` when the utterance isn't a
    yes/no at all (so it falls through to Jess as normal conversation). Denials
    are matched before approvals so a hedged "yeah, no, cancel that" stops.
    """
    lowered = text.strip().lower().rstrip(_TRAILING_PUNCTUATION)
    if not lowered:
        return None
    if any(phrase in lowered for phrase in _APPROVE_OVERRIDES):
        return "approve"
    words = set(_WORD_RE.findall(lowered))
    if words & _DENY_WORDS or any(phrase in lowered for phrase in _DENY_PHRASES):
        return "deny"
    if words & _APPROVE_WORDS or any(phrase in lowered for phrase in _APPROVE_PHRASES):
        return "approve"
    return None
