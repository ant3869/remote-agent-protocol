"""The staged provider/model test pipeline, against a fake local HTTP server.

Every assertion here exercises real HTTP round-trips (reach, auth, catalog,
chat, tool call, JSON) rather than mocking ``aiohttp`` -- the point of the
fake server is to make that safe and fast, not to skip it.
"""

from __future__ import annotations

import pytest

from remote_agent_protocol import model_providers as mp
from remote_agent_protocol import provider_tests as pt
from tests.fake_openai_server import FakeOpenAIServer, closed_port


@pytest.fixture
def server():
    srv = FakeOpenAIServer()
    srv.start()
    yield srv
    srv.stop()


def _provider(base_url: str, **overrides) -> mp.ProviderConfig:
    fields = {
        "id": "test-provider",
        "preset": "custom",
        "label": "Test",
        "base_url": base_url,
        "auth": "bearer",
    }
    fields.update(overrides)
    return mp.ProviderConfig(**fields)


# --- provider test: reach -> authenticate -> catalog ---


@pytest.mark.asyncio
async def test_provider_test_passes_end_to_end(server):
    provider = _provider(server.base_url)

    results, models = await pt.run_provider_test(provider, "sk-test")

    assert [r.stage for r in results] == ["reach", "authenticate", "catalog"]
    assert all(r.ok for r in results)
    assert models == []
    assert results[-1].hint == "Enter a model ID manually."


@pytest.mark.asyncio
async def test_reach_stage_fails_against_an_unreachable_port():
    provider = _provider(f"http://127.0.0.1:{closed_port()}/v1")

    results, models = await pt.run_provider_test(provider, "sk-test")

    assert [r.stage for r in results] == ["reach"]
    assert results[0].ok is False
    assert models == []


@pytest.mark.asyncio
async def test_authenticate_stage_reports_a_rejected_key(server):
    server.script.require_auth = True
    server.script.expected_key = "sk-correct"
    provider = _provider(server.base_url)

    results, models = await pt.run_provider_test(provider, "sk-wrong")

    assert [r.stage for r in results] == ["reach", "authenticate"]
    assert results[-1].ok is False
    assert results[-1].detail == "Key rejected"
    assert models == []


@pytest.mark.asyncio
async def test_authenticate_stage_passes_with_the_correct_key(server):
    server.script.require_auth = True
    server.script.expected_key = "sk-correct"
    provider = _provider(server.base_url)

    results, _ = await pt.run_provider_test(provider, "sk-correct")

    assert all(r.ok for r in results)


@pytest.mark.asyncio
async def test_catalog_stage_reports_the_model_count(server):
    server.script.models_body = {"data": [{"id": "vendor/a"}, {"id": "vendor/b"}]}
    provider = _provider(server.base_url)

    results, models = await pt.run_provider_test(provider, "sk-test")

    assert models == ["vendor/a", "vendor/b"]
    assert results[-1].detail == "2 models"
    assert results[-1].hint == ""


@pytest.mark.asyncio
async def test_catalog_stage_surfaces_a_server_error(server):
    # openrouter has its own /key path, so /models can fail independently
    # of authenticate -- a preset without one (like "custom") shares the
    # same endpoint for both stages.
    server.script.models_status = 500
    server.script.models_body = {"error": {"message": "boom"}}
    provider = _provider(server.base_url, preset="openrouter")

    results, models = await pt.run_provider_test(provider, "sk-test")

    assert [r.stage for r in results] == ["reach", "authenticate", "catalog"]
    assert results[-1].ok is False
    assert "500" in results[-1].detail
    assert models == []


@pytest.mark.asyncio
async def test_a_leaked_key_never_appears_in_an_error_detail(server):
    """The catalog error body is redacted through secret_store, same as any log line."""
    from remote_agent_protocol import secret_store

    secret_store.set_key("test-provider", "sk-should-be-redacted")
    server.script.models_status = 500
    server.script.models_body = {"error": {"message": "bad key sk-should-be-redacted"}}
    provider = _provider(server.base_url)

    try:
        results, _ = await pt.run_provider_test(provider, "sk-should-be-redacted")
    finally:
        secret_store._known_secrets.discard("sk-should-be-redacted")

    assert "sk-should-be-redacted" not in results[-1].detail


# --- model test: chat -> tool call -> JSON ---


@pytest.mark.asyncio
async def test_chat_stage_passes_when_the_marker_is_present(server):
    server.script.chat_body = {"choices": [{"message": {"content": "RAP_OK"}}]}
    provider = _provider(server.base_url)

    result = await pt._chat(provider, "sk-test", "vendor/model-a")

    assert result.ok is True


