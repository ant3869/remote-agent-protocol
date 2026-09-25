# Design: Model Providers and Role Assignment

**Product:** Remote Agent Protocol
**Roadmap:** `docs/notes/grok-style-orchestration-roadmap.md`, prerequisite for Phase C (tool-calling Butler)
**Status:** Proposed for user review
**Date:** 2026-09-25

## Product outcome

Ant opens **Settings → Models & providers**, adds a provider such as OpenRouter or 9Router, pastes an API key, and presses **Test**. RAP confirms it can reach the provider, lists the models the key can use, and checks whether a chosen model handles chat, tool calls, and JSON output, with measured latency for each. He then assigns models to RAP's roles (Butler, intent, orchestration, narration), each with an ordered fallback, and the change applies on the next turn without a restart. Keys never show up in a file, a log, the transcript, or the UI after they're saved.

## Current state

- `llm_endpoint.py` resolves three callers (`BRAIN`, `INTENT`, `ORCHESTRATION`) against **one** cloud endpoint built from `CLOUD_LLM_BASE_URL` / `CLOUD_LLM_API_KEY` / `CLOUD_LLM_MODEL` (and `CLOUD_INTENT_MODEL`, `CLOUD_ORCHESTRATION_MODEL`), with `OPENROUTER_API_KEY` as a shortcut (`config.py` ~L277–324).
- `fetch_cloud_models()` reads `GET {base}/models` for that single endpoint, best effort. `web_gui.py` merges the result into the model picker every 300 s.
- All of this is configured only in `.env`. Changing it means a restart, and the key sits in plain text.
- There's no connectivity test, no capability check, and no way to hold more than one provider at a time.
- `NARRATION_MODEL` is local-only (it defaults to `INTENT_MODEL`).
- `pywin32` is already in `.venv`, so `win32cred` (Windows Credential Manager) is available without adding a dependency.

## Goals

1. Hold any number of OpenAI-compatible providers at once, with presets for the common ones and first-class **OpenRouter** and **9Router**.
2. Store API keys in Windows Credential Manager, never in `.env`, JSON, logs, or API responses.
3. Test a provider in explicit stages and report the result for each stage in plain words.
4. Fetch and cache each provider's model catalog, with search/filter in the UI.
5. Test a specific model for chat, tool calling, and structured JSON, with latency, because Phase C needs a model that does reliable tool calls.
6. Assign a provider+model with an ordered fallback chain to each role: **Butler** (persona now, tool loop in Phase C), **Intent**, **Orchestration**, **Narration**.
7. Apply assignments live, starting with the next turn.
8. Keep backward compatibility: existing `CLOUD_*` / `OPENROUTER_API_KEY` env configs keep working unchanged.

## Non-goals

- Changing which model an **agent harness** (Hermes, OpenClaw, …) uses. That's roadmap Phase A2, which may later reuse this registry.
- Cost dashboards or spend limits beyond what a provider's key-info endpoint returns.
- Non-OpenAI-compatible SDK paths (native Anthropic Messages, native Gemini). Everything goes through `/chat/completions`.
- Exposing providers to other devices. The control center stays loopback plus CSRF.

## Provider presets

The preset fills in the base URL, auth style, and hints. Everything stays editable, and **Custom** takes any base URL. **At implementation time, verify every base URL and the models/key-info paths against the provider's current docs, and fix the preset table before writing tests. If a preset can't be verified, ship it marked "unverified" instead of guessing.**

| Preset | Default base URL | Key | Models list | Notes |
|---|---|---|---|---|
| **OpenRouter** | `https://openrouter.ai/api/v1` | required | `GET /models` (public; filter by key access not guaranteed) | Key info: `GET /key` (limit/usage), verify the path. Model IDs look like `vendor/model`. Send `HTTP-Referer` and `X-Title: Remote Agent Protocol` headers |
| **9Router** | `http://localhost:20128/v1` | required (from the 9Router dashboard) | `GET /models` returns models **and combos** | Local router. IDs look like `provider/model` (e.g. `cc/claude-…`) or a combo name. Unreachable usually means 9Router isn't running, so say that. Dashboard at `http://localhost:20128/dashboard` |
| OpenAI | `https://api.openai.com/v1` | required | `GET /models` | |
| Anthropic (OpenAI-compatible) | `https://api.anthropic.com/v1` | required | verify | OpenAI SDK compatibility layer. Tool-call support must pass the model test |
| Google Gemini (OpenAI-compatible) | `https://generativelanguage.googleapis.com/v1beta/openai` | required | `GET /models` | |
| xAI | `https://api.x.ai/v1` | required | `GET /models` | |
| Groq | `https://api.groq.com/openai/v1` | required | `GET /models` | |
| DeepSeek | `https://api.deepseek.com/v1` | required | `GET /models` | |
| Mistral | `https://api.mistral.ai/v1` | required | `GET /models` | |
| Together | `https://api.together.xyz/v1` | required | `GET /models` | |
| Ollama (local) | `http://127.0.0.1:11434/v1` | none | `GET /models` | Existing local path, shown as a provider for uniformity |
| LM Studio (local) | `http://127.0.0.1:1234/v1` | none | `GET /models` | |
| Custom | user-entered | optional | `GET /models` (optional) | |

