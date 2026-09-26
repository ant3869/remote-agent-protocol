"""The Butler replay eval: the harness can pass every case, and it catches bad decisions."""

from __future__ import annotations

import pytest

from remote_agent_protocol import config as cfg
from voice_probe import butler_eval
from voice_probe.openai_fake import Call, Say, tool_results


def test_the_corpus_is_well_formed_and_covers_the_roadmap():
    assert butler_eval.validate_cases() == []
    refs = {case.roadmap_ref for case in butler_eval.CASES}
    assert refs == {f"#{n}" for n in range(1, 19)}


@pytest.mark.asyncio
async def test_the_ideal_model_passes_every_counted_case():
    results = await butler_eval.run_all(mode="scripted")
    summary = butler_eval.summarize(results)

    failures = {r.id: r.problems for r in results if r.verdict == "fail"}
    assert failures == {}
    assert summary["score"] == 100.0
    assert {r.id for r in results if r.verdict == "gap"} == {"token-file", "clean-downloads"}


@pytest.mark.asyncio
async def test_a_model_that_delegates_everything_fails(monkeypatch):
    def eager(_case):
        def decide(messages, tools_offered):
            if tool_results(messages):
                return Say("Codex is on it and has already finished.")
            return Call(
                ("start_task", {"agent": "code-puppy", "instructions": "handle it", "subject": "x"})
            )

        return decide

    monkeypatch.setattr(butler_eval, "_oracle_brain", eager)
    cases = tuple(
        c
        for c in butler_eval.CASES
        if c.id in {"status-all", "have-codex-do-it", "chat-dog-name", "email-thing"}
    )

    results = {r.id: r for r in await butler_eval.run_all(cases, mode="scripted")}

    assert all(r.verdict == "fail" for r in results.values())
    assert any("expected no dispatch" in p for p in results["status-all"].problems)
    assert any("dispatched to code-puppy" in p for p in results["have-codex-do-it"].problems)
    assert any("expected no tools" in p for p in results["chat-dog-name"].problems)


def test_grading_rejects_a_reply_that_claims_unreturned_results():
    case = next(c for c in butler_eval.CASES if c.id == "email-search")
    calls = [{"name": "start_task", "status": "started", "arguments": {}, "summary": ""}]
    sent = [("hermes", "Search the user's email for school news")]

    problems = butler_eval.grade(
        case, "I found two events at Miles' school.", calls, sent, butler_eval.DEFAULT_HEALTH
    )

    assert any("forbidden" in p for p in problems)


@pytest.mark.asyncio
async def test_the_eval_restores_the_settings_it_overrides():
    before = (cfg.MEMORY_ENABLED, cfg.BUTLER_TOOLS_ENABLED, cfg.AGENT_HISTORY_FILE)
    case = next(c for c in butler_eval.CASES if c.id == "chat-thanks")

    await butler_eval.run_all((case,), mode="scripted")

    assert (cfg.MEMORY_ENABLED, cfg.BUTLER_TOOLS_ENABLED, cfg.AGENT_HISTORY_FILE) == before


@pytest.mark.asyncio
async def test_live_mode_refuses_without_a_cloud_butler_endpoint(monkeypatch):
    from remote_agent_protocol import llm_endpoint

    monkeypatch.setattr(llm_endpoint, "chain", lambda kind: (llm_endpoint.local_endpoint(kind),))

    with pytest.raises(RuntimeError, match="No cloud Butler endpoint"):
        await butler_eval.run_all(mode="live")
