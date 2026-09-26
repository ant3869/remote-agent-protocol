"""Staged connectivity, auth, and capability tests for a model provider.

Two pipelines, each a short sequence of independent HTTP calls: a provider
test (reach -> authenticate -> catalog) run from the provider's own Test
button, and a model test (chat -> tool call -> JSON) run from a model row's
Test button, or automatically when a model is assigned to a role. In both,
a stage runs only if the one before it passed, and every stage carries its
own timeout -- a test must never hang the settings UI, let alone the voice
loop this process also runs.
"""

from __future__ import annotations

import asyncio
import json
import time
from urllib.parse import urlsplit

import aiohttp

from remote_agent_protocol import secret_store
from remote_agent_protocol.model_providers import PRESETS, PresetInfo, ProviderConfig, TestResult

REACH_TIMEOUT_SECS = 5.0
AUTH_TIMEOUT_SECS = 8.0
CATALOG_TIMEOUT_SECS = 8.0
CHAT_TIMEOUT_SECS = 15.0
TOOL_CALL_TIMEOUT_SECS = 15.0
JSON_TIMEOUT_SECS = 15.0

_BODY_PREVIEW_CHARS = 200
_CHAT_PROMPT = "Reply with exactly: RAP_OK"
_CHAT_MAX_TOKENS = 16
_CHAT_MARKER = "RAP_OK"

_GET_TIME_TOOL = {
    "type": "function",
    "function": {
        "name": "get_time",
        "description": "Get the current time in a given timezone.",
        "parameters": {
            "type": "object",
            "properties": {"timezone": {"type": "string"}},
            "required": ["timezone"],
        },
    },
}

_JSON_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "ok_check",
        "schema": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
    },
}


def _now_ms() -> float:
    return time.monotonic() * 1000


def _headers(provider: ProviderConfig, api_key: str) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    headers.update(provider.extra_headers)
    return headers


def _preset_for(provider: ProviderConfig) -> PresetInfo:
    return PRESETS.get(provider.preset, PRESETS["custom"])


async def _reach(provider: ProviderConfig, preset: PresetInfo) -> TestResult:
    started = _now_ms()
    parts = urlsplit(provider.base_url)
    host = parts.hostname
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if not host:
        return TestResult(stage="reach", ok=False, detail=f"Invalid base URL: {provider.base_url}")
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=REACH_TIMEOUT_SECS
        )
        writer.close()
    except (OSError, TimeoutError) as exc:
        hint = f"Is {preset.label} running?" if preset.id == "nine_router" else ""
        return TestResult(
            stage="reach",
            ok=False,
            latency_ms=_now_ms() - started,
            detail=f"Can't reach {host}:{port}: {exc}",
            hint=hint,
        )
    return TestResult(stage="reach", ok=True, latency_ms=_now_ms() - started)


async def _authenticate(provider: ProviderConfig, preset: PresetInfo, api_key: str) -> TestResult:
    started = _now_ms()
    path = preset.key_info_path or preset.models_path
    url = f"{provider.base_url.rstrip('/')}{path}"
    timeout = aiohttp.ClientTimeout(total=AUTH_TIMEOUT_SECS)
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as http,
            http.get(url, headers=_headers(provider, api_key)) as resp,
        ):
            if resp.status in (401, 403):
                return TestResult(
                    stage="authenticate",
                    ok=False,
                    latency_ms=_now_ms() - started,
                    detail="Key rejected",
                    hint="Check the API key.",
                )
            if resp.status >= 400:
                body = await resp.text()
                return TestResult(
                    stage="authenticate",
                    ok=False,
                    latency_ms=_now_ms() - started,
                    detail=f"HTTP {resp.status}: {secret_store.redact(body[:_BODY_PREVIEW_CHARS])}",
                )
    except (TimeoutError, aiohttp.ClientError) as exc:
        return TestResult(
            stage="authenticate", ok=False, latency_ms=_now_ms() - started, detail=str(exc)
        )
    return TestResult(stage="authenticate", ok=True, latency_ms=_now_ms() - started)


async def _catalog(
    provider: ProviderConfig, preset: PresetInfo, api_key: str
) -> tuple[TestResult, list[str]]:
    started = _now_ms()
    url = f"{provider.base_url.rstrip('/')}{preset.models_path}"
    timeout = aiohttp.ClientTimeout(total=CATALOG_TIMEOUT_SECS)
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as http,
            http.get(url, headers=_headers(provider, api_key)) as resp,
        ):
            if resp.status >= 400:
                body = await resp.text()
                return (
                    TestResult(
                        stage="catalog",
                        ok=False,
                        latency_ms=_now_ms() - started,
                        detail=f"HTTP {resp.status}: "
                        f"{secret_store.redact(body[:_BODY_PREVIEW_CHARS])}",
                    ),
                    [],
                )
            payload = await resp.json()
    except (TimeoutError, aiohttp.ClientError, ValueError) as exc:
        return (
            TestResult(stage="catalog", ok=False, latency_ms=_now_ms() - started, detail=str(exc)),
            [],
        )
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    ids = sorted(
        {row.get("id", "").strip() for row in rows if isinstance(row, dict) and row.get("id")}
    )
    hint = "" if ids else "Enter a model ID manually."
    return (
        TestResult(
            stage="catalog",
            ok=True,
            latency_ms=_now_ms() - started,
            detail=f"{len(ids)} models",
            hint=hint,
        ),
        ids,
    )


