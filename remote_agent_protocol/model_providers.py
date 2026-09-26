"""Which OpenAI-compatible endpoints RAP knows about, and which model each role should try, in what order.

This registry holds configuration only -- base URLs, presets, cached model
catalogs, test results, and role chains. It never holds an API key; that is
`secret_store`'s job alone, so that this file's on-disk JSON can be read,
diagnosed, or shipped in a bug report without ever leaking one.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

SCHEMA_VERSION = 1
_SLUG_INVALID = re.compile(r"[^a-z0-9]+")

ROLES: tuple[str, ...] = ("butler", "intent", "orchestration", "narration")
AuthKind = Literal["bearer", "none"]

_SECRET_FIELD_NAMES = frozenset({"api_key", "key", "token", "secret", "password"})


@dataclass(frozen=True)
class PresetInfo:
    """A provider preset: base URL, auth style, and where to find things.

    Every base URL and path here was checked against the provider's current
    docs at implementation time (see the plan file); ``verified=False`` marks
    the rare one that couldn't be confirmed rather than guessed.
    """

    id: str
    label: str
    base_url: str
    auth: AuthKind
    models_path: str = "/models"
    key_info_path: str = ""
    verified: bool = True
    notes: str = ""


PRESETS: dict[str, PresetInfo] = {
    preset.id: preset
    for preset in (
        PresetInfo(
            id="openrouter",
            label="OpenRouter",
            base_url="https://openrouter.ai/api/v1",
            auth="bearer",
            key_info_path="/key",
            notes=(
                "Send HTTP-Referer and X-Title headers. Model IDs look like "
                "vendor/model; catalog filtering by key access is not guaranteed."
            ),
        ),
        PresetInfo(
            id="nine_router",
            label="9Router",
            base_url="http://localhost:20128/v1",
            auth="bearer",
            notes=(
                "Local router; GET /models returns models and combos. Unreachable "
                "usually means 9Router isn't running. Dashboard at "
                "http://localhost:20128/dashboard."
            ),
        ),
        PresetInfo(
            id="openai",
            label="OpenAI",
            base_url="https://api.openai.com/v1",
            auth="bearer",
        ),
        PresetInfo(
            id="anthropic",
            label="Anthropic (OpenAI-compatible)",
            base_url="https://api.anthropic.com/v1",
            auth="bearer",
            notes=(
                "OpenAI SDK compatibility layer; model IDs are Claude model names. "
                "Tool-call support must pass the model test."
            ),
        ),
        PresetInfo(
            id="gemini",
            label="Google Gemini (OpenAI-compatible)",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            auth="bearer",
        ),
        PresetInfo(id="xai", label="xAI", base_url="https://api.x.ai/v1", auth="bearer"),
        PresetInfo(
            id="groq",
            label="Groq",
            base_url="https://api.groq.com/openai/v1",
            auth="bearer",
        ),
        PresetInfo(
            id="deepseek",
            label="DeepSeek",
            base_url="https://api.deepseek.com",
            auth="bearer",
        ),
        PresetInfo(
            id="mistral",
            label="Mistral",
            base_url="https://api.mistral.ai/v1",
            auth="bearer",
        ),
        PresetInfo(
            id="together",
            label="Together",
            base_url="https://api.together.ai/v1",
            auth="bearer",
            notes="Model IDs follow <provider>/<model_name>.",
        ),
        PresetInfo(
            id="ollama",
            label="Ollama (local)",
            base_url="http://127.0.0.1:11434/v1",
            auth="none",
        ),
        PresetInfo(
            id="lm_studio",
            label="LM Studio (local)",
            base_url="http://127.0.0.1:1234/v1",
            auth="none",
        ),
        PresetInfo(id="custom", label="Custom", base_url="", auth="bearer"),
    )
}


def slug_id(label: str, existing: Iterable[str]) -> str:
    """A short, JSON-key-safe provider id derived from a label, unique against ``existing``."""
    base = _SLUG_INVALID.sub("-", label.strip().lower()).strip("-") or "provider"
    existing_set = set(existing)
    if base not in existing_set:
        return base
    suffix = 2
    while f"{base}-{suffix}" in existing_set:
        suffix += 1
    return f"{base}-{suffix}"


@dataclass(frozen=True)
class ProviderConfig:
    """One OpenAI-compatible endpoint RAP knows about. Never holds a key."""

    id: str
    preset: str
    label: str
    base_url: str
    auth: AuthKind = "bearer"
    extra_headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """This provider's config as JSON-serialisable data. No secrets."""
        return {
            "id": self.id,
            "preset": self.preset,
            "label": self.label,
            "base_url": self.base_url,
            "auth": self.auth,
            "extra_headers": dict(self.extra_headers),
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ProviderConfig:
        """Rebuild a provider from persisted data, dropping any secret-shaped field."""
        cleaned = {k: v for k, v in payload.items() if k not in _SECRET_FIELD_NAMES}
        headers = cleaned.get("extra_headers")
        if not isinstance(headers, dict):
            headers = {}
        auth = cleaned.get("auth")
        return cls(
            id=str(cleaned.get("id", "")),
            preset=str(cleaned.get("preset", "custom")),
            label=str(cleaned.get("label", "")),
            base_url=str(cleaned.get("base_url", "")),
            auth=auth if auth in ("bearer", "none") else "bearer",
            extra_headers={str(k): str(v) for k, v in headers.items()},
            enabled=bool(cleaned.get("enabled", True)),
            created_at=float(cleaned.get("created_at", 0.0) or 0.0),
            updated_at=float(cleaned.get("updated_at", 0.0) or 0.0),
        )


@dataclass(frozen=True)
class ModelCatalog:
    """A provider's cached model catalog."""

    models: tuple[str, ...] = ()
    fetched_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """This catalog as JSON-serialisable data."""
        return {"models": list(self.models), "fetched_at": self.fetched_at}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ModelCatalog:
        """Rebuild a catalog from persisted data."""
        models = payload.get("models")
        if not isinstance(models, list):
            models = []
        return cls(
            models=tuple(str(m) for m in models if isinstance(m, str)),
            fetched_at=float(payload.get("fetched_at", 0.0) or 0.0),
        )


@dataclass(frozen=True)
class TestResult:
    """One stage's result from the provider/model test pipeline."""

    stage: str
    ok: bool
    latency_ms: float = 0.0
    detail: str = ""
    hint: str = ""
    tested_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """This result as JSON-serialisable data."""
        return {
            "stage": self.stage,
            "ok": self.ok,
            "latency_ms": self.latency_ms,
            "detail": self.detail,
            "hint": self.hint,
            "tested_at": self.tested_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TestResult:
        """Rebuild a result from persisted data."""
        return cls(
            stage=str(payload.get("stage", "")),
            ok=bool(payload.get("ok", False)),
            latency_ms=float(payload.get("latency_ms", 0.0) or 0.0),
            detail=str(payload.get("detail", "")),
            hint=str(payload.get("hint", "")),
            tested_at=float(payload.get("tested_at", 0.0) or 0.0),
        )


@dataclass(frozen=True)
class RoleChainEntry:
    """One hop in a role's ordered fallback chain."""

    provider_id: str
    model: str

    def to_dict(self) -> dict[str, Any]:
        """This chain entry as JSON-serialisable data."""
        return {"provider_id": self.provider_id, "model": self.model}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RoleChainEntry:
        """Rebuild a chain entry from persisted data."""
        return cls(
            provider_id=str(payload.get("provider_id", "")),
            model=str(payload.get("model", "")),
        )


class ProviderRegistry:
    """Providers, their cached catalogs and test results, and role chains.

    Persisted to a schema-versioned JSON file via temp-file-then-``os.replace``,
    the same pattern ``conversation_hub/store.py`` uses. Holds no secrets.
    """

    def __init__(self, path: str | Path):
        """Bind the registry to an on-disk JSON path; call ``load()`` to read it."""
        self.path = Path(path)
        self.temp_path = self.path.with_name(f"{self.path.name}.tmp")
        self._providers: dict[str, ProviderConfig] = {}
        self._catalogs: dict[str, ModelCatalog] = {}
        self._provider_tests: dict[str, tuple[TestResult, ...]] = {}
        self._model_tests: dict[tuple[str, str], tuple[TestResult, ...]] = {}
        self._role_chains: dict[str, tuple[RoleChainEntry, ...]] = dict.fromkeys(ROLES, ())

    def load(self) -> None:
        """Restore state from disk.

        A missing, unreadable, or newer-schema file leaves the registry empty
        rather than raising -- the caller starts fresh.
        """
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        if payload.get("schema_version") != SCHEMA_VERSION:
            return
        self._load_providers(payload)
        self._load_catalogs(payload)
        self._load_provider_tests(payload)
        self._load_model_tests(payload)
        self._load_role_chains(payload)

    def _load_providers(self, payload: dict[str, Any]) -> None:
        providers = payload.get("providers")
        if not isinstance(providers, list):
            return
        for item in providers:
            if isinstance(item, dict) and item.get("id"):
                config = ProviderConfig.from_dict(item)
                self._providers[config.id] = config

    def _load_catalogs(self, payload: dict[str, Any]) -> None:
        catalogs = payload.get("catalogs")
        if not isinstance(catalogs, dict):
            return
        for provider_id, item in catalogs.items():
            if isinstance(item, dict):
                self._catalogs[provider_id] = ModelCatalog.from_dict(item)

    def _load_provider_tests(self, payload: dict[str, Any]) -> None:
        provider_tests = payload.get("provider_tests")
        if not isinstance(provider_tests, dict):
            return
        for provider_id, rows in provider_tests.items():
            if isinstance(rows, list):
                self._provider_tests[provider_id] = tuple(
                    TestResult.from_dict(row) for row in rows if isinstance(row, dict)
                )

    def _load_model_tests(self, payload: dict[str, Any]) -> None:
        model_tests = payload.get("model_tests")
        if not isinstance(model_tests, dict):
            return
        for key, rows in model_tests.items():
            if not isinstance(rows, list) or "::" not in key:
                continue
            provider_id, model = key.split("::", 1)
            self._model_tests[(provider_id, model)] = tuple(
                TestResult.from_dict(row) for row in rows if isinstance(row, dict)
            )

    def _load_role_chains(self, payload: dict[str, Any]) -> None:
        role_chains = payload.get("role_chains")
        if not isinstance(role_chains, dict):
            return
        for role, rows in role_chains.items():
            if role in ROLES and isinstance(rows, list):
                self._role_chains[role] = tuple(
                    RoleChainEntry.from_dict(row) for row in rows if isinstance(row, dict)
                )

    def save(self) -> None:
        """Write state to a temp sibling file, then atomically replace the real one."""
        payload = {
            "schema_version": SCHEMA_VERSION,
            "providers": [p.to_dict() for p in self._providers.values()],
            "catalogs": {pid: c.to_dict() for pid, c in self._catalogs.items()},
            "provider_tests": {
                pid: [r.to_dict() for r in rows] for pid, rows in self._provider_tests.items()
            },
            "model_tests": {
                f"{pid}::{model}": [r.to_dict() for r in rows]
                for (pid, model), rows in self._model_tests.items()
            },
            "role_chains": {
                role: [e.to_dict() for e in chain] for role, chain in self._role_chains.items()
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.temp_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        os.replace(self.temp_path, self.path)

    # --- providers ---

    def upsert_provider(self, config: ProviderConfig) -> None:
        """Add or replace a provider by id."""
        self._providers[config.id] = config

    def get_provider(self, provider_id: str) -> ProviderConfig | None:
        """The provider by id, or None if it isn't registered."""
        return self._providers.get(provider_id)

    def list_providers(self) -> tuple[ProviderConfig, ...]:
        """All registered providers."""
        return tuple(self._providers.values())

    def delete_provider(self, provider_id: str) -> bool:
        """Remove a provider and its cached catalog/test results.

        The caller is responsible for deleting the stored key (`secret_store`)
        and for refusing the delete while a role chain still references it.
        """
        self._catalogs.pop(provider_id, None)
        self._provider_tests.pop(provider_id, None)
        for key in [k for k in self._model_tests if k[0] == provider_id]:
            self._model_tests.pop(key, None)
        return self._providers.pop(provider_id, None) is not None

    def providers_referenced_by_roles(self, provider_id: str) -> tuple[str, ...]:
        """Which roles' chains currently reference this provider."""
        return tuple(
            role
            for role, chain in self._role_chains.items()
            if any(entry.provider_id == provider_id for entry in chain)
        )

    # --- model catalogs ---

    def set_catalog(
        self, provider_id: str, models: list[str], *, fetched_at: float | None = None
    ) -> None:
        """Cache a provider's model catalog."""
        self._catalogs[provider_id] = ModelCatalog(
            models=tuple(models),
            fetched_at=time.time() if fetched_at is None else fetched_at,
        )

    def get_catalog(self, provider_id: str) -> ModelCatalog | None:
        """A provider's cached catalog, or None if it has never been fetched."""
        return self._catalogs.get(provider_id)

    # --- test results ---

    def record_provider_test(self, provider_id: str, results: list[TestResult]) -> None:
        """Cache a provider test run's stage results."""
        self._provider_tests[provider_id] = tuple(results)

    def get_provider_test(self, provider_id: str) -> tuple[TestResult, ...]:
        """A provider's last test run, or an empty tuple if never tested."""
        return self._provider_tests.get(provider_id, ())

    def record_model_test(self, provider_id: str, model: str, results: list[TestResult]) -> None:
        """Cache a model test run's stage results."""
        self._model_tests[(provider_id, model)] = tuple(results)

    def get_model_test(self, provider_id: str, model: str) -> tuple[TestResult, ...]:
        """A model's last test run, or an empty tuple if never tested."""
        return self._model_tests.get((provider_id, model), ())

    def list_model_tests(self) -> tuple[tuple[str, str, tuple[TestResult, ...]], ...]:
        """Every cached model test result, as ``(provider_id, model, results)``."""
        return tuple(
            (provider_id, model, results)
            for (provider_id, model), results in self._model_tests.items()
        )

    # --- role chains ---

    def get_role_chain(self, role: str) -> tuple[RoleChainEntry, ...]:
        """The ordered fallback chain assigned to a role, empty if unassigned."""
        return self._role_chains.get(role, ())

    def set_role_chain(self, role: str, chain: list[RoleChainEntry]) -> None:
        """Replace a role's fallback chain."""
        if role not in ROLES:
            raise ValueError(f"unknown role: {role}")
        self._role_chains[role] = tuple(chain)
