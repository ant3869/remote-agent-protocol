"""orchestration/providers/{local,copilot}.py -- no real network, no real CLI.

LocalProvider is exercised against a fake aiohttp-shaped session (same style
as tests/test_intent_router.py's _FakeSession/_FakeResp). CopilotProvider is
exercised against a fake ``copilot.CopilotClient``-shaped object injected via
``client_factory`` -- the real ``github-copilot-sdk`` package is never
imported, launched, or billed for anything here.
"""

import unittest

from remote_agent_protocol.orchestration.providers.base import ModelCapability
from remote_agent_protocol.orchestration.providers.copilot import (
    CopilotProvider,
    CopilotUnavailable,
)
from remote_agent_protocol.orchestration.providers.local import LocalProvider


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"status {self.status}")

    async def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, get_payload=None, post_payload=None, fail=False):
        self._get_payload = get_payload or {"models": [{"name": "qwen2.5:3b"}]}
        self._post_payload = post_payload or {"message": {"content": "ok"}}
        self._fail = fail

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url):
        if self._fail:
            return _FakeResp({}, status=500)
        return _FakeResp(self._get_payload)

    def post(self, url, json=None):
        if self._fail:
            return _FakeResp({}, status=500)
        return _FakeResp(self._post_payload)


class LocalProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_reports_available_when_ollama_reachable(self):
        provider = LocalProvider(session_factory=lambda: _FakeSession())
        health = await provider.health()
        self.assertTrue(health.available)
        self.assertTrue(health.authenticated)

    async def test_health_reports_unavailable_on_failure(self):
        provider = LocalProvider(session_factory=lambda: _FakeSession(fail=True))
        health = await provider.health()
        self.assertFalse(health.available)

    async def test_list_models_falls_back_to_configured_model_on_error(self):
        provider = LocalProvider(
            model="qwen2.5:3b", session_factory=lambda: _FakeSession(fail=True)
        )
        self.assertEqual(await provider.list_models(), ["qwen2.5:3b"])

    async def test_complete_returns_message_content(self):
        provider = LocalProvider(
            session_factory=lambda: _FakeSession(post_payload={"message": {"content": "hermes"}})
        )
        result = await provider.complete("pick a harness")
        self.assertEqual(result.text, "hermes")

    async def test_quota_is_always_none(self):
        provider = LocalProvider(session_factory=lambda: _FakeSession())
        self.assertIsNone(await provider.quota())


class _FakeAssistantMessageData:
    """Mirrors copilot.generated.session_events.AssistantMessageData's shape
    (only the field CopilotProvider actually reads)."""

    def __init__(self, content):
        self.content = content


class _FakeSessionEvent:
    """Mirrors copilot.generated.session_events.SessionEvent's shape --
    confirmed live against the installed github-copilot-sdk 1.0.13 that
    send_and_wait() returns one of these, not a plain string."""

    def __init__(self, content):
        self.data = _FakeAssistantMessageData(content)


class _FakeCopilotSession:
    def __init__(self, reply="hermes", as_event=True):
        # Real send_and_wait() returns a SessionEvent wrapping the text in
        # .data.content (as_event=True, the default and realistic case);
        # as_event=False exercises _extract_text's plain-string fallback.
        self._reply = _FakeSessionEvent(reply) if as_event else reply

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send_and_wait(self, prompt):
        return self._reply


class _FakeCopilotClient:
    """Stands in for copilot.CopilotClient: async context manager with
    list_models()/create_session(), matching the SDK's documented shape."""

    def __init__(self, models=None, reply="hermes", raise_on_list_models=None):
        self._models = models if models is not None else ["gpt-5", "auto"]
        self._reply = reply
        self._raise_on_list_models = raise_on_list_models

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def list_models(self):
        if self._raise_on_list_models:
            raise self._raise_on_list_models
        return self._models

    async def create_session(self, **kwargs):
        return _FakeCopilotSession(self._reply)


class CopilotProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_authenticated_when_client_reachable(self):
        provider = CopilotProvider(client_factory=lambda: _FakeCopilotClient())
        health = await provider.health()
        self.assertTrue(health.available)
        self.assertTrue(health.authenticated)

    async def test_health_reports_not_authenticated_on_auth_error(self):
        provider = CopilotProvider(
            client_factory=lambda: _FakeCopilotClient(
                raise_on_list_models=RuntimeError("401 unauthorized: run copilot auth login")
            )
        )
        health = await provider.health()
        self.assertFalse(health.available)
        self.assertFalse(health.authenticated)

    async def test_health_reports_missing_sdk_without_raising(self):
        def factory():
            raise ModuleNotFoundError("no module named copilot")

        provider = CopilotProvider(client_factory=factory)
        health = await provider.health()
        self.assertFalse(health.available)
        self.assertIn("github-copilot-sdk", health.detail)

    async def test_complete_returns_session_reply(self):
        # _FakeCopilotClient wraps the reply in the realistic SessionEvent
        # shape by default -- this is the real send_and_wait() return shape.
        provider = CopilotProvider(
            client_factory=lambda: _FakeCopilotClient(reply="code-puppy"),
            permission_handler="approve-all-sentinel",
        )
        result = await provider.complete("pick a harness", capability=ModelCapability.REASONING)
        self.assertEqual(result.text, "code-puppy")
        self.assertEqual(result.model, "gpt-5")  # doc-confirmed default for REASONING

    async def test_complete_falls_back_to_plain_string_reply(self):
        class _PlainStringClient(_FakeCopilotClient):
            async def create_session(self, **kwargs):
                return _FakeCopilotSession(self._reply, as_event=False)

        provider = CopilotProvider(
            client_factory=lambda: _PlainStringClient(reply="hermes"),
            permission_handler="sentinel",
        )
        result = await provider.complete("pick a harness")
        self.assertEqual(result.text, "hermes")

    async def test_complete_raises_copilot_unavailable_on_session_failure(self):
        class _FailingClient(_FakeCopilotClient):
            async def create_session(self, **kwargs):
                raise RuntimeError("CLI crashed")

        provider = CopilotProvider(
            client_factory=lambda: _FailingClient(), permission_handler="sentinel"
        )
        with self.assertRaises(CopilotUnavailable):
            await provider.complete("pick a harness")

    async def test_quota_is_always_none(self):
        provider = CopilotProvider(client_factory=lambda: _FakeCopilotClient())
        self.assertIsNone(await provider.quota())

    async def test_model_map_override_respected(self):
        provider = CopilotProvider(
            client_factory=lambda: _FakeCopilotClient(reply="x"),
            model_map={"reasoning": "gpt-5.1"},
            permission_handler="sentinel",
        )
        result = await provider.complete("pick", capability=ModelCapability.REASONING)
        self.assertEqual(result.model, "gpt-5.1")


if __name__ == "__main__":
    unittest.main()