async def run_provider_test(
    provider: ProviderConfig, api_key: str
) -> tuple[list[TestResult], list[str]]:
    """Reach, then authenticate, then catalog -- each gated by the one before it."""
    preset = _preset_for(provider)
    results = [await _reach(provider, preset)]
    if not results[-1].ok:
        return results, []
    results.append(await _authenticate(provider, preset, api_key))
    if not results[-1].ok:
        return results, []
    catalog_result, models = await _catalog(provider, preset, api_key)
    results.append(catalog_result)
    return results, models


async def _post_chat(
    provider: ProviderConfig, api_key: str, payload: dict, *, timeout_secs: float, stage: str
) -> tuple[TestResult | None, dict | None]:
    """POST to ``/chat/completions``; returns (error result, None) or (None, body)."""
    url = f"{provider.base_url.rstrip('/')}/chat/completions"
    started = _now_ms()
    timeout = aiohttp.ClientTimeout(total=timeout_secs)
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as http,
            http.post(url, json=payload, headers=_headers(provider, api_key)) as resp,
        ):
            if resp.status >= 400:
                body = await resp.text()
                return (
                    TestResult(
                        stage=stage,
                        ok=False,
                        latency_ms=_now_ms() - started,
                        detail=f"HTTP {resp.status}: "
                        f"{secret_store.redact(body[:_BODY_PREVIEW_CHARS])}",
                    ),
                    None,
                )
            data = await resp.json()
    except (TimeoutError, aiohttp.ClientError) as exc:
        return (
            TestResult(stage=stage, ok=False, latency_ms=_now_ms() - started, detail=str(exc)),
            None,
        )
    return None, data


async def _chat(provider: ProviderConfig, api_key: str, model: str) -> TestResult:
    started = _now_ms()
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": _CHAT_PROMPT}],
        "max_tokens": _CHAT_MAX_TOKENS,
    }
    error, data = await _post_chat(
        provider, api_key, payload, timeout_secs=CHAT_TIMEOUT_SECS, stage="chat"
    )
    if error is not None:
        return error
    try:
        text = str(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as exc:
        return TestResult(stage="chat", ok=False, latency_ms=_now_ms() - started, detail=str(exc))
    ok = _CHAT_MARKER in text
    return TestResult(
        stage="chat",
        ok=ok,
        latency_ms=_now_ms() - started,
        detail="" if ok else text.strip()[:_BODY_PREVIEW_CHARS],
        hint="" if ok else f"Expected the reply to contain {_CHAT_MARKER}.",
    )


async def _tool_call(provider: ProviderConfig, api_key: str, model: str) -> TestResult:
    started = _now_ms()
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": "What time is it in Tokyo? Use the get_time tool."}
        ],
        "tools": [_GET_TIME_TOOL],
        "tool_choice": "required",
        "max_tokens": 128,
    }
    error, data = await _post_chat(
        provider, api_key, payload, timeout_secs=TOOL_CALL_TIMEOUT_SECS, stage="tool_call"
    )
    if error is not None:
        return error
    try:
        calls = data["choices"][0]["message"].get("tool_calls") or []
    except (KeyError, IndexError, TypeError) as exc:
        return TestResult(
            stage="tool_call", ok=False, latency_ms=_now_ms() - started, detail=str(exc)
        )
    if not calls:
        return TestResult(
            stage="tool_call",
            ok=False,
            latency_ms=_now_ms() - started,
            detail="The model did not call a tool.",
        )
    function = calls[0].get("function") or {}
    if function.get("name") != "get_time":
        return TestResult(
            stage="tool_call",
            ok=False,
            latency_ms=_now_ms() - started,
            detail=f"Called {function.get('name')!r} instead of get_time.",
        )
    try:
        json.loads(function.get("arguments") or "{}")
    except ValueError:
        return TestResult(
            stage="tool_call",
            ok=False,
            latency_ms=_now_ms() - started,
            detail="The tool call's arguments were not valid JSON.",
        )
    return TestResult(stage="tool_call", ok=True, latency_ms=_now_ms() - started)


async def _json_output(provider: ProviderConfig, api_key: str, model: str) -> TestResult:
    started = _now_ms()
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": 'Reply with {"ok": true} and nothing else.'}],
        "response_format": _JSON_SCHEMA,
        "max_tokens": 32,
    }
    error, data = await _post_chat(
        provider, api_key, payload, timeout_secs=JSON_TIMEOUT_SECS, stage="json"
    )
    if error is not None:
        return error
    try:
        text = str(data["choices"][0]["message"]["content"])
        parsed = json.loads(text)
    except (KeyError, IndexError, TypeError) as exc:
        return TestResult(stage="json", ok=False, latency_ms=_now_ms() - started, detail=str(exc))
    except ValueError:
        return TestResult(
            stage="json",
            ok=False,
            latency_ms=_now_ms() - started,
            detail="The reply was not valid JSON.",
        )
    if not isinstance(parsed, dict) or not isinstance(parsed.get("ok"), bool):
        return TestResult(
            stage="json",
            ok=False,
            latency_ms=_now_ms() - started,
            detail='The reply did not match {"ok": boolean}.',
        )
    return TestResult(stage="json", ok=True, latency_ms=_now_ms() - started)


async def run_model_test(provider: ProviderConfig, api_key: str, model: str) -> list[TestResult]:
    """Chat, then tool call, then JSON -- each gated by the one before it.

    A tool-call or JSON failure does not undo the chat result: a model that
    only chats is still useful for a role that never needs the other two.
    The gating just means there is nothing more specific to report once an
    earlier stage has already failed.
    """
    results = [await _chat(provider, api_key, model)]
    if not results[-1].ok:
        return results
    results.append(await _tool_call(provider, api_key, model))
    if not results[-1].ok:
        return results
    results.append(await _json_output(provider, api_key, model))
    return results
