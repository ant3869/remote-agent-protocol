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

    def add_tool_delta(self, delta: dict) -> None:
        index = int(delta.get("index", len(self.tool_calls)))
        call = self.tool_calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
        call["id"] = delta.get("id") or call["id"]
        function = delta.get("function") or {}
        call["name"] += function.get("name") or ""
        call["arguments"] += function.get("arguments") or ""


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

    async def run(self, messages: list[dict]) -> AsyncIterator[str]:
        """Yield the reply's text for ``messages`` (system first), running tools as asked."""
        endpoints = self._endpoints()
        if not endpoints:
            raise ButlerUnavailable("no model endpoint is configured for the Butler")
        conversation = list(messages)
        results: list[dict] = []
        spoke = False
        endpoint_index = 0
        round_number = 0
        while round_number < self._max_rounds:
            endpoint = endpoints[endpoint_index]
            final_round = round_number == self._max_rounds - 1
            current = _Round()
            try:
                async for delta in self._stream_round(endpoint, conversation, current, final_round):
                    spoke = True
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
                                "arguments": call["arguments"] or "{}",
                            },
                        }
                        for call in calls
                    ],
                }
            )
            for call in calls:
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
        if not final_round:
            payload["tools"] = TOOL_SCHEMAS
            payload["tool_choice"] = "auto"
        timeout = aiohttp.ClientTimeout(total=self._timeout_secs)
        async with http.post(
            endpoint.chat_url, json=payload, headers=endpoint.headers, timeout=timeout
        ) as resp:
            if resp.status >= 400:
                body = (await resp.text())[:300]
                raise RuntimeError(f"HTTP {resp.status}: {body}")
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
