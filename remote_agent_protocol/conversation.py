"""Attributable conversation events and bounded replay independent of telemetry."""

from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

TERMINAL = frozenset({"done", "failed", "cancelled", "timeout"})
DELIVERY_TERMINAL = frozenset({"played", "interrupted", "failed"})


class SpeechText(str):
    """A compatible text chunk carrying its utterance identity to the HTTP bridge."""

    def __new__(cls, text: str, utterance: dict):
        """Preserve string consumers while exposing metadata to capable clients."""
        obj = super().__new__(cls, text)
        obj.utterance = {
            key: utterance.get(key)
            for key in (
                "message_id",
                "session_id",
                "turn_id",
                "speaker_id",
                "speaker_name",
                "source_agent",
                "job_id",
                "relation",
            )
        }
        return obj


def now() -> str:
    """Return an occurrence timestamp suitable for replay and display."""
    return datetime.now(UTC).isoformat()


class ConversationEvents:
    """Stamp source identity before asynchronous delivery can change the persona."""

    def __init__(self, speaker: Callable[[], str], delivery: str = "text_only"):
        """Create one session's identity factory."""
        self.session_id = uuid4().hex
        self._speaker = speaker
        self.delivery = delivery

    def utterance(self, text: str = "", **fields) -> dict:
        """Create a stable utterance that partial and playback updates can reuse."""
        ident = uuid4().hex
        role = fields.get("role", "assistant")
        name = fields.get("speaker_name") or ("You" if role == "user" else self._speaker())
        return {
            "type": "transcript",
            "session_id": self.session_id,
            "message_id": ident,
            "turn_id": fields.pop("turn_id", ident),
            "speaker_id": fields.get("speaker_id", name),
            "speaker_name": name,
            "role": role,
            "text": text,
            "final": True,
            "revision": 0,
            "delivery": "text_only" if role == "user" else self.delivery,
            "occurred_at": now(),
            **fields,
        }

    def stamp(self, event: dict) -> dict:
        """Add event identity without changing an existing utterance's attribution."""
        row = dict(event)
        if row.get("type") == "transcript" and not row.get("message_id"):
            row = self.utterance(**{k: v for k, v in row.items() if k != "type"})
        return {
            "session_id": self.session_id,
            "occurred_at": now(),
            **row,
            "event_id": row.get("event_id") or uuid4().hex,
        }

    def reset(self) -> str:
        """Retire the source generation so late in-flight turns can be rejected."""
        previous = self.session_id
        self.session_id = uuid4().hex
        return previous


class ConversationStore:
    """Fold conversation rows; noisy audio metrics and CLI output consume no history."""

    def __init__(self, limit: int = 1200):
        """Keep a bounded session snapshot and an explicit truncation indicator."""
        self.limit = limit
        self.rows: OrderedDict[str, dict] = OrderedDict()
        self.epoch = uuid4().hex
        self.truncated = False
        self._cleared: OrderedDict[str, None] = OrderedDict()
        self._retired_sessions: deque[str] = deque(maxlen=64)

    def retire(self, session_id: str) -> None:
        """Reject in-flight utterances created before an explicit conversation clear."""
        self._retired_sessions.append(session_id)

    def clear(self) -> None:
        """Forget retained text and reject late updates to the cleared utterances."""
        self._cleared.update(dict.fromkeys(self.rows))
        while len(self._cleared) > self.limit * 4:
            self._cleared.popitem(last=False)
        self.rows.clear()
        self.epoch = uuid4().hex
        self.truncated = False

    def apply(self, event: dict) -> dict | None:
        """Return a normalized changed row, or None for non-conversation events."""
        kind = event.get("type")
        if event.get("session_id") in self._retired_sessions:
            return None
        key = ""
        if kind == "transcript":
            key = "speech:" + str(event.get("message_id") or event.get("event_id") or event["id"])
        elif kind == "agent_job" and event.get("job_id") and event.get("event") != "output":
            key = "job:" + event["job_id"]
        elif kind in {"agent_confirm", "agent_confirm_resolved"}:
            key = "confirm:" + str(event.get("session_id", "")) + ":" + str(event.get("token"))
        elif kind == "agent_consult":
            key = "consult:" + str(
                event.get("consultation_id") or event.get("event_id") or event["id"]
            )
        elif kind == "routing" and event.get("action") not in {None, "none", "chat"}:
            key = "route:" + str(event.get("event_id") or event["id"])
        elif kind == "sys" and event.get("text"):
            key = "sys:" + str(event.get("event_id") or event["id"])
        if not key or key in self._cleared:
            return None
        old = self.rows.get(key, {})
        if kind == "transcript" and event.get("playback_update") and not old:
            return None
        if old and event.get("id", 0) <= old.get("id", -1):
            return None
        row = {**old, **event, "key": key}
        if kind == "transcript":
            if old.get("final") and event.get("final") is False:
                return None
            if old.get("delivery") in DELIVERY_TERMINAL or (
                old.get("delivery") == "playing" and not event.get("playback_update")
            ):
                row["delivery"] = old["delivery"]
            # Playback reports must not remove the generated text or its speaker.
            if event.get("playback_update"):
                if old.get("delivery") in DELIVERY_TERMINAL:
                    return None
                row = {
                    **old,
                    **{
                        k: event[k]
                        for k in ("delivery", "spoken_text", "id", "event_id", "playback_sequence")
                        if k in event
                    },
                    "key": key,
                }
        elif kind == "agent_job":
            if old.get("status") in TERMINAL and event.get("status") not in TERMINAL:
                return None
            row.pop("lines", None)
            row.pop("line", None)
            activity = list(old.get("activity", []))
            detail = event.get("action") or event.get("last_completed_step") or event.get("state")
            signature = (event.get("state"), detail, event.get("tool"), event.get("step"))
            if detail and (not activity or tuple(activity[-1]["signature"]) != signature):
                activity.append(
                    {
                        "text": detail,
                        "tool": event.get("tool"),
                        "at": event.get("occurred_at"),
                        "signature": signature,
                    }
                )
            row["activity"] = activity[-60:]
        elif kind == "agent_consult":
            row["parent_job_id"] = old.get("parent_job_id") or event.get("job_id")
            exchanges = dict(old.get("exchanges", {}))
            exchanges[event.get("event", "update")] = dict(event)
            row["exchanges"] = exchanges
        elif kind == "agent_confirm" and old.get("type") == "agent_confirm_resolved":
            return None
        self.rows[key] = row
        while len(self.rows) > self.limit:
            self.rows.popitem(last=False)
            self.truncated = True
        return deepcopy(row)

    def snapshot(self) -> dict:
        """Return a detached snapshot safe for the HTTP server to serialize."""
        return {
            "epoch": self.epoch,
            "truncated": self.truncated,
            "rows": deepcopy(list(self.rows.values())),
        }
