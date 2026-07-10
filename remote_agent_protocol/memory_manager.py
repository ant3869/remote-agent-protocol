"""Memory-manager helpers for Jess.

This module is intentionally tiny/pure: normalize mem0's result shapes and turn
messages into readable GUI rows. Actual mem0 access is owned by VoiceSession so
we don't open a second embedded Qdrant client and start a lock-fight. Been there,
chewed that shoe.
"""

import re
from typing import Any

_REMEMBER_THAT_RE = re.compile(r"^\s*remember\s+that\s+", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")
_POSSESSIVE_ALIAS_RE = re.compile(
    r"\b(?:ant|superhands|user)(?:'s|’s|'|’)(?=\s|$)",
    re.IGNORECASE,
)
_THE_USER_RE = re.compile(r"\bthe\s+user\b", re.IGNORECASE)
_LEADING_ALIAS_ATTRIBUTE_RE = re.compile(
    r"^(?:ant|superhands|user)\s+(gpu|name|favorite color|current home address)\b",
    re.IGNORECASE,
)
_LEADING_SUBJECT_ALIAS_RE = re.compile(r"^(?:ant|superhands|user)\b", re.IGNORECASE)
_FIRST_PERSON_REPLACEMENTS = (
    (re.compile(r"\bmy\b", re.IGNORECASE), "user's"),
    (re.compile(r"\bi\s+am\b|\bi'm\b|\bim\b", re.IGNORECASE), "user is"),
    (re.compile(r"\bi\s+have\b|\bi've\b|\bive\b", re.IGNORECASE), "user has"),
    (re.compile(r"\bi\s+use\b", re.IGNORECASE), "user uses"),
    (re.compile(r"\bi\s+like\b", re.IGNORECASE), "user likes"),
    (re.compile(r"\bi\s+love\b", re.IGNORECASE), "user loves"),
    (re.compile(r"\bi\s+hate\b", re.IGNORECASE), "user hates"),
    (re.compile(r"\bi\s+prefer\b", re.IGNORECASE), "user prefers"),
    (re.compile(r"\bi\s+want\b", re.IGNORECASE), "user wants"),
    (re.compile(r"\bi\s+need\b", re.IGNORECASE), "user needs"),
    (re.compile(r"\bcall\s+me\b", re.IGNORECASE), "user is called"),
)


def cleaned_fact_text(text: str) -> str:
    """Normalize whitespace and remove the manual-memory instruction prefix."""
    cleaned = _WHITESPACE_RE.sub(" ", str(text).strip())
    cleaned = _REMEMBER_THAT_RE.sub("", cleaned, count=1)
    return cleaned.strip()


def fact_key(text: str) -> str:
    """Build a stable duplicate key for semantically identical user facts."""
    cleaned = cleaned_fact_text(text).replace("’", "'").lower()
    for pattern, replacement in _FIRST_PERSON_REPLACEMENTS:
        cleaned = pattern.sub(replacement, cleaned)
    cleaned = _THE_USER_RE.sub("user", cleaned)
    cleaned = _POSSESSIVE_ALIAS_RE.sub("user's", cleaned)
    cleaned = _LEADING_ALIAS_ATTRIBUTE_RE.sub(r"user's \1", cleaned)
    cleaned = _LEADING_SUBJECT_ALIAS_RE.sub("user", cleaned)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip(" .?!")
    return cleaned


def semantic_memory_metadata(text: str, *, source: str, **extra: Any) -> dict[str, Any]:
    """Metadata stored with mem0 rows so future writes can dedupe cheaply."""
    metadata: dict[str, Any] = {"source": source, "fact_key": fact_key(text)}
    metadata.update(extra)
    return metadata


def row_fact_key(row: dict[str, Any]) -> str:
    """Return the stored or derived fact key for a normalized or raw mem0 row."""
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    stored = metadata.get("fact_key")
    if stored:
        return str(stored)
    text = row.get("memory") or row.get("text") or row.get("content") or ""
    return fact_key(str(text))


def fact_keys(raw: Any) -> set[str]:
    """Return known semantic fact keys from raw mem0 output."""
    if isinstance(raw, dict):
        items = raw.get("results") or raw.get("memories") or []
    elif isinstance(raw, list):
        items = raw
    else:
        items = []
    return {row_fact_key(item) for item in items if isinstance(item, dict)}


def normalize_memories(raw: Any) -> list[dict[str, Any]]:
    """Normalize mem0 get_all/search output into display-ready semantic rows."""
    if isinstance(raw, dict):
        items = raw.get("results") or raw.get("memories") or []
    elif isinstance(raw, list):
        items = raw
    else:
        items = []

    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        text = item.get("memory") or item.get("text") or item.get("content") or ""
        source = item.get("source") or metadata.get("source") or "semantic"
        rows.append(
            {
                "id": str(item.get("id") or item.get("memory_id") or ""),
                "scope": "semantic",
                "source": str(source),
                "text": str(text),
                "score": item.get("score"),
                "metadata": metadata,
            }
        )
    return rows


def display_line(row: dict[str, Any]) -> str:
    """Compact one-line display for a semantic memory row."""
    ident = str(row.get("id") or "")[:8]
    score = row.get("score")
    text = str(row.get("text") or "")
    if isinstance(score, (int, float)):
        return f"{ident} | {score:.2f} | {text}"
    return f"{ident} | -- | {text}"


def transcript_rows(messages: list[dict[str, Any]]) -> list[str]:
    """Readable rows for the short-term transcript memory."""
    rows: list[str] = []
    for msg in messages:
        role = msg.get("role")
        who = "You" if role == "user" else "Jess" if role == "assistant" else str(role)
        rows.append(f"{who}: {msg.get('content', '')}")
    return rows


def transcript_memory_rows(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Display-ready rows for short-term transcript memory."""
    rows: list[dict[str, Any]] = []
    for index, msg in enumerate(messages, start=1):
        role = str(msg.get("role") or "message")
        who = "You" if role == "user" else "Jess" if role == "assistant" else role
        rows.append(
            {
                "id": f"turn-{index}",
                "scope": "short",
                "source": "transcript",
                "role": role,
                "label": who,
                "text": str(msg.get("content", "")),
                "score": None,
            }
        )
    return rows


def manual_memory_message(text: str) -> dict[str, str]:
    """Build a user-style message for a manually pinned semantic memory."""
    cleaned = " ".join(text.strip().split())
    if not cleaned:
        raise ValueError("memory text is empty")
    if cleaned.lower().startswith("remember that "):
        content = "Remember that " + cleaned[len("remember that ") :]
    else:
        content = f"Remember that {cleaned}"
    return {"role": "user", "content": content}
