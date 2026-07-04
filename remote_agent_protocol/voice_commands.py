"""Voice command parsing -- deterministic delegation triggers.

Why this exists: the LLM will happily *say* "sure, I'll send that to Hermes"
while dispatching absolutely nothing (see gui_boot.log, 17:27, the Great File
That Never Was). Task dispatch must be code, not vibes. This module spots
imperative delegation phrases in the user's transcript BEFORE the LLM sees
them, so the session can actually start the job.

Pure functions only -- no pipecat imports, fully unit-testable.
"""

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
_VERBS = ("ask", "tell", "have", "get")

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
# Singular forms only -- matching strips a trailing "s" from each word, so
# "repositories"/"files"/"downloads" all land. (Lesson learned the hard way:
# "Find trending GitHub repositories" sailed straight past the old list and
# Jess INVENTED five repos. See gui_boot.log 18:29. Never again.)
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
    "app",
    "application",
    "program",
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
    "did you write the file yet?" -> None.
    """
    lowered = _strip_fillers(text.strip().lower().rstrip(_TRAILING_PUNCTUATION))
    if not lowered:
        return None
    if lowered.split(" ", 1)[0] in _QUESTION_STARTS:
        return None

    padded = f" {lowered.replace(',', ' ').replace('.', ' ')} "
    if any(f" {phrase} " in padded for phrase in _STANDALONE_VERBS):
        return lowered

    words: set[str] = set()
    for w in padded.split():
        words.add(w)  # raw form -- keeps irregulars like "news" intact
        if w.endswith("s"):
            words.add(w[:-1])  # naive singular -- catches files/repos/etc.
    if words & set(_ACTION_VERBS) and words & set(_TASK_KEYWORDS):
        return lowered
    return None


def requires_confirmation(
    backend: str,
    task: str,
    *,
    elevated_markers: tuple[str, ...] = (),
    destructive_words: tuple[str, ...] = (),
) -> bool:
    """Whether an auto-parsed delegation must be confirmed before it runs.

    Two independent triggers, either of which demands a human in the loop:
      * an *elevated* backend (name contains an ``elevated_markers`` token, e.g.
        ``hermes-yolo`` which auto-approves shell/file writes), or
      * a *destructive* task (its text contains a ``destructive_words`` verb like
        "delete"/"format"/"uninstall").

    Deliberately conservative: a false "needs confirming" costs one spoken
    sentence; a false "just do it" can wipe a folder.
    """
    lowered_backend = (backend or "").lower()
    if any(marker in lowered_backend for marker in elevated_markers):
        return True
    lowered_task = f" {(task or '').lower()} "
    return any(word.strip() and word in lowered_task for word in destructive_words)


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
    words = set(_WORD_RE.findall(lowered))
    if words & _DENY_WORDS or any(phrase in lowered for phrase in _DENY_PHRASES):
        return "deny"
    if words & _APPROVE_WORDS or any(phrase in lowered for phrase in _APPROVE_PHRASES):
        return "approve"
    return None