## Architecture

### `model_providers.py` (new, app package)

- `ProviderConfig`, a frozen dataclass: `id` (slug), `preset`, `label`, `base_url`, `auth` (`bearer` | `none`), `extra_headers`, `enabled`, `created_at`, `updated_at`.
- `ProviderRegistry`: loads and saves `data/model_providers.json` (schema-versioned, atomic temp-file + swap like the other stores). It holds **no secrets**. It also holds the model catalog cache per provider (`models`, `fetched_at`) and the last test results per provider and per model.
- `RoleAssignment`: for each role in {`butler`, `intent`, `orchestration`, `narration`}, an ordered list of `(provider_id, model)`. The local Ollama entry can appear anywhere in a chain, so "local last" is data, not a hard-coded rule.

### `secret_store.py` (new)

- `set_key(provider_id, key)`, `get_key(provider_id)`, `delete_key(provider_id)`, `has_key(provider_id)`, `masked(provider_id)` (returns `••••abcd`).
- On Windows: `win32cred` generic credentials, target `RAP/model-provider/<id>`.
- Fallback when `win32cred` isn't available (tests or another OS): an in-memory store for tests only. Production never falls back to plain-text storage. If the store isn't available, saving fails with a clear message.
- Keys are never logged. Add a logging filter that redacts any stored key value if it ever ends up in a message.

### `llm_endpoint.py` changes

- Resolution order for a role: that role's `RoleAssignment` chain, then (if empty) the legacy `CLOUD_*` env endpoint exactly as today, then local Ollama if `CLOUD_LLM_LOCAL_FALLBACK`.
- `Endpoint` gains `provider_id` and `extra_headers`. The `headers` property merges bearer and extra headers. The key comes from `secret_store` at call time and isn't cached on the frozen dataclass or anywhere else in memory.
- Add `NARRATION` as a fourth caller. `narration.py` resolves through it, and when nothing is assigned it defaults to today's local behavior.
- Existing request-shape rules are unchanged: Ollama extras go only to local endpoints, cloud JSON schema travels as `response_format`, `CLOUD_LLM_MAX_TOKENS` still caps cloud requests, and the persona falls back only before its first token.

### Test pipeline (`provider_tests.py`, new)

Each stage returns `{stage, ok, latency_ms, detail, hint}`. A stage runs only if the one before it passed. Everything is bounded by per-stage timeouts, and results are cached in the registry with a timestamp.

**Provider test** (Test button on a provider):
1. **Reach**: open a TCP/HTTP connection to the base URL. On failure: "Can't reach localhost:20128 — is 9Router running?" or a DNS/proxy message.
2. **Authenticate**: `GET /models` with the key (or the OpenRouter key-info call). 401/403 → "Key rejected". Anything else non-2xx → the status and the first 200 characters of the body, secrets redacted.
3. **Catalog**: parse the model IDs, store them with a count, and show "412 models" (or "0 models" plus the hint "enter a model ID manually").

**Model test** (Test button on a model row, and automatically when a model is assigned to a role):
1. **Chat**: a minimal completion ("Reply with exactly: RAP_OK", `max_tokens` 16). Record time to first byte and total time. Check that the text contains `RAP_OK`.
2. **Tool call**: one tool, `get_time(timezone: string)`, with a prompt that requires calling it. Pass means `tool_calls[0].function.name == "get_time"` and the arguments parse as JSON. **Required for the Butler role once Phase C lands.** Until then it's a warning, not a block.
3. **JSON**: `response_format` with a tiny schema, `{"ok": boolean}`. Pass means it parses and validates. Required for the Intent role.
4. Overall result: a badge row (`chat ✓ · tools ✓ · json ✓ · 1.2 s`).

