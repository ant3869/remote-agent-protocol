"""One Butler turn: the model talks, calls tools, reads their results, and talks again.

Streams over any OpenAI-compatible ``/chat/completions`` endpoint. Text is
yielded as it arrives so speech can start before the turn is over. A failure
before anything happened (nothing spoken, no tool run) raises
``ButlerUnavailable`` so the caller can use its fallback path; a failure after
that point cannot be retried elsewhere without repeating side effects, so the
turn ends with a sentence built from the tool results that did come back.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

import aiohttp
from loguru import logger

from remote_agent_protocol import llm_endpoint
from remote_agent_protocol.butler.tools import TOOL_SCHEMAS, ButlerToolbox

ToolListener = Callable[[str, dict, dict], None]


class ButlerUnavailable(RuntimeError):
    """No endpoint could run the turn before it had any effect."""


@dataclass
class _Round:
    text: str = ""
    tool_calls: dict[int, dict] = field(default_factory=dict)
    _slots: dict[int, int] = field(default_factory=dict)  # provider index -> our slot
    _last_slot: int | None = None

    def add_tool_delta(self, delta: dict) -> None:
        """Fold one streamed tool-call fragment into the call it belongs to.

        Providers differ: some number parallel calls 0, 1, 2; some send every
        call as index 0 with a fresh id; some omit the index on continuation
        fragments. A new id always starts a new call, and a fragment without
        an index or id continues the latest one.
        """
        call_id = delta.get("id") or ""
        raw_index = delta.get("index")
        slot = self._slots.get(int(raw_index)) if raw_index is not None else self._last_slot
        current = self.tool_calls.get(slot) if slot is not None else None
        if current is None or (call_id and current["id"] and call_id != current["id"]):
            slot = len(self.tool_calls)
            current = self.tool_calls[slot] = {"id": "", "name": "", "arguments": ""}
            if raw_index is not None:
                self._slots[int(raw_index)] = slot
        self._last_slot = slot
        current["id"] = call_id or current["id"]
        function = delta.get("function") or {}
        current["name"] += function.get("name") or ""
        current["arguments"] += function.get("arguments") or ""


class ButlerLoop:
    """Runs tool-calling turns for the Butler."""

    def __init__(
        self,
        *,
        toolbox: ButlerToolbox,
        endpoints: Callable[[], tuple[llm_endpoint.Endpoint, ...]],
        http: Callable[[], aiohttp.ClientSession | None],
        max_rounds: int = 5,
        max_tokens: int = 600,
        timeout_secs: float = 60.0,
        on_tool: ToolListener | None = None,
    ):
        """Initialize the loop.

        Args:
            toolbox: Executes the model's tool calls.
            endpoints: The Butler role's endpoints, in the order to try them.
            http: The session's shared HTTP client (None before start()).
            max_rounds: Tool-calling rounds before the model must answer in words.
            max_tokens: Output cap per model call.
            timeout_secs: Per-call timeout.
            on_tool: Called with (name, arguments, result) after each tool runs.
        """
        self._toolbox = toolbox
        self._endpoints = endpoints
        self._http = http
        self._max_rounds = max(1, max_rounds)
        self._max_tokens = max_tokens
        self._timeout_secs = timeout_secs
        self._on_tool = on_tool

    async def run(
        self, messages: list[dict], allowed_tools: frozenset[str] | None = None
    ) -> AsyncIterator[str]:
        """Yield the reply's text for ``messages`` (system first), running tools as asked.

        ``allowed_tools`` narrows what the model is offered and may run; a call
        to anything else comes back as an error result instead of executing.
        """
        schemas = [
            schema
            for schema in TOOL_SCHEMAS
            if allowed_tools is None or schema["function"]["name"] in allowed_tools
        ]
        endpoints = self._endpoints()
        if not endpoints:
            raise ButlerUnavailable("no model endpoint is configured for the Butler")
        conversation = list(messages)
        results: list[dict] = []
        spoke = False
        last_char = ""
        endpoint_index = 0
        round_number = 0
        while round_number < self._max_rounds:
            endpoint = endpoints[endpoint_index]
            final_round = round_number == self._max_rounds - 1
            current = _Round()
            try:
                async for delta in self._stream_round(
                    endpoint, conversation, current, final_round, schemas
                ):
                    # Text from an earlier round ("I'll check now, sir.") and
                    # this round's answer arrive as separate streams; keep a
                    # space between them so they don't run together.
                    first_of_round = current.text == delta
                    if (
                        first_of_round
                        and last_char
                        and not last_char.isspace()
                        and not delta[:1].isspace()
                    ):
                        yield " "
                    spoke = True
                    last_char = delta[-1:] or last_char
                    yield delta
            except Exception as exc:  # noqa: BLE001 - provider failures vary widely
                if not spoke and not results and endpoint_index + 1 < len(endpoints):
                    logger.warning(
                        f"Butler model {endpoint.label} failed ({exc}); "
                        f"trying {endpoints[endpoint_index + 1].label}"
                    )
                    endpoint_index += 1
                    continue
                if not spoke and not results:
                    raise ButlerUnavailable(f"{endpoint.label}: {exc}") from exc
                logger.warning(f"Butler model {endpoint.label} failed mid-turn ({exc})")
                yield _fallback_sentence(results)
                return
            llm_endpoint.record_answer(llm_endpoint.BRAIN, endpoint)
            if not current.tool_calls:
                if not current.text.strip():
                    yield (
                        _fallback_sentence(results)
                        if results
                        else "Sorry, I lost my train of thought."
                    )
                return
            calls = [current.tool_calls[i] for i in sorted(current.tool_calls)]
            for index, call in enumerate(calls):
                call["id"] = call["id"] or f"call_{round_number}_{index}"
                # Providers validate the history they are sent: echoing a
                # call whose arguments aren't a JSON object back to them
                # fails the whole turn. The tool still sees the raw text and
                # reports the problem to the model as its result.
                call["history_arguments"] = _normalized_arguments(call["arguments"])
            conversation.append(
                {
                    "role": "assistant",
                    "content": current.text or None,
                    "tool_calls": [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": call["history_arguments"],
                            },
                        }
                        for call in calls
                    ],
                }
            )
            for call in calls:
                if allowed_tools is not None and call["name"] not in allowed_tools:
                    result = {
                        "error": f"{call['name']} is not available right now.",
                        "summary": f"{call['name']} is not available right now.",
                    }
                else:
                    result = await self._toolbox.call(call["name"], call["arguments"] or "{}")
                results.append(result)
                if self._on_tool is not None:
                    try:
                        self._on_tool(call["name"], _safe_json(call["arguments"]), result)
                    except Exception as exc:  # noqa: BLE001 - UI listeners must not break turns
                        logger.warning(f"Butler tool listener raised: {exc}")
                conversation.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            round_number += 1
        yield _fallback_sentence(results)

    async def _stream_round(
        self,
        endpoint: llm_endpoint.Endpoint,
        conversation: list[dict],
        current: _Round,
        final_round: bool,
        schemas: list[dict],
    ) -> AsyncIterator[str]:
        http = self._http()
        if http is None:
            raise RuntimeError("the Butler's HTTP session is not started")
        payload: dict = {
            "model": endpoint.model,
            "messages": conversation,
            "stream": True,
            "temperature": 0.3,
            "max_tokens": self._max_tokens,
        }
        if not final_round and schemas:
            payload["tools"] = schemas
            payload["tool_choice"] = "auto"
        if endpoint.cloud:
            llm_endpoint.apply_cloud_request_options(payload)
        timeout = aiohttp.ClientTimeout(total=self._timeout_secs)
        async with http.post(
            endpoint.chat_url, json=payload, headers=endpoint.headers, timeout=timeout
        ) as resp:
            if resp.status >= 400:
                body = await resp.text()
                retry = llm_endpoint.cloud_retry_without_reasoning_effort(
                    resp.status, body, payload
                )
                if retry is None:
                    if "arguments" in body:
                        sent = [
                            call["function"]["arguments"]
                            for message in conversation
                            for call in message.get("tool_calls") or ()
                        ]
                        logger.warning(f"Butler provider rejected tool arguments; sent: {sent}")
                    raise RuntimeError(f"HTTP {resp.status}: {body[:300]}")
            else:
                retry = None
                async for delta in self._read_stream(resp, current):
                    yield delta
        if retry is not None:
            async with http.post(
                endpoint.chat_url, json=retry, headers=endpoint.headers, timeout=timeout
            ) as resp:
                if resp.status >= 400:
                    raise RuntimeError(f"HTTP {resp.status}: {(await resp.text())[:300]}")
                async for delta in self._read_stream(resp, current):
                    yield delta

    @staticmethod
    async def _read_stream(resp: aiohttp.ClientResponse, current: _Round) -> AsyncIterator[str]:
        """Accumulate one streamed response into ``current``, yielding its text."""
        async for raw in resp.content:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(chunk, dict) and chunk.get("error"):
                raise RuntimeError(f"provider error: {chunk['error']}")
            choice = (chunk.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            for tool_delta in delta.get("tool_calls") or ():
                current.add_tool_delta(tool_delta)
            text = delta.get("content")
            if text:
                current.text += text
                yield text


def _normalized_arguments(raw: str) -> str:
    """Strict JSON for a call's arguments: the parsed object re-encoded, or ``"{}"``.

    Re-encoding (rather than echoing ``raw``) drops anything Python's parser
    tolerates but a strict one rejects, such as NaN or stray whitespace.
    """
    try:
        value = json.loads(raw or "{}")
        return json.dumps(value, allow_nan=False) if isinstance(value, dict) else "{}"
    except ValueError:
        return "{}"


def _safe_json(raw: str) -> dict:
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _fallback_sentence(results: list[dict]) -> str:
    """A truthful reply from tool summaries alone, for when the model can't finish."""
    summaries = [str(r.get("summary", "")).strip() for r in results if r.get("summary")]
    if not summaries:
        return "I couldn't finish that; nothing was started."
    return " ".join(summaries[-3:])
