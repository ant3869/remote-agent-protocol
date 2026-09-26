"""The provider registry: which endpoints RAP knows about, and which model
each role should try, in what order. It holds no secrets -- `secret_store`
does that -- and it is schema-versioned and atomically written like the
other on-disk stores (`conversation_hub/store.py`, `job_store.py`).
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from remote_agent_protocol import model_providers as mp


@pytest.fixture
def registry(tmp_path):
    return mp.ProviderRegistry(tmp_path / "model_providers.json")


def _provider(provider_id="openrouter", **overrides) -> mp.ProviderConfig:
    fields = {
        "id": provider_id,
        "preset": "openrouter",
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "auth": "bearer",
        "extra_headers": {"X-Title": "Remote Agent Protocol"},
        "enabled": True,
        "created_at": 1.0,
        "updated_at": 2.0,
    }
    fields.update(overrides)
    return mp.ProviderConfig(**fields)


def test_slug_id_derives_a_lowercase_dashed_id_from_a_label():
    assert mp.slug_id("OpenRouter", existing=[]) == "openrouter"
    assert mp.slug_id("9Router", existing=[]) == "9router"
    assert mp.slug_id("My Custom Provider!", existing=[]) == "my-custom-provider"


def test_slug_id_disambiguates_against_existing_ids():
    assert mp.slug_id("OpenRouter", existing=["openrouter"]) == "openrouter-2"
    assert mp.slug_id("OpenRouter", existing=["openrouter", "openrouter-2"]) == "openrouter-3"


def test_provider_config_has_no_secret_bearing_field():
    names = {f.name for f in dataclasses.fields(mp.ProviderConfig)}
    assert names.isdisjoint({"api_key", "key", "token", "secret", "password"})


def test_provider_round_trips_through_save_and_load(registry):
    registry.upsert_provider(_provider())
    registry.save()

    reloaded = mp.ProviderRegistry(registry.path)
    reloaded.load()

    provider = reloaded.get_provider("openrouter")
    assert provider == _provider()


def test_loading_drops_stray_secret_shaped_keys(registry):
    registry.upsert_provider(_provider())
    registry.save()
    payload = json.loads(registry.path.read_text(encoding="utf-8"))
    payload["providers"][0]["api_key"] = "sk-should-never-load"
    payload["providers"][0]["token"] = "also-should-never-load"
    registry.path.write_text(json.dumps(payload), encoding="utf-8")

    reloaded = mp.ProviderRegistry(registry.path)
    reloaded.load()

    provider = reloaded.get_provider("openrouter")
    assert not hasattr(provider, "api_key")
    assert not hasattr(provider, "token")


def test_save_writes_a_temp_file_then_replaces_atomically(registry, monkeypatch):
    calls = []
    real_replace = __import__("os").replace

    def spy_replace(src, dst):
        calls.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(mp.os, "replace", spy_replace)
    registry.upsert_provider(_provider())
    registry.save()

    assert calls == [(str(registry.temp_path), str(registry.path))]
    assert not registry.temp_path.exists()
    assert registry.path.exists()


def test_a_failed_replace_leaves_the_previous_file_untouched(registry, monkeypatch):
    registry.upsert_provider(_provider())
    registry.save()
    original_bytes = registry.path.read_bytes()

    registry.upsert_provider(_provider(label="changed"))

    def boom(src, dst):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(mp.os, "replace", boom)
    with pytest.raises(OSError):
        registry.save()

    assert registry.path.read_bytes() == original_bytes


def test_catalog_cache_round_trips(registry):
    registry.upsert_provider(_provider())
    registry.set_catalog("openrouter", ["vendor/model-a", "vendor/model-b"], fetched_at=123.0)
    registry.save()

    reloaded = mp.ProviderRegistry(registry.path)
    reloaded.load()

    catalog = reloaded.get_catalog("openrouter")
    assert catalog.models == ("vendor/model-a", "vendor/model-b")
    assert catalog.fetched_at == 123.0


def test_provider_test_results_round_trip(registry):
    registry.upsert_provider(_provider())
    results = [
        mp.TestResult(stage="reach", ok=True, latency_ms=12.5, tested_at=10.0),
        mp.TestResult(stage="authenticate", ok=True, latency_ms=45.0, tested_at=10.0),
    ]
    registry.record_provider_test("openrouter", results)
    registry.save()

    reloaded = mp.ProviderRegistry(registry.path)
    reloaded.load()

    assert reloaded.get_provider_test("openrouter") == tuple(results)


def test_model_test_results_round_trip_per_provider_and_model(registry):
    registry.upsert_provider(_provider())
    results = [mp.TestResult(stage="chat", ok=True, latency_ms=200.0, tested_at=5.0)]
    registry.record_model_test("openrouter", "vendor/model-a", results)
    registry.save()

    reloaded = mp.ProviderRegistry(registry.path)
    reloaded.load()

    assert reloaded.get_model_test("openrouter", "vendor/model-a") == tuple(results)
    assert reloaded.get_model_test("openrouter", "vendor/model-b") == ()


def test_list_model_tests_returns_every_cached_result(registry):
    registry.upsert_provider(_provider())
    registry.record_model_test("openrouter", "vendor/a", [mp.TestResult(stage="chat", ok=True)])
    registry.record_model_test("openrouter", "vendor/b", [mp.TestResult(stage="chat", ok=False)])

    listed = {(pid, model): results for pid, model, results in registry.list_model_tests()}

    assert set(listed) == {("openrouter", "vendor/a"), ("openrouter", "vendor/b")}
    assert listed[("openrouter", "vendor/a")][0].ok is True


def test_role_chain_round_trips_in_order(registry):
    chain = [
        mp.RoleChainEntry(provider_id="openrouter", model="vendor/model-a"),
        mp.RoleChainEntry(provider_id="nine_router", model="cc/claude-thing"),
    ]
    registry.set_role_chain("butler", chain)
    registry.save()

    reloaded = mp.ProviderRegistry(registry.path)
    reloaded.load()

    assert reloaded.get_role_chain("butler") == tuple(chain)
    assert reloaded.get_role_chain("intent") == ()


def test_set_role_chain_rejects_an_unknown_role(registry):
    with pytest.raises(ValueError):
        registry.set_role_chain("not-a-role", [])


def test_delete_provider_clears_its_catalog_and_test_results(registry):
    registry.upsert_provider(_provider())
    registry.set_catalog("openrouter", ["vendor/model-a"])
    registry.record_provider_test("openrouter", [mp.TestResult(stage="reach", ok=True)])
    registry.record_model_test(
        "openrouter", "vendor/model-a", [mp.TestResult(stage="chat", ok=True)]
    )

    assert registry.delete_provider("openrouter") is True

    assert registry.get_provider("openrouter") is None
    assert registry.get_catalog("openrouter") is None
    assert registry.get_provider_test("openrouter") == ()
    assert registry.get_model_test("openrouter", "vendor/model-a") == ()


def test_delete_provider_reports_false_when_nothing_to_delete(registry):
    assert registry.delete_provider("nothing-here") is False


def test_providers_referenced_by_roles(registry):
    registry.upsert_provider(_provider())
    registry.set_role_chain("butler", [mp.RoleChainEntry(provider_id="openrouter", model="m")])
    registry.set_role_chain("intent", [mp.RoleChainEntry(provider_id="openrouter", model="m")])

    referenced = registry.providers_referenced_by_roles("openrouter")

    assert set(referenced) == {"butler", "intent"}
    assert registry.providers_referenced_by_roles("unused-provider") == ()


def test_loading_a_missing_file_leaves_the_registry_empty(tmp_path):
    registry = mp.ProviderRegistry(tmp_path / "does-not-exist.json")
    registry.load()
    assert registry.list_providers() == ()


def test_loading_an_unreadable_file_does_not_crash(registry):
    registry.path.parent.mkdir(parents=True, exist_ok=True)
    registry.path.write_text("{not valid json", encoding="utf-8")
    registry.load()  # must not raise
    assert registry.list_providers() == ()


def test_loading_a_newer_schema_is_ignored_not_crashed(registry):
    registry.path.parent.mkdir(parents=True, exist_ok=True)
    registry.path.write_text(
        json.dumps({"schema_version": mp.SCHEMA_VERSION + 1, "providers": []}), encoding="utf-8"
    )
    registry.load()  # must not raise
    assert registry.list_providers() == ()


def test_env_import_is_available_when_the_registry_is_empty_and_env_is_set(registry):
    assert mp.env_import_available(registry, base_url="https://x/v1", api_key="") is True
    assert mp.env_import_available(registry, base_url="", api_key="sk-or-x") is True


def test_env_import_is_unavailable_with_nothing_configured(registry):
    assert mp.env_import_available(registry, base_url="", api_key="") is False


def test_env_import_is_unavailable_once_a_provider_exists(registry):
    registry.upsert_provider(_provider())
    assert mp.env_import_available(registry, base_url="https://x/v1", api_key="sk-y") is False


def test_import_from_env_creates_an_openrouter_provider_and_role_chains(registry):
    result = mp.import_from_env(
        registry,
        base_url=mp.PRESETS["openrouter"].base_url,
        api_key="sk-or-legacy",
        brain_model="vendor/brain-model",
        intent_model="vendor/intent-model",
    )

    assert result is not None
    config, key = result
    assert config.preset == "openrouter"
    assert key == "sk-or-legacy"
    assert registry.get_provider(config.id) == config
    assert [e.model for e in registry.get_role_chain("butler")] == ["vendor/brain-model"]
    assert [e.model for e in registry.get_role_chain("intent")] == ["vendor/intent-model"]
    # orchestration wasn't given its own model, so it falls back to the shared brain model,
    # matching the legacy _cloud_model() behavior this import is standing in for.
    assert [e.model for e in registry.get_role_chain("orchestration")] == ["vendor/brain-model"]


def test_import_from_env_uses_the_custom_preset_for_a_non_openrouter_base_url(registry):
    result = mp.import_from_env(
        registry, base_url="https://elsewhere.test/v1", api_key="sk-x", brain_model="m"
    )

    assert result is not None
    config, _key = result
    assert config.preset == "custom"


def test_import_from_env_is_a_no_op_once_the_registry_is_non_empty(registry):
    registry.upsert_provider(_provider())

    result = mp.import_from_env(registry, base_url="https://x/v1", api_key="sk-x", brain_model="m")

    assert result is None
    assert len(registry.list_providers()) == 1


def test_import_from_env_is_a_no_op_without_a_base_url(registry):
    result = mp.import_from_env(registry, base_url="", api_key="sk-x", brain_model="m")
    assert result is None


@pytest.mark.parametrize(
    ("preset_id", "expected_base_url"),
    [
        ("openrouter", "https://openrouter.ai/api/v1"),
        ("nine_router", "http://localhost:20128/v1"),
        ("deepseek", "https://api.deepseek.com"),
        ("together", "https://api.together.ai/v1"),
        ("mistral", "https://api.mistral.ai/v1"),
        ("groq", "https://api.groq.com/openai/v1"),
        ("xai", "https://api.x.ai/v1"),
        ("anthropic", "https://api.anthropic.com/v1"),
        ("gemini", "https://generativelanguage.googleapis.com/v1beta/openai"),
        ("ollama", "http://127.0.0.1:11434/v1"),
        ("lm_studio", "http://127.0.0.1:1234/v1"),
    ],
)
def test_preset_base_urls_match_the_verified_docs(preset_id, expected_base_url):
    assert mp.PRESETS[preset_id].base_url == expected_base_url
