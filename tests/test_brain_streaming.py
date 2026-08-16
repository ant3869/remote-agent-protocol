"""Streaming turns: speech must start before the reply is finished.

A spoken turn cannot wait for a completed reply, so the brain releases text at
sentence boundaries. Delegation markers must never reach the speakers.
"""

import asyncio

import aiohttp
import pytest

from remote_agent_protocol import config as cfg
from remote_agent_protocol.brain import BrainSession, LLMUnavailable, _split_sentences
from remote_agent_protocol.personas import PERSONAS


def test_split_sentences_holds_back_an_unfinished_fragment():
    assert _split_sentences("Hello there. How are") == ("Hello there.", " How are")


def test_split_sentences_releases_every_finished_sentence_at_once():
    assert _split_sentences("One. Two! Three? ") == ("One. Two! Three?", " ")


def test_split_sentences_waits_when_nothing_is_finished():
    assert _split_sentences("no terminator yet") == ("", "no terminator yet")


def test_split_sentences_keeps_closing_quotes_with_their_sentence():
    ready, rest = _split_sentences('He said "go." Then left')
    assert ready == 'He said "go."'
    assert rest == " Then left"


def _brain(monkeypatch, deltas):
    monkeypatch.setattr(cfg, "MEMORY_ENABLED", False)
    brain = BrainSession(PERSONAS[0])
    # Start from an empty history so a developer's saved memory file cannot
    # shift the message indices these tests assert on.
    brain._messages.clear()

    async def fake_stream():
        for delta in deltas:
            yield delta

    monkeypatch.setattr(brain, "_stream_ollama", fake_stream)

    async def no_delegation(_text):
        return None

    monkeypatch.setattr(brain, "_resolve_delegation", no_delegation)
    return brain


async def _collect(brain, text="hello"):
    return [piece async for piece in brain.complete_stream(text)]


@pytest.mark.asyncio
async def test_marker_delegation_honors_the_agent_named_in_the_request(monkeypatch):
    brain = _brain(monkeypatch, ["On it. "])
    monkeypatch.setattr(cfg, "AGENT_BACKENDS", {"codex": {}, "code-puppy": {}})
    monkeypatch.setattr(
        cfg, "AGENT_SPOKEN_ALIASES", {"code puppy": "code-puppy", "codex": "codex"}
    )
    brain._default_agent_backend = "codex"
    calls = []
    monkeypatch.setattr(
        brain, "_delegate_ack", lambda agent, task, cwd=None: calls.append((agent, task)) or "ack"
    )
    brain._last_user_text = "codex keeps failing, maybe code puppy can fix the tests"

    brain._handle_delegate_markers("Right away, sir. [[delegate: fix the tests]]")

    assert calls == [("code-puppy", "fix the tests")]


@pytest.mark.asyncio
async def test_marker_delegation_without_a_named_agent_uses_the_default(monkeypatch):
    brain = _brain(monkeypatch, ["On it. "])
    monkeypatch.setattr(cfg, "AGENT_BACKENDS", {"codex": {}, "code-puppy": {}})
    monkeypatch.setattr(
        cfg, "AGENT_SPOKEN_ALIASES", {"code puppy": "code-puppy", "codex": "codex"}
    )
    brain._default_agent_backend = "codex"
    calls = []
    monkeypatch.setattr(
        brain, "_delegate_ack", lambda agent, task, cwd=None: calls.append((agent, task)) or "ack"
    )
    brain._last_user_text = "someone should really fix the tests"

    brain._handle_delegate_markers("Right away, sir. [[delegate: fix the tests]]")

    assert calls == [("codex", "fix the tests")]


@pytest.mark.asyncio
async def test_spoken_cancel_actually_cancels_active_jobs(monkeypatch):
    # Live failure: "cancel whatever Claude Code is doing" produced a polite
    # promise and zero cancellations; brain mode never wired parse_agent_cancel.
    brain = _brain(monkeypatch, ["Very well, sir."])
    monkeypatch.setattr(cfg, "AGENT_SPOKEN_ALIASES", {"claude code": "claude-code"})
    cancelled = []

    class Bridge:
        async def cancel_active(self, agent=None, *, all_jobs=False):
            cancelled.append((agent, all_jobs))
            return 2

    monkeypatch.setattr(brain, "_bridge", Bridge())

    async def routing_must_not_run(_text):
        raise AssertionError("cancel turns must never reach delegation routing")

    monkeypatch.setattr(brain, "_resolve_delegation", routing_must_not_run)

    await _collect(brain, "cancel whatever claude code is doing")

    assert cancelled == [("claude-code", False)]
    assert "cancelled 2" in brain._messages[0]["content"]


