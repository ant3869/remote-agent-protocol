"""Tiny helpers for the local Ollama server: which models exist, and preloading one.

Populating the GUI's model dropdown from a hardcoded list would drift the moment
you `ollama create` (or delete) something. Instead we hit Ollama's /api/tags at
startup and use the live truth. Stdlib only (urllib) -- no extra deps, no reason
to import the heavy `ollama` package just to list names.
"""

import json
import urllib.error
import urllib.request
from urllib.error import URLError

from loguru import logger

# Shown if Ollama isn't reachable -- the models config.py says are pre-registered
# and voice-friendly. Better a sensible fallback than an empty dropdown.
_FALLBACK = ["llama3.2:1b", "gemma-e4b-max", "hermes-20b", "gemma-12b"]


def available(host: str, timeout: float = 2.0) -> list[str]:
    """Return sorted model names from Ollama, or a static fallback on failure.

    Args:
        host: bare Ollama host, e.g. "http://localhost:11434" (no /v1).
        timeout: seconds to wait before giving up and using the fallback.
    """
    url = host.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.load(resp)
        names = [m["name"] for m in data.get("models", []) if m.get("name")]
        # Ollama tags models as "name:tag"; ":latest" is noise, trim it for display.
        names = [n[:-7] if n.endswith(":latest") else n for n in names]
        return sorted(set(names)) or _FALLBACK
    except (URLError, OSError, ValueError, KeyError):
        return _FALLBACK


def preload(host: str, model: str, keep_alive: str, timeout: float = 180.0) -> bool:
    """Make ``model`` resident before the first turn needs it.

    An empty prompt loads the weights and generates nothing, which is exactly
    what a warmup wants. Worth doing because the cost lands on the user
    otherwise: a cold chat model turned one recorded turn into 67 seconds
    before the assistant made a sound (data/s2s_turn_timings.jsonl
    2026-08-12), and every idle gap longer than the keep-alive window brings
    that back.

    Best-effort by contract: the caller runs this in the background and the
    session works either way, just slower on its first reply.
    """
    if not model:
        return False
    payload = json.dumps(
        {"model": model, "prompt": "", "stream": False, "keep_alive": keep_alive}
    ).encode()
    request = urllib.request.Request(
        host.rstrip("/") + "/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            resp.read()
    except (URLError, OSError, ValueError) as exc:
        logger.warning(f"Could not preload '{model}': {exc}")
        return False
    logger.info(f"Preloaded '{model}' (keep_alive={keep_alive})")
    return True