Tests cost a few tokens per run. The UI says so, and nothing re-runs automatically more than once per assignment change.

### Web API (existing CSRF'd handler)

- `GET /api/providers` returns providers (no keys; `has_key` and `key_masked` only), cached catalogs (IDs only, count, `fetched_at`), test results, role assignments, and presets.
- `POST /api/action` gains the following actions, validated server-side:
  - `provider_save {id?, preset, label, base_url, auth, extra_headers, api_key?}`. The key is optional on edit, and an empty value keeps the existing one.
  - `provider_delete {id}` deletes the credential too, and is refused while a role chain references the provider unless `force`.
  - `provider_test {id}` and `model_test {provider_id, model}` run asynchronously and push results over the existing event stream.
  - `provider_refresh_models {id}`.
  - `role_assign {role, chain: [{provider_id, model}]}` validates that each provider exists and is enabled. It applies live on the next turn and emits a `routing`-style `model_assignment` event.
- The key never appears in any response, event, diagnostics bundle (the EXPORT button), or `/api/status`.

### UI (Settings → new section "Models & providers")

- Add a new settings nav button, "Models & providers", between General and Voice, following the existing `data-settings-section` pattern and graphite styling. Dark theme only.
- **Providers list:** a card per provider with its label, preset icon, base URL, masked key, last test result (colored dot plus text), model count, and Test / Refresh / Edit / Delete buttons.
- **Add provider:** preset select → base URL (prefilled) → key (password input with show-while-held) → Save & Test. The staged results appear inline as a checklist.
- **Model browser:** pick a provider, then a searchable list (the catalogs can be large) with a manual "enter model ID" field. Each row has a Test button and capability badges once tested.
- **Role assignment:** four rows (Butler, Intent, Orchestration, Narration), each an ordered chain editor (add from the browser, reorder, remove), plus the current live endpoint for that role and its last latency.
- **Status view:** the existing "Persona orchestration" panel shows which provider and model actually answered each role on the last turn, and whether it fell back.
- Command palette entries: "Test providers" and "Open models & providers".

### Migration

On first run with an empty registry, if `CLOUD_LLM_BASE_URL` or `OPENROUTER_API_KEY` is set, show a one-time "Import from .env" action. It creates the provider, moves the key into Credential Manager, and copies the role models. It **doesn't** edit `.env`. It tells Ant which lines he can delete, and until he does the env values stay as the legacy fallback.

## Error handling

- Every network call has a timeout and runs off the voice loop.
- Provider errors are classified (unreachable / auth / quota / model not found / rate limit / malformed) with a one-line hint. The raw body is kept only in debug logs, redacted.
- A role whose whole chain fails falls back as it does today and says so in the Status panel. It never fails silently.

## Testing

- Unit: registry load/save/schema/atomicity. Secret store via an injected fake (no real Credential Manager in CI). Endpoint resolution order (role chain → legacy env → local). Header merge. Redaction filter.
- Test pipeline against a local fake OpenAI-compatible HTTP server fixture: reach failure, 401, empty catalog, chat pass/fail, tool-call pass/fail/malformed args, JSON pass/fail, and timeouts.
- API: key never present in `/api/providers`, events, `/api/status`, or the diagnostics export. Also CSRF enforcement and validation errors.
- UI: `tests/test_web_gui.py` markup assertions for the new section. A node test for the chain-editor pure functions.
- Parity: brain mode and full mode resolve the same endpoints (`test_conversation_mode_parity.py` stays green).

## Acceptance

1. Add OpenRouter with a real key. Test shows reach ✓, auth ✓, and a catalog count.
2. Add 9Router with 9Router stopped. Test shows "Can't reach localhost:20128 — is 9Router running?". Start it, re-test, and the models and combos are listed.
3. Test a model: chat, tools, and JSON badges with latency.
4. Assign Butler = OpenRouter model A → 9Router combo B → local. The next spoken turn uses A, and the Status panel shows it.
5. Revoke or break key A. The next turn falls back to B, and Status shows the fallback and the reason.
6. Search the repo, `data/`, logs, and the diagnostics export for the key string: zero hits.