@pytest.mark.asyncio
async def test_chat_stage_fails_when_the_marker_is_missing(server):
    server.script.chat_body = {"choices": [{"message": {"content": "sure, hi!"}}]}
    provider = _provider(server.base_url)

    result = await pt._chat(provider, "sk-test", "vendor/model-a")

    assert result.ok is False
    assert result.hint


@pytest.mark.asyncio
async def test_tool_call_stage_passes_with_valid_json_arguments(server):
    server.script.chat_body = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {"function": {"name": "get_time", "arguments": '{"timezone": "UTC"}'}}
                    ]
                }
            }
        ]
    }
    provider = _provider(server.base_url)

    result = await pt._tool_call(provider, "sk-test", "vendor/model-a")

    assert result.ok is True


@pytest.mark.asyncio
async def test_tool_call_stage_fails_when_no_tool_was_called(server):
    server.script.chat_body = {"choices": [{"message": {"content": "it's 3pm"}}]}
    provider = _provider(server.base_url)

    result = await pt._tool_call(provider, "sk-test", "vendor/model-a")

    assert result.ok is False
    assert "did not call a tool" in result.detail


@pytest.mark.asyncio
async def test_tool_call_stage_fails_on_malformed_arguments(server):
    server.script.chat_body = {
        "choices": [
            {"message": {"tool_calls": [{"function": {"name": "get_time", "arguments": "{bad"}}]}}
        ]
    }
    provider = _provider(server.base_url)

    result = await pt._tool_call(provider, "sk-test", "vendor/model-a")

    assert result.ok is False
    assert "not valid JSON" in result.detail


@pytest.mark.asyncio
async def test_json_stage_passes_with_a_matching_schema(server):
    server.script.chat_body = {"choices": [{"message": {"content": '{"ok": true}'}}]}
    provider = _provider(server.base_url)

    result = await pt._json_output(provider, "sk-test", "vendor/model-a")

    assert result.ok is True


@pytest.mark.asyncio
async def test_json_stage_fails_when_the_reply_is_not_json(server):
    server.script.chat_body = {"choices": [{"message": {"content": "sure, ok!"}}]}
    provider = _provider(server.base_url)

    result = await pt._json_output(provider, "sk-test", "vendor/model-a")

    assert result.ok is False
    assert "not valid JSON" in result.detail


@pytest.mark.asyncio
async def test_json_stage_fails_when_the_shape_does_not_match(server):
    server.script.chat_body = {"choices": [{"message": {"content": '{"status": "fine"}'}}]}
    provider = _provider(server.base_url)

    result = await pt._json_output(provider, "sk-test", "vendor/model-a")

    assert result.ok is False


@pytest.mark.asyncio
async def test_run_model_test_stops_after_a_failed_chat_stage(server):
    server.script.chat_body = {"choices": [{"message": {"content": "nope"}}]}
    provider = _provider(server.base_url)

    results = await pt.run_model_test(provider, "sk-test", "vendor/model-a")

    assert [r.stage for r in results] == ["chat"]
    assert results[0].ok is False


@pytest.mark.asyncio
async def test_run_model_test_stops_after_a_failed_tool_call_stage(server):
    server.script.chat_bodies = [
        {"choices": [{"message": {"content": "RAP_OK"}}]},  # chat: pass
        {"choices": [{"message": {"content": "no tool for you"}}]},  # tool call: fail
    ]
    provider = _provider(server.base_url)

    results = await pt.run_model_test(provider, "sk-test", "vendor/model-a")

    assert [r.stage for r in results] == ["chat", "tool_call"]
    assert results[0].ok is True
    assert results[1].ok is False


@pytest.mark.asyncio
async def test_run_model_test_passes_all_three_stages(server):
    server.script.chat_bodies = [
        {"choices": [{"message": {"content": "RAP_OK"}}]},
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {"function": {"name": "get_time", "arguments": '{"timezone": "UTC"}'}}
                        ]
                    }
                }
            ]
        },
        {"choices": [{"message": {"content": '{"ok": true}'}}]},
    ]
    provider = _provider(server.base_url)

    results = await pt.run_model_test(provider, "sk-test", "vendor/model-a")

    assert [r.stage for r in results] == ["chat", "tool_call", "json"]
    assert all(r.ok for r in results)


@pytest.mark.asyncio
async def test_a_slow_server_times_out_rather_than_hanging(server, monkeypatch):
    server.script.delay_secs = 0.3
    monkeypatch.setattr(pt, "CHAT_TIMEOUT_SECS", 0.05)
    provider = _provider(server.base_url)

    result = await pt._chat(provider, "sk-test", "vendor/model-a")

    assert result.ok is False
