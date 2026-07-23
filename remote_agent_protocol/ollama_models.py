"""Tiny helper: ask the local Ollama server which models are actually registered.

Populating the GUI's model dropdown from a hardcoded list would drift the moment
you `ollama create` (or delete) something. Instead we hit Ollama's /api/tags at
startup and use the live truth. Stdlib only (urllib) -- no extra deps, no reason
to import the heavy `ollama` package just to list names.
"""

import json
import urllib.request
from urllib.error import URLError

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