@pytest.mark.asyncio
async def test_cancel_turns_never_dispatch_delegation_markers(monkeypatch):
    # Live failure: the LLM narrated the cancellation with a [[delegate:]]
    # marker, which started a brand-new job mid-cancellation.
    brain = _brain(monkeypatch, ["Terminating. ", "[[delegate: stop claude code work]]"])
    monkeypatch.setattr(cfg, "AGENT_SPOKEN_ALIASES", {"claude code": "claude-code"})

    class Bridge:
        async def cancel_active(self, agent=None, *, all_jobs=False):
            return 1

    monkeypatch.setattr(brain, "_bridge", Bridge())
    dispatched = []
    monkeypatch.setattr(
        brain, "_delegate_ack", lambda agent, task, cwd=None: dispatched.append(task) or "ack"
    )

    pieces = await _collect(brain, "I want you to cancel all tasks")

    assert dispatched == []
    assert "[[delegate" not in "".join(pieces)


@pytest.mark.asyncio
async def test_announce_turns_never_dispatch_delegation_markers(monkeypatch):
    # Live failure: the butler's job-summary narration included a marker,
    # which spawned a brand-new job every time a job finished. Job mitosis.
    brain = _brain(monkeypatch, ["Summarized. ", "[[delegate: diagnose it]]"])
    dispatched = []
    monkeypatch.setattr(
        brain, "_delegate_ack", lambda agent, task, cwd=None: dispatched.append(task) or "ack"
    )

    await _collect(brain, "[[announce]] [Agent job update: hermes failed. Summarize.]")

    assert dispatched == []


@pytest.mark.asyncio
async def test_a_hung_cancellation_cannot_freeze_the_brain(monkeypatch):
    # Live failure: a job that would not die blocked cancel_active, which held
    # the turn lock; every later request timed out after 20s of silence.
    brain = _brain(monkeypatch, ["Working on it, sir."])
    monkeypatch.setattr(cfg, "AGENT_SPOKEN_ALIASES", {"hermes": "hermes"})
    started = asyncio.Event()

    class StuckBridge:
        async def cancel_active(self, agent=None, *, all_jobs=False):
            started.set()
            await asyncio.sleep(3600)

    monkeypatch.setattr(brain, "_bridge", StuckBridge())
    monkeypatch.setattr(brain, "_cancel_wait_s", 0.05, raising=False)

    pieces = await asyncio.wait_for(_collect(brain, "cancel all tasks"), timeout=5.0)

    assert started.is_set()
    assert pieces  # the butler still answered instead of going silent
    assert "background" in brain._messages[0]["content"]


@pytest.mark.asyncio
async def test_status_questions_report_jobs_instead_of_spawning_new_ones(monkeypatch):
    # Live failure: "can I get an update?" was delegated as a brand-new task.
    brain = _brain(monkeypatch, ["Here is the state of play, sir."])
    monkeypatch.setattr(cfg, "AGENT_SPOKEN_ALIASES", {"hermes": "hermes"})

    class Job:
        agent = "hermes"
        task = "fix the configuration problem"
        status = "running"

    class Bridge:
        def active_jobs(self, agent=None):
            return [Job()]

    monkeypatch.setattr(brain, "_bridge", Bridge())

    async def routing_must_not_run(_text):
        raise AssertionError("status questions must never reach delegation routing")

    monkeypatch.setattr(brain, "_resolve_delegation", routing_must_not_run)

    await _collect(brain, "what about an update on the hermes task?")

    content = brain._messages[0]["content"]
    assert "hermes" in content
    assert "running" in content


@pytest.mark.asyncio
async def test_announce_turns_bypass_routing_and_answer_from_history(monkeypatch):
    brain = _brain(monkeypatch, ["The tests pass now, sir."])

    async def routing_must_not_run(_text):
        raise AssertionError("announce turns must never consult the router")

    monkeypatch.setattr(brain, "_resolve_delegation", routing_must_not_run)

    pieces = await _collect(
        brain, "[[announce]] [Agent job finished. Summarize the outcome for the user.]"
    )

    assert "".join(pieces) == "The tests pass now, sir."
    # The magic prefix is transport plumbing and must not reach the model.
    assert brain._messages[0]["role"] == "user"
    assert "[[announce]]" not in brain._messages[0]["content"]
    assert "Agent job finished" in brain._messages[0]["content"]


@pytest.mark.asyncio
async def test_sentences_are_released_before_the_reply_finishes(monkeypatch):
    brain = _brain(monkeypatch, ["One two. ", "Three four. ", "Five six."])

    pieces = await _collect(brain)

    # Released progressively, not as one block at the end.
    assert len(pieces) > 1
    assert "".join(pieces).strip() == "One two. Three four. Five six."


@pytest.mark.asyncio
async def test_a_trailing_fragment_is_still_delivered(monkeypatch):
    brain = _brain(monkeypatch, ["Complete one. ", "dangling tail"])

    assert "".join(await _collect(brain)).strip() == "Complete one. dangling tail"


@pytest.mark.asyncio
async def test_a_delegation_marker_is_never_spoken(monkeypatch):
    brain = _brain(monkeypatch, ["Right away, sir. ", "[[delegate: ", "check the logs]]"])

    spoken = "".join(await _collect(brain))

    assert "[[" not in spoken
    assert "delegate" not in spoken.lower()
    assert spoken.startswith("Right away, sir.")


