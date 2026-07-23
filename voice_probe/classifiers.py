"""Classifier backends for the tier-6 semantic router, and router assembly.

Three modes, chosen at run time:

* ``live`` -- the real thing: an Ollama call via
  :func:`remote_agent_protocol.intent_router.classify_with_ollama`. This is the
  only mode that truly evaluates the semantic tier, so classifier-dependent
  cases are judged for real only here. Needs Ollama up with ``INTENT_MODEL``.
* ``stub`` -- a deterministic, offline fake classifier. It lets the harness run
  anywhere (CI, no GPU, no Ollama) and fully exercises tiers 1-5 plus the
  scoring/reporting plumbing. Tier-6 verdicts it produces are *synthetic*, so
  classifier-dependent failures are reported as info, not fails.
* ``off`` -- disable the semantic tier entirely (``INTENT_ROUTER_ENABLED``
  off). Everything rides the deterministic tiers; whatever they miss falls to
  chat. Useful for isolating exactly what the keyword net alone can do.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

from remote_agent_protocol import config as cfg
from remote_agent_protocol import intent_router

Classifier = Callable[[str], Awaitable[dict]]

# Signals the stub uses to imitate the real classifier's judgment. Deliberately
# broad and imperfect -- the point is to keep tiers 1-5 exercised offline, not
# to be a second production router.
_LIVE_SIGNAL = re.compile(
    r"\b(weather|forecast|temperature|news|price|stock|score|traffic|time|"
    r"calendar|email|inbox|schedule|directions?|nearby|nearest|closest|open now)\b",
    re.IGNORECASE,
)
_ACTION_SIGNAL = re.compile(
    r"\b(install|uninstall|download|delete|remove|erase|wipe|format|move|copy|"
    r"rename|organize|organise|create|make|write|save|edit|append|overwrite|"
    r"clone|build|run|execute|kill|shutdown|shut down|reboot|drop|disable|"
    r"empty|sort|back ?up|clean|open|list|search|look up|google|browse)\b",
    re.IGNORECASE,
)
_OBJECT_SIGNAL = re.compile(
    r"\b(file|files|folder|desktop|drive|disk|repo|repository|github|script|"
    r"package|plugin|skill|tool|app|application|program|process|firewall|"
    r"database|table|system32|recycle|usb|ssd|browser|history|photos?|"
    r"documents?|downloads?|computer|machine)\b",
    re.IGNORECASE,
)


def make_stub_classifier() -> Classifier:
    """Return a deterministic offline classifier that fakes the semantic tier."""

    async def classify(text: str) -> dict:
        has_live = bool(_LIVE_SIGNAL.search(text))
        has_action = bool(_ACTION_SIGNAL.search(text))
        has_object = bool(_OBJECT_SIGNAL.search(text))
        # A real-world task needs either a live-data noun or an action verb with
        # a concrete object -- otherwise it's chat, mirroring the router's own
        # bias toward keeping bare or objectless utterances conversational.
        if has_live or (has_action and has_object):
            category = "live_information" if has_live else "files_or_apps"
            return {
                "intent": "agent_task",
                "category": category,
                # Echo the utterance as the task so the grounding check passes
                # (a live classifier keeps the concrete details too).
                "task": text.strip(),
                "confidence": 0.85,
                "reason": "stub matched a real-world signal in the utterance",
            }
        return {
            "intent": "chat",
            "category": "none",
            "task": "",
            "confidence": 0.2,
            "reason": "stub found no real-world signal",
        }

    return classify


def make_live_classifier(model: str, timeout_secs: float) -> Classifier:
    """Return a live Ollama classifier bound to a specific model tag."""

    async def classify(text: str) -> dict:
        return await intent_router.classify_with_ollama(
            text,
            host=cfg.OLLAMA_HOST,
            model=model,
            timeout_secs=timeout_secs,
        )

    return classify


def build_router(
    mode: str,
    *,
    model: str | None = None,
    timeout_secs: float | None = None,
) -> intent_router.IntentRouter:
    """Assemble an :class:`IntentRouter` wired for the chosen classifier mode.

    ``model`` overrides the tier-6 classifier tag (live mode only), letting a
    run benchmark a candidate model against the corpus. ``timeout_secs`` widens
    the per-utterance classifier budget -- necessary for large models (e.g.
    hermes-20b), which will always time out at the 1.5s production default and
    silently degrade every case to chat.
    """
    if mode == "off":
        return intent_router.IntentRouter(enabled=False)
    if mode == "stub":
        return intent_router.IntentRouter(enabled=True, classify=make_stub_classifier())
    if mode == "live":
        model = model or cfg.INTENT_MODEL
        budget = timeout_secs if timeout_secs is not None else cfg.INTENT_TIMEOUT_SECS
        return intent_router.IntentRouter(
            enabled=True,
            classify=make_live_classifier(model, budget),
            timeout_secs=budget,
        )
    raise ValueError(f"unknown classifier mode: {mode!r} (use live|stub|off)")
