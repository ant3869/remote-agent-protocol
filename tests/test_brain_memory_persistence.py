"""Brain mode must persist what Ant actually said, not injected scaffolding.

Phase B3 (2026-09-25): jess_memory.json accumulated "User request: ...
Application context (not a new request): ..." wrapped control-turn content
and synthetic "[[announce]] ... [Agent job update: ...]" relays as if they
were real user turns, because BrainSession never stripped ephemeral content
before saving (VoiceSession already did -- session.py's _save_memory). These
tests cover the fix: self._messages (what the model sees) is unaffected;
only what's written to disk changes.
"""

import json

from remote_agent_protocol import config as cfg
from remote_agent_protocol import memory
from remote_agent_protocol.brain import ANNOUNCE_PREFIX, BrainSession
from remote_agent_protocol.personas import PERSONAS


def _brain(monkeypatch, tmp_path):
    memory_file = tmp_path / "jess_memory.json"
    monkeypatch.setattr(cfg, "MEMORY_FILE", str(memory_file))
    monkeypatch.setattr(cfg, "MEMORY_ENABLED", True)
    brain = BrainSession(PERSONAS[0])
    brain._messages.clear()
    return brain, memory_file


def test_control_turn_wrapper_reaches_the_model_but_not_disk(monkeypatch, tmp_path):
    brain, memory_file = _brain(monkeypatch, tmp_path)
    brain._control_turn = True

    brain._record_user_turn("what's the invoice total", "Invoice total: $412.50")
    brain._finish_turn("It's $412.50.")

    # The model-bound list keeps the full wrapped content, unchanged.
    live_content = brain._messages[-2]["content"]
    assert live_content.startswith("User request: what's the invoice total")
    assert "Application context (not a new request):" in live_content

    saved = json.loads(memory_file.read_text(encoding="utf-8"))
    saved_user_messages = [m["content"] for m in saved if m["role"] == "user"]
    assert saved_user_messages == ["what's the invoice total"]
    assert not any("Application context" in c for c in saved_user_messages)


def test_non_control_turn_is_stored_verbatim(monkeypatch, tmp_path):
    brain, memory_file = _brain(monkeypatch, tmp_path)
    brain._control_turn = False

    brain._record_user_turn("hey what's up", "hey what's up")
    brain._finish_turn("Not much!")

    saved = json.loads(memory_file.read_text(encoding="utf-8"))
    saved_user_messages = [m["content"] for m in saved if m["role"] == "user"]
    assert saved_user_messages == ["hey what's up"]


def test_announce_relay_reaches_the_model_but_is_dropped_from_disk(monkeypatch, tmp_path):
    brain, memory_file = _brain(monkeypatch, tmp_path)
    brain._control_turn = True
    announce_text = f"{ANNOUNCE_PREFIX} [Agent job update: openclaw done. Outcome: inbox clear.]"

    brain._record_user_turn(announce_text, announce_text)
    brain._finish_turn("The inbox is clear.")

    assert brain._messages[-2]["content"] == announce_text  # unaffected, model still sees it

    saved = json.loads(memory_file.read_text(encoding="utf-8"))
    assert not any("Agent job update" in m["content"] for m in saved)


async def _noop():
    return None


def test_stop_persists_through_the_same_stripped_view(monkeypatch, tmp_path):
    brain, memory_file = _brain(monkeypatch, tmp_path)
    brain._control_turn = True
    brain._record_user_turn("check my calendar", "Calendar: 2 events today")

    monkeypatch.setattr(brain, "_remotes", type("_", (), {"stop": staticmethod(_noop)})())
    monkeypatch.setattr(brain, "_bridge", type("_", (), {"shutdown": staticmethod(_noop)})())
    brain._http = None
    brain._lifecycle_ws = None

    import asyncio

    asyncio.run(brain.stop())

    saved = json.loads(memory_file.read_text(encoding="utf-8"))
    saved_user_messages = [m["content"] for m in saved if m["role"] == "user"]
    assert saved_user_messages == ["check my calendar"]


def test_load_strips_a_previously_persisted_announce_relay(monkeypatch, tmp_path):
    memory_file = tmp_path / "jess_memory.json"
    memory.save_memory(
        memory_file,
        [
            {"role": "user", "content": "hello"},
            {
                "role": "user",
                "content": f"{ANNOUNCE_PREFIX} [Agent job update: hermes done. Outcome: ok.]",
            },
            {"role": "assistant", "content": "hi"},
        ],
    )
    monkeypatch.setattr(cfg, "MEMORY_FILE", str(memory_file))
    monkeypatch.setattr(cfg, "MEMORY_ENABLED", True)

    brain = BrainSession(PERSONAS[0])

    contents = [m["content"] for m in brain._messages]
    assert contents == ["hello", "hi"]
