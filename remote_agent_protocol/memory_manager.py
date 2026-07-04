"""Memory-manager helpers for Jess.

This module is intentionally tiny/pure: normalize mem0's result shapes and turn
messages into readable GUI rows. Actual mem0 access is owned by VoiceSession so
we don't open a second embedded Qdrant client and start a lock-fight. Been there,
chewed that shoe.
"""

from typing import Any


def normalize_memories(raw: Any) -> list[dict[str, Any]]:
    """Normalize mem0 get_all/search output into [{id, text, score}, ...]."""
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
        text = item.get("memory") or item.get("text") or item.get("content") or ""
        rows.append(
            {
                "id": str(item.get("id") or item.get("memory_id") or ""),
                "text": str(text),
                "score": item.get("score"),
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
