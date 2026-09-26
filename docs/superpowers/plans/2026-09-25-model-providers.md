# Plan: Model Providers and Role Assignment (Phase C0)

**Spec:** `docs/superpowers/specs/2026-09-25-model-providers-design.md`
**Branch:** `feat/c0-model-providers` (worktree `../rap-c0-providers`)
**Tests:** `.venv\Scripts\python -m pytest tests/<file>` · lint: `.venv\Scripts\python -m ruff check remote_agent_protocol tests`

## Preset verification (done before Task 1)

Checked each base URL against current provider docs (web fetch/search) on 2026-09-25:

| Preset | Spec draft | Verified | Source |
|---|---|---|---|
| OpenRouter | `https://openrouter.ai/api/v1` | **matches** | openrouter.ai/docs/api-reference/overview — chat endpoint is `/api/v1/chat/completions` |
| OpenRouter key info | `GET /key` | **matches** | openrouter.ai/docs/api-reference/limits — `GET /api/v1/key` (relative to base: `/key`) |
| 9Router | `http://localhost:20128/v1`, `GET /v1/models` | **kept as given** | no public docs; per user instruction directly |
| OpenAI | `https://api.openai.com/v1` | **matches** | well-established, not re-fetched (low risk, unauthenticated GET /models is standard) |
| Anthropic (OpenAI-compat) | `https://api.anthropic.com/v1` | **matches** | docs.anthropic.com/en/api/openai-sdk — `base_url="https://api.anthropic.com/v1/"`. Note: a personal/service-account key scoped to multiple workspaces additionally needs an `anthropic-workspace-id` header — out of scope for now, `extra_headers` on `ProviderConfig` already covers it if ever needed |
| Gemini (OpenAI-compat) | `https://generativelanguage.googleapis.com/v1beta/openai` | **matches** | ai.google.dev/gemini-api/docs/openai — `base_url="https://generativelanguage.googleapis.com/v1beta/openai/"` |
| xAI | `https://api.x.ai/v1` | **matches** | docs.x.ai/docs/api-reference |
| Groq | `https://api.groq.com/openai/v1` | **matches** | console.groq.com/docs/openai (via web search, direct fetch 403'd) |
| DeepSeek | `https://api.deepseek.com/v1` | **CORRECTED → `https://api.deepseek.com`** | api-docs.deepseek.com — current quick-start uses `base_url="https://api.deepseek.com"`, no `/v1` |
| Mistral | `https://api.mistral.ai/v1` | **matches** | docs.mistral.ai/api — `POST /v1/chat/completions` off `https://api.mistral.ai` |
| Together | `https://api.together.xyz/v1` | **CORRECTED → `https://api.together.ai/v1`** | docs.together.ai/docs/openai-api-compatibility — current docs use the `.ai` domain, not `.xyz` |
| Ollama (local) | `http://127.0.0.1:11434/v1` | **matches** existing `cfg.OLLAMA_BASE_URL` (`http://localhost:11434/v1`) | config.py:265-266 |
| LM Studio (local) | `http://127.0.0.1:1234/v1` | **matches** | lmstudio.ai/docs/developer/openai-compat |

Deviation from spec is limited to the two corrected base URLs above; `model_providers.py`'s preset table uses the corrected values, not the spec draft's.

## Deviations from spec (tracked as we go — appended per task if code disagrees with the spec's "current state")

- **Task 4 fixture path.** The plan named `tests/fixtures/fake_openai_server.py`; it lives at `tests/fake_openai_server.py` instead, matching this repo's actual convention for a shared test-only helper module (`tests/aic_mocks.py` is flat, not under a `fixtures/` subfolder, and `tests/` has no `fixtures/` package today).
- **Task 3 registry test-isolation.** Beyond the plan's explicit tests, added `cfg.MODEL_PROVIDERS_PATH` (mirroring the existing `CONVERSATION_STORE_PATH` pattern) plus a `tests/conftest.py` redirect, so `llm_endpoint.get_registry()`'s process-wide singleton never touches real `data/model_providers.json` during a test run — the same problem `CONVERSATION_STORE_PATH` already exists to solve for the conversation store.

## Tasks

### 1. `secret_store.py` — Windows Credential Manager key storage
**Files:** `remote_agent_protocol/secret_store.py` (new), `tests/test_secret_store.py` (new)
**Tests first:**
- `set_key`/`get_key`/`delete_key`/`has_key` round-trip against an injected fake backend (no real `win32cred` in CI)
- `masked()` returns `••••<last4>` and never the full key
- a logging filter (or redaction helper) strips a known key value out of an arbitrary log message
- production path (no fake backend, `win32cred` unavailable) raises/fails clearly rather than falling back to plain text
**Verify:** `.venv\Scripts\python -m pytest tests/test_secret_store.py`

### 2. `model_providers.py` — provider registry (no secrets)
**Files:** `remote_agent_protocol/model_providers.py` (new), `tests/test_model_providers.py` (new)
**Tests first:**
- `ProviderConfig` round-trips through the registry's JSON (save/load), schema-versioned like `conversation_hub/store.py`
- atomic write: temp-file-then-`os.replace` sequence, mirroring the existing store pattern
- registry never serializes an api key field (there isn't one on `ProviderConfig`; assert the dataclass has no such field, and that stray `api_key`/`key`/`token` payload keys are dropped on load)
- catalog cache (`models`, `fetched_at`) and last test results persist and reload per provider
- `RoleAssignment` chains: ordered `(provider_id, model)` list per role, round-trip
- preset table pins the two corrected URLs above (regression test)
**Verify:** `.venv\Scripts\python -m pytest tests/test_model_providers.py`

### 3. `llm_endpoint.py` — role-chain resolution + `NARRATION` caller
**Files:** `remote_agent_protocol/llm_endpoint.py`, `remote_agent_protocol/narration.py`, `tests/test_llm_endpoint.py`, `tests/test_narration.py`
**Tests first (extend `tests/test_llm_endpoint.py`):**
- resolution order for a role: registry chain (if non-empty) → legacy `CLOUD_*` env exactly as today → local Ollama per `CLOUD_LLM_LOCAL_FALLBACK`
- `Endpoint.provider_id` / `Endpoint.extra_headers`; `headers` property merges bearer (fetched from `secret_store` at call time, never cached on the frozen dataclass) + `extra_headers`
- existing tests in the file (cloud-first, local-fallback-last, OpenRouter shortcut, request-shape caps) stay green unchanged — the parity guard for "existing CLOUD_*/OPENROUTER_API_KEY behavior keeps working unchanged when no roles are assigned"
- `NARRATION` caller: `chain(NARRATION)` falls back to today's local behavior (resident classifier model) when nothing is assigned; resolves through a role chain when one exists
**Verify:** `.venv\Scripts\python -m pytest tests/test_llm_endpoint.py tests/test_narration.py tests/test_conversation_mode_parity.py tests/test_brain_adapter_orchestration.py`

### 4. `provider_tests.py` — staged test pipeline + fake HTTP fixture
**Files:** `remote_agent_protocol/provider_tests.py` (new), `tests/fixtures/fake_openai_server.py` (new), `tests/test_provider_tests.py` (new)
**Tests first:**
- fixture: a local `http.server`-based fake OpenAI-compatible server (threaded, ephemeral port) with switchable behavior (reach failure via unused port, 401, empty/populated `/models`, chat pass/fail, tool-call pass/malformed-args, JSON pass/fail, artificial delay for timeout)
- provider test: reach → authenticate → catalog, each stage only running if the prior passed, each returns `{stage, ok, latency_ms, detail, hint}`
- model test: chat (`RAP_OK` check) → tool call (`get_time` args parse as JSON) → JSON (`response_format` schema validates)
- timeouts on every stage; results cached on the registry with a timestamp
**Verify:** `.venv\Scripts\python -m pytest tests/test_provider_tests.py`

### 5. Web API actions
**Files:** `remote_agent_protocol/web_gui.py`, `tests/test_web_gui.py`
**Tests first:**
- `GET /api/providers`: no key material anywhere in the payload (`has_key`/`key_masked` only), catalogs as IDs+count+`fetched_at`, test results, role assignments, presets
- `provider_save`/`provider_delete` (refused while referenced by a role chain unless `force`)/`provider_test`/`model_test`/`provider_refresh_models`/`role_assign`, each validated server-side, each CSRF-gated like the existing `/api/action` handler
- **security test**: grep the full JSON of `/api/providers`, `/api/status`, the event stream, and the diagnostics export for a sentinel key string saved via `provider_save` — zero hits (spec acceptance criterion #6, made a hard CI gate)
**Verify:** `.venv\Scripts\python -m pytest tests/test_web_gui.py`

### 6. Settings UI — "Models & providers" section
**Files:** `remote_agent_protocol/web_app/index.html`, `remote_agent_protocol/web_app/app.js`, `tests/test_web_gui.py` (markup assertions)
**Tests first:**
- markup assertions: new nav button follows `data-settings-section` pattern, section renders provider cards, add-provider form fields, model browser, 4 role rows, status view
- pure-function test for the chain editor (add/reorder/remove) if it's written as a standalone function
**Verify:** `.venv\Scripts\python -m pytest tests/test_web_gui.py`

### 7. `.env` import migration
**Files:** `remote_agent_protocol/model_providers.py` (migration helper), `tests/test_model_providers.py`
**Tests first:**
- empty registry + `CLOUD_LLM_BASE_URL` or `OPENROUTER_API_KEY` set → one-time import creates a provider, moves the key to the (fake, injected) secret store, copies role models
- `.env` itself is never written
- import is a no-op once the registry is non-empty (not re-offered every run)
**Verify:** `.venv\Scripts\python -m pytest tests/test_model_providers.py`

### 8. Docs + CHANGELOG
**Files:** `docs/architecture.md`, `CHANGELOG.md`
- Short section in `architecture.md` describing the provider registry / secret store / role-chain resolution.
- `CHANGELOG.md` `[Unreleased]`: new feature entry.
**Verify:** none (docs only); re-run the full touched-test list below.

## Final verification (before writing progress notes)

```
.venv\Scripts\python -m pytest tests/test_secret_store.py tests/test_model_providers.py tests/test_llm_endpoint.py tests/test_narration.py tests/test_provider_tests.py tests/test_web_gui.py tests/test_brain_adapter_orchestration.py tests/test_conversation_mode_parity.py tests/test_app_config.py
.venv\Scripts\python -m ruff check remote_agent_protocol tests
```

Then `docs/notes/2026-09-25-c0-progress.md` per the brief, commit, stop.
