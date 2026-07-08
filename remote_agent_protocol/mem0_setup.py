"""Fully-local long-term semantic memory via mem0 + Ollama + embedded Qdrant.

This is the "heavy-duty" memory: unlike memory.py (which just persists the raw
recent transcript), mem0 uses a small LLM to *distill facts* from conversations
and stores them in a vector database. On every turn it semantically searches
those facts and injects the relevant ones into the context -- so Jess can recall
"you have a dog named Pixel" months later, even if you phrase the question
completely differently.

Everything runs locally:
  * fact-extraction LLM : Ollama (a small/fast model -- doesn't tie up the chat model)
  * embeddings          : Ollama (nomic-embed-text, 768-dim)
  * vector store        : Qdrant in embedded on-disk mode (no server, just a folder)

No API keys, no cloud.
"""

import logging
import os
import re

from remote_agent_protocol import config as cfg

# mem0 still imports the OpenAI SDK internally even when fully local; give it a
# dummy key so nothing hangs probing for one. And opt out of telemetry.
os.environ.setdefault("OPENAI_API_KEY", "not-needed-local")
os.environ.setdefault("MEM0_TELEMETRY", "False")

# mem0 uses stdlib `logging` (not loguru), so our loguru level filter doesn't
# catch its chatter. Mute the benign "spaCy not installed" / "fastembed not
# installed" warnings -- those are optional NLP extras; semantic recall works
# fine without them. Bump to ERROR so real problems still surface.
logging.getLogger("mem0").setLevel(logging.ERROR)

# nomic-embed-text produces 768-dimensional vectors. The embedder and the vector
# store must agree on this number or Qdrant rejects the inserts.
_EMBED_DIMS = 768

# Keep mem0 for durable user facts, not quotes from Jess or random chatter.
# This is deliberately conservative: forgetting a useless utterance is cheap;
# storing junk forever makes the assistant worse every day. YAGNI with teeth.
_DURABLE_USER_MARKERS = (
    "my ",
    "i am ",
    "i'm ",
    "im ",
    "i live ",
    "i work ",
    "i use ",
    "i have ",
    "i've ",
    "ive ",
    "i like ",
    "i love ",
    "i hate ",
    "i prefer ",
    "i want ",
    "i need ",
    "i go by ",
    "remember ",
    "remember that ",
    "call me ",
    "you can call me ",
    "your name is ",
    "the agent's name is ",
    "the agents name is ",
    "the assistant's name is ",
    "the assistants name is ",
    "don't ",
    "do not ",
)

# Speech-to-text and casual phrasing bury the real marker behind filler ("well,
# my name is..."). Peel these off the front before matching so genuine facts are
# not dropped just for a leading discourse word.
_LEADING_FILLER = (
    "well",
    "so",
    "actually",
    "okay",
    "ok",
    "yeah",
    "yep",
    "hey",
    "oh",
    "um",
    "uh",
    "like",
    "just",
)
_FILLER_EDGE = " ,.-!?"


def _strip_leading_filler(text: str) -> str:
    """Drop leading discourse words (and their punctuation) before matching."""
    changed = True
    while changed:
        changed = False
        text = text.lstrip(_FILLER_EDGE)
        for filler in _LEADING_FILLER:
            # Whole-word only: strip "so my..." but never "some...".
            if (
                text.startswith(filler)
                and len(text) > len(filler)
                and not text[len(filler)].isalpha()
            ):
                text = text[len(filler) :]
                changed = True
                break
    return text.lstrip(_FILLER_EDGE)


_LOW_VALUE_USER_TEXT = {
    "lol",
    "lmao",
    "haha",
    "what",
    "what?",
    "yes",
    "no",
    "ok",
    "okay",
    "thanks",
    "thank you",
    "tell me a joke",
    "continue",
    "go on",
}

_AGENT_FACT_RE = re.compile(
    r"^(?:the\s+)?(?:agent|assistant|tool agent|jess|hermes|hermes-agent|code puppy|code-puppy)"
    r"\b.*\b(?:is|are|name is|goes by|called)\b",
    re.IGNORECASE,
)