@pytest.mark.asyncio
async def test_text_before_a_marker_still_gets_spoken(monkeypatch):
    brain = _brain(monkeypatch, ["Certainly. ", "I shall see to it. ", "[[delegate: tidy up]]"])

    spoken = "".join(await _collect(brain))

    assert "Certainly." in spoken
    assert "I shall see to it." in spoken
    assert "[[" not in spoken


@pytest.mark.asyncio
async def test_a_marker_split_across_deltas_is_still_caught(monkeypatch):
    # The "[[" itself can straddle two deltas of the stream.
    brain = _brain(monkeypatch, ["Fine. ", "[", "[delegate: do it]]"])

    assert "[[" not in "".join(await _collect(brain))


@pytest.mark.asyncio
async def test_the_finished_reply_is_recorded_once(monkeypatch):
    events = []
    brain = _brain(monkeypatch, ["All done. "])
    brain._on_event = events.append

    await _collect(brain)

    assistant = [e for e in events if e.get("role") == "assistant"]
    assert len(assistant) == 1
    assert assistant[0]["text"] == "All done."
    assert brain._messages[-1] == {"role": "assistant", "content": "All done."}


@pytest.mark.asyncio
async def test_an_empty_turn_streams_nothing(monkeypatch):
    brain = _brain(monkeypatch, ["ignored"])

    assert await _collect(brain, text="   ") == []


@pytest.mark.asyncio
async def test_concurrent_turns_are_serialized_without_crossing_history(monkeypatch):
    monkeypatch.setattr(cfg, "MEMORY_ENABLED", False)
    brain = BrainSession(PERSONAS[0])
    brain._messages.clear()
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()

    async def no_delegation(_text):
        return None

    async def fake_stream():
        current = brain._messages[-1]["content"]
        if current == "first":
            first_entered.set()
            await release_first.wait()
            yield "First reply."
        else:
            second_entered.set()
            yield "Second reply."

    monkeypatch.setattr(brain, "_resolve_delegation", no_delegation)
    monkeypatch.setattr(brain, "_stream_ollama", fake_stream)

    first = asyncio.create_task(_collect(brain, "first"))
    await asyncio.wait_for(first_entered.wait(), timeout=1)
    second = asyncio.create_task(_collect(brain, "second"))
    await asyncio.sleep(0.05)
    serialized = not second_entered.is_set()
    release_first.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert serialized is True
    assert first_result == ["First reply."]
    assert second_result == ["Second reply."]
    assert brain._messages == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "First reply."},
        {"role": "user", "content": "second"},
        {"role": "assistant", "content": "Second reply."},
    ]


@pytest.mark.asyncio
async def test_the_multimodal_body_feeds_the_model_not_the_transcript(monkeypatch):
    events = []
    brain = _brain(monkeypatch, ["Noted. "])
    brain._on_event = events.append

    [p async for p in brain.complete_stream("summarize this", llm_content="## Files\nreport.pdf")]

    user_events = [e for e in events if e.get("role") == "user"]
    assert user_events[0]["text"] == "summarize this"
    assert brain._messages[0]["content"] == "## Files\nreport.pdf"


class _RefusedConnection:
    """Fails the way aiohttp does when nothing is listening on the model host."""

    async def __aenter__(self):
        raise aiohttp.ClientConnectionError("Cannot connect to host localhost:11434")

    async def __aexit__(self, *exc_info):
        return False


class _RefusingHttp:
    def post(self, *args, **kwargs):
        return _RefusedConnection()


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_an_unreachable_model_host_is_named_as_such(monkeypatch, streaming):
    # Callers answer an unreachable host with a spoken hint instead of a server
    # fault, which they can only do if it arrives as its own error.
    monkeypatch.setattr(cfg, "MEMORY_ENABLED", False)
    brain = BrainSession(PERSONAS[0])
    brain._http = _RefusingHttp()

    with pytest.raises(LLMUnavailable):
        if streaming:
            [delta async for delta in brain._stream_ollama()]
        else:
            await brain._call_ollama()


@pytest.mark.asyncio
async def test_a_dispatched_delegation_is_held_until_it_reaches_the_bridge(monkeypatch):
    # asyncio keeps only a weak reference to a bare task: a delegation dropped
    # by the garbage collector would leave the assistant claiming work that
    # never started.
    monkeypatch.setattr(cfg, "MEMORY_ENABLED", False)
    brain = BrainSession(PERSONAS[0])
    started = asyncio.Event()

    async def slow_start(agent, task, cwd=None, **kwargs):
        await asyncio.sleep(0.05)
        started.set()
        return "job-1"

    monkeypatch.setattr(brain._bridge, "start", slow_start)
    brain._delegate_ack("mock", "check the printer")

    assert brain._tasks, "the dispatch task must be referenced while it runs"
    import gc

    gc.collect()  # a bare create_task would not survive this
    await asyncio.wait_for(started.wait(), timeout=2)
    await asyncio.sleep(0)
    assert not brain._tasks, "finished tasks are released again"
