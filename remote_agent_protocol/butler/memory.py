"""The Butler's memory: facts the user asked him to keep.

Facts live in the conversation hub's memory store as shared, user-stated
memories. The store refuses recognizable secrets, and the hub hands shared
memories to agents with their work, so "remember I prefer Codex for UI work"
reaches the agent that does it as well as the Butler.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Protocol

from remote_agent_protocol.conversation_hub.models import ScopedMemory


class MemoryHub(Protocol):
    """The slice of ConversationHub the Butler's memory uses."""

    async def remember(self, subject: str, value: str, *, source_turn_id: str) -> ScopedMemory:
        """Store one user-stated fact."""
        ...

    def shared_memories(self) -> list[ScopedMemory]:
        """Active shared memories, newest first."""
        ...

    async def forget_memory(self, memory_id: str) -> ScopedMemory:
        """Tombstone one memory."""
        ...


_STOPWORDS = frozenset(
    "a an and are as at be but by did do does for from had has have he her his how i if in"
    " is it its me my of on or our she so that the their them they this to was we what"
    " when where which who why will with you your".split()
)


def _words(text: str) -> set[str]:
    return {
        word
        for word in re.findall(r"[a-z0-9]+", text.lower())
        if len(word) > 1 and word not in _STOPWORDS
    }


class ButlerMemory:
    """Remember, recall, and forget facts through the hub."""

    def __init__(self, hub: MemoryHub, turn_id: Callable[[], str]):
        """Bind to the hub.

        Args:
            hub: Where memories are stored and searched.
            turn_id: The current user turn's id, recorded as each fact's source.
        """
        self._hub = hub
        self._turn_id = turn_id

    async def remember(self, subject: str, fact: str) -> ScopedMemory:
        """Store a fact; raises ``ValueError`` when the memory policy refuses it."""
        return await self._hub.remember(subject, fact, source_turn_id=self._turn_id())

    def recall(self, query: str = "", limit: int = 5) -> list[ScopedMemory]:
        """The facts best matching ``query``; the newest when it is empty."""
        memories = self._hub.shared_memories()
        wanted = _words(query)
        if not wanted:
            return memories[:limit]
        scored = [
            (len(wanted & _words(f"{memory.subject} {memory.value}")), index, memory)
            for index, memory in enumerate(memories)
        ]
        matches = sorted((s for s in scored if s[0] > 0), key=lambda s: (-s[0], s[1]))
        return [memory for _score, _index, memory in matches[:limit]]

    async def forget(self, query: str) -> ScopedMemory | None:
        """Forget the single fact that best matches ``query``; returns what it was."""
        found = self.recall(query, limit=1)
        if not found:
            return None
        await self._hub.forget_memory(found[0].memory_id)
        return found[0]

    def prompt_section(self, limit: int = 15, limit_chars: int = 1500) -> str:
        """The newest facts for the system prompt, plus how to use the tools."""
        facts = []
        used = 0
        for memory in self._hub.shared_memories()[:limit]:
            entry = f"{memory.subject}: {memory.value}"
            if used + len(entry) > limit_chars:
                break
            facts.append(entry)
            used += len(entry)
        known = f" Things the user asked you to remember: {'; '.join(facts)}." if facts else ""
        return (
            f"{known} When the user asks you to remember something, or corrects a lasting fact"
            " such as how to refer to someone, call remember; when they ask what you know about"
            " something not listed here, call recall; forget only what they ask you to forget."
        )
