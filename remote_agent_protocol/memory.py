"""Persistent conversation memory for the local pipecat demo.

The bot's LLMContext holds the whole conversation in RAM, but it vanishes the
moment the process dies. This module persists those messages to a JSON file so
the bot remembers you across restarts -- no cloud, no vector DB, just a file.

Design notes:
  * Only user/assistant turns are stored. The system prompt lives in the LLM
    settings (`system_instruction`), NOT in the message list, so we never
    accidentally duplicate or stale-cache the personality.
  * On load we keep only the last `max_messages` turns. A small local model has
    a finite context window, and an ever-growing transcript would eventually
    blow it out (and slow every single inference). Old memories gracefully fall
    off the back.
"""

import contextlib
import json
import os
from pathlib import Path
from typing import Any

from loguru import logger


def load_memory(path: str | Path, max_messages: int) -> list[dict[str, Any]]:
    """Load saved conversation messages, trimmed to the last `max_messages`.

    Returns an empty list if the file is missing or unreadable -- a corrupt or
    absent memory file should never stop the bot from booting.
    """
    path = Path(path)
    if not path.exists():
        logger.info(f"No memory file at {path} -- starting fresh.")
        return []

    try:
        messages = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Couldn't read memory file {path} ({e}) -- starting fresh.")
        return []

    if not isinstance(messages, list):
        logger.warning(f"Memory file {path} isn't a list -- ignoring it.")
        return []

    if max_messages > 0 and len(messages) > max_messages:
        messages = messages[-max_messages:]

    logger.info(f"Loaded {len(messages)} remembered messages from {path}.")
    return messages


def strip_ephemeral(
    messages: list[dict[str, Any]],
    *,
    system_prefixes: tuple[str, ...] = (),
    drop_contents: tuple[str, ...] = (),
    drop_prefixes: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Return ``messages`` with non-conversational cruft removed.

    Two kinds of junk accumulate in the live LLMContext and must NOT be
    persisted, or they compound across every single restart:

      * mem0's injected "Here's what I remember..." system blocks. These are
        regenerated fresh from the vector store each session, so persisting them
        just stacks duplicates and bloats the prompt (the #1 cause of slow
        responses). Matched by ``system_prefixes``.
      * one-shot kickoff instructions like "The user just came back..." that are
        directions to the model, not things the user actually said. Matched
        exactly (whitespace-stripped) by ``drop_contents``.
      * injected one-shot prompts whose text varies but shares a stable prefix
        (e.g. agent-bridge "[Background task update..." relays). Matched on any
        role by ``drop_prefixes``.
    """
    blocked = {c.strip() for c in drop_contents}
    cleaned: list[dict[str, Any]] = []
    for message in messages:
        content = str(message.get("content", ""))
        if message.get("role") == "system" and any(
            content.startswith(prefix) for prefix in system_prefixes
        ):
            continue
        if content.strip() in blocked:
            continue
        if any(content.startswith(prefix) for prefix in drop_prefixes):
            continue
        cleaned.append(message)
    return cleaned


def save_memory(path: str | Path, messages: list[dict[str, Any]]) -> None:
    """Persist conversation messages to disk as pretty-printed JSON.

    Written to a temp file and swapped into place so a crash mid-write can't
    corrupt the existing memory file. Best-effort: a write failure is logged
    but never raised, so a flaky disk can't crash the shutdown path.
    """
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(messages, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        logger.info(f"Saved {len(messages)} messages to {path}.")
    except OSError as e:
        logger.warning(f"Couldn't save memory to {path}: {e}")
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
