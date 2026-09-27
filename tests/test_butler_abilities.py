"""Butler abilities beyond agent control: skills, memory, and web lookup."""

from __future__ import annotations

import pytest

from remote_agent_protocol.butler import BUILTIN_SKILLS_DIR, SkillLibrary
from remote_agent_protocol.butler.tools import READ_ONLY_TOOLS, ButlerToolbox
from tests.butler_fakes import FakeBridge


def _write_skill(folder, name, text):
    path = folder / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _box(**abilities):
    return ButlerToolbox(
        bridge=FakeBridge(),
        control_plane=None,
        ledger=None,
        dispatch=None,
        admit=lambda agent, task: None,
        needs_confirmation=lambda agent, task: False,
        hold_confirmation=lambda agent, task: "token",
        drop_confirmation=lambda token: True,
        aliases={},
        **abilities,
    )


# -- skills --------------------------------------------------------------------------


def test_a_user_skill_is_listed_and_replaces_a_packaged_one_of_the_same_name(tmp_path):
    _write_skill(
        tmp_path,
        "briefing",
        "---\nname: briefing\ndescription: My own rundown\n---\nSay only what failed.\n",
    )
    _write_skill(tmp_path, "email-style", "---\ndescription: How I like emails\n---\nBe brief.\n")
    library = SkillLibrary(BUILTIN_SKILLS_DIR, tmp_path)

    names = [skill.name for skill in library.catalog()]

    assert names == ["briefing", "email-style"]
    assert library.get("Briefing").instructions == "Say only what failed."
    assert library.get("email style").description == "How I like emails"
    assert "email-style (How I like emails)" in library.prompt_section()


def test_skills_without_instructions_or_a_valid_name_are_skipped(tmp_path):
    _write_skill(tmp_path, "empty", "---\nname: empty\ndescription: nothing here\n---\n")
    _write_skill(tmp_path, "bad", "---\nname: Not A/Valid Name\ndescription: x\n---\nbody\n")

    assert SkillLibrary(tmp_path).catalog() == []
    assert SkillLibrary(tmp_path).prompt_section() == ""


def test_a_new_skill_is_picked_up_without_a_restart(tmp_path):
    library = SkillLibrary(tmp_path)
    assert library.catalog() == []

    _write_skill(tmp_path, "packing", "---\ndescription: Travel packing\n---\nList it.\n")

    assert [skill.name for skill in library.catalog()] == ["packing"]


@pytest.mark.asyncio
async def test_use_skill_returns_the_instructions_and_is_offered_only_with_skills(tmp_path):
    _write_skill(tmp_path, "packing", "---\ndescription: Travel packing\n---\nList it.\n")
    box = _box(skills=SkillLibrary(tmp_path))

    loaded = await box.call("use_skill", {"name": "packing"})
    missing = await box.call("use_skill", {"name": "juggling"})

    assert loaded["instructions"] == "List it."
    assert "packing" in missing["error"]
    assert "use_skill" in {s["function"]["name"] for s in box.schemas()}
    assert "use_skill" in READ_ONLY_TOOLS
    assert "use_skill" not in {s["function"]["name"] for s in _box().schemas()}
    assert (await _box().call("use_skill", {"name": "packing"}))["error"]


# -- memory --------------------------------------------------------------------------


def _hub(tmp_path):
    from remote_agent_protocol.control_plane.registry import AgentRegistry
    from remote_agent_protocol.conversation_hub.factory import build_conversation_hub

    return build_conversation_hub(
        store_path=tmp_path / "conversations.json",
        adapters={},
        registry=AgentRegistry(),
        backends={"codex": ["codex"]},
        aliases={},
    )


@pytest.mark.asyncio
async def test_remembered_facts_are_recalled_listed_and_survive_a_restart(tmp_path):
    from remote_agent_protocol.butler import ButlerMemory

    memory = ButlerMemory(_hub(tmp_path), lambda: "turn-1")
    box = _box(memory=memory)

    kept = await box.call("remember", {"subject": "UI work", "fact": "Prefers Codex for UI work"})
    await box.call("remember", {"subject": "coffee", "fact": "Takes it black"})
    found = await box.call("recall", {"query": "who does the UI"})

    assert kept["status"] == "remembered"
    assert found["facts"] == [{"subject": "UI work", "fact": "Prefers Codex for UI work"}]
    assert "UI work: Prefers Codex for UI work" in box.system_notes()

    restarted = ButlerMemory(_hub(tmp_path), lambda: "turn-2")
    assert [m.subject for m in restarted.recall()] == ["coffee", "UI work"]


@pytest.mark.asyncio
async def test_secrets_are_refused_and_forget_removes_the_best_match(tmp_path):
    from remote_agent_protocol.butler import ButlerMemory

    box = _box(memory=ButlerMemory(_hub(tmp_path), lambda: "turn-1"))
    refused = await box.call(
        "remember", {"subject": "openai", "fact": "api key: sk-abcdefghijklmnop1234"}
    )
    await box.call("remember", {"subject": "coffee", "fact": "Takes it black"})

    forgotten = await box.call("forget", {"query": "coffee"})
    after = await box.call("recall", {"query": "coffee"})

    assert "wasn't kept" in refused["error"]
    assert forgotten["summary"] == "Forgot: coffee: Takes it black"
    assert after["facts"] == []
    assert "recall" in READ_ONLY_TOOLS and "remember" not in READ_ONLY_TOOLS
