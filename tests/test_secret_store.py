"""Where a provider's API key actually lives, and what never happens to it.

Windows Credential Manager is the only production backend. There is no
plain-text fallback: if it isn't available, saving fails loudly rather than
writing the key to disk. Tests inject an in-memory backend instead of talking
to the real Credential Manager.
"""

from __future__ import annotations

import pytest

from remote_agent_protocol import secret_store


@pytest.fixture(autouse=True)
def fake_backend():
    """Every test gets its own in-memory backend, never the real one."""
    backend = secret_store.InMemoryBackend()
    secret_store.use_backend(backend)
    secret_store._known_secrets.clear()
    yield backend
    secret_store.use_backend(None)
    secret_store._known_secrets.clear()


def test_set_get_roundtrip():
    secret_store.set_key("openrouter", "sk-or-abcd1234")
    assert secret_store.get_key("openrouter") == "sk-or-abcd1234"


def test_has_key_reflects_presence():
    assert secret_store.has_key("openrouter") is False
    secret_store.set_key("openrouter", "sk-or-abcd1234")
    assert secret_store.has_key("openrouter") is True


def test_delete_removes_the_key():
    secret_store.set_key("openrouter", "sk-or-abcd1234")
    assert secret_store.delete_key("openrouter") is True
    assert secret_store.get_key("openrouter") is None
    assert secret_store.has_key("openrouter") is False


def test_delete_of_a_missing_key_reports_false_not_an_error():
    assert secret_store.delete_key("nothing-here") is False


def test_providers_are_stored_independently():
    secret_store.set_key("openrouter", "sk-or-aaaa")
    secret_store.set_key("nine-router", "sk-9r-bbbb")
    assert secret_store.get_key("openrouter") == "sk-or-aaaa"
    assert secret_store.get_key("nine-router") == "sk-9r-bbbb"


def test_masked_shows_only_the_last_four_characters():
    secret_store.set_key("openrouter", "sk-or-abcd1234")
    assert secret_store.masked("openrouter") == "••••1234"


def test_masked_hides_a_short_key_entirely():
    secret_store.set_key("local", "abc")
    assert secret_store.masked("local") == "••••"


def test_masked_is_none_when_nothing_is_stored():
    assert secret_store.masked("openrouter") is None


def test_the_full_key_is_never_in_the_masked_string():
    secret_store.set_key("openrouter", "sk-or-super-secret-value")
    assert "super-secret-value" not in secret_store.masked("openrouter")


def test_redact_strips_a_tracked_key_out_of_arbitrary_text():
    secret_store.set_key("openrouter", "sk-or-super-secret-value")
    message = "request failed with key sk-or-super-secret-value attached"
    assert "sk-or-super-secret-value" not in secret_store.redact(message)
    assert "[REDACTED]" in secret_store.redact(message)


def test_get_key_also_tracks_the_value_for_redaction():
    """The dominant real path is: save once, then only ever get_key() on later
    starts. Redaction must cover that path, not just the value passed to set_key.
    """
    backend = secret_store.InMemoryBackend()
    backend.set(secret_store._target("openrouter"), "sk-or-loaded-from-store")
    secret_store.use_backend(backend)

    secret_store.get_key("openrouter")

    assert "sk-or-loaded-from-store" not in secret_store.redact(
        "used key sk-or-loaded-from-store just now"
    )


def test_redact_leaves_unrelated_text_untouched():
    secret_store.set_key("openrouter", "sk-or-super-secret-value")
    assert secret_store.redact("nothing sensitive here") == "nothing sensitive here"


def test_install_log_redaction_scrubs_loguru_output(capsys):
    secret_store.set_key("openrouter", "sk-or-super-secret-value")
    secret_store.install_log_redaction()
    try:
        from loguru import logger

        logger.remove()
        logger.add(lambda msg: print(msg, end=""))
        logger.info("provider call used key sk-or-super-secret-value")
        captured = capsys.readouterr()
        assert "sk-or-super-secret-value" not in captured.out
        assert "[REDACTED]" in captured.out
    finally:
        from loguru import logger

        logger.configure(patcher=lambda record: None)


def test_saving_fails_clearly_when_no_secure_backend_is_available(monkeypatch):
    monkeypatch.setattr(secret_store, "win32cred", None)
    monkeypatch.setattr(secret_store, "_backend", None)
    secret_store.use_backend(None)  # no test override either: exercise the real resolver
    try:
        with pytest.raises(secret_store.SecretBackendUnavailable):
            secret_store.set_key("openrouter", "sk-or-abcd1234")
    finally:
        secret_store.use_backend(secret_store.InMemoryBackend())