def filter_messages_for_storage(messages: list[dict]) -> list[dict]:
    """Return only durable user-authored facts/preferences worth mem0 storage.

    mem0's extractor is good, but feeding it *everything* lets assistant quips
    and throwaway user chatter become "long-term memories". Gross. We only pass
    user statements that look like identity/preference/fact material.
    """
    blocked = {cfg.KICKOFF_FIRST.strip(), cfg.KICKOFF_RETURNING.strip()}
    kept: list[dict] = []
    for message in messages:
        if message.get("role") != "user":
            continue
        content = str(message.get("content", "")).strip()
        if not content or content in blocked:
            continue
        lower = content.lower().strip()
        if lower in _LOW_VALUE_USER_TEXT:
            continue
        # A durable marker is the real signal -- don't also impose a length floor
        # that would silently drop short but genuine facts like "I'm 40" or
        # "call me Ant". The marker prefixes already require content after them.
        stripped_lower = _strip_leading_filler(lower.lstrip(" ,.-"))
        if stripped_lower.startswith(_DURABLE_USER_MARKERS) or _AGENT_FACT_RE.search(
            stripped_lower
        ):
            kept.append({"role": "user", "content": content})
    return kept


def build_local_config() -> dict:
    """Assemble the mem0 ``local_config`` dict for a 100% offline stack."""
    return {
        "llm": {
            "provider": "ollama",
            "config": {
                "model": cfg.MEM0_LLM_MODEL,
                "ollama_base_url": cfg.OLLAMA_HOST,
            },
        },
        "embedder": {
            "provider": "ollama",
            "config": {
                "model": cfg.MEM0_EMBED_MODEL,
                "ollama_base_url": cfg.OLLAMA_HOST,
                "embedding_dims": _EMBED_DIMS,
            },
        },
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "collection_name": cfg.MEM0_COLLECTION,
                "embedding_model_dims": _EMBED_DIMS,
                "path": cfg.MEM0_QDRANT_PATH,
                "on_disk": True,
            },
        },
    }


def create_memory_service():
    """Build a configured Mem0MemoryService ready to drop into the pipeline.

    Imported lazily so the (heavy) mem0 import only happens when memory is
    actually enabled.

    NOTE: we return a small subclass that DEDUPES the injected memory block.
    Upstream's Mem0MemoryService ``insert``s a fresh "Here's what I remember..."
    system message every turn but never removes the previous one, so the context
    balloons with stacked duplicates -- which tanks inference speed on a local
    CPU model. The override strips any stale block before letting the parent add
    the new one.
    """
    from pipecat.services.mem0.memory import Mem0MemoryService

    header = cfg.MEM0_MEMORY_HEADER

    class DedupingMem0MemoryService(Mem0MemoryService):
        async def _store_messages(self, messages):
            filtered = filter_messages_for_storage(messages)
            if not filtered:
                return
            await super()._store_messages(filtered)

        async def _enhance_context_with_memories(self, context, query):
            # Mirror the parent's no-op guard so we don't strip a live block on
            # a repeated query (where the parent would re-add nothing).
            if self.last_query == query:
                return

            # Drop every memory block we injected on a previous turn before the
            # parent inserts a freshly-retrieved one.
            kept = [
                m
                for m in context.get_messages()
                if not (m.get("role") == "system" and str(m.get("content", "")).startswith(header))
            ]
            context.set_messages(kept)

            await super()._enhance_context_with_memories(context, query)

    return DedupingMem0MemoryService(
        local_config=build_local_config(),
        user_id=cfg.MEM0_USER_ID,
        params=Mem0MemoryService.InputParams(
            search_limit=cfg.MEM0_SEARCH_LIMIT,
            search_threshold=cfg.MEM0_SEARCH_THRESHOLD,
            system_prompt=f"{header}\n\n",
            add_as_system_message=True,
            position=1,
        ),
    )
