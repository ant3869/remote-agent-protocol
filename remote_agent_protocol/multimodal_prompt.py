"""Draft and assemble shared multimodal user prompts."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

ATTACHMENT_IMAGE = "image"
ATTACHMENT_SCREENSHOT = "screenshot"
ATTACHMENT_LINK = "link"
ATTACHMENT_FILE = "file"
VOICE_MODE_WAKE_WORD = "wake_word"
VOICE_MODE_FREE_TALK = "free_talk"
VOICE_MODE_PUSH_TO_TALK = "push_to_talk"
VOICE_MODES = (VOICE_MODE_WAKE_WORD, VOICE_MODE_FREE_TALK, VOICE_MODE_PUSH_TO_TALK)
DEFAULT_VOICE_MODE = VOICE_MODE_FREE_TALK

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_SEND_NOW_RE = re.compile(r"\b(send|submit)\s+(it|this|that|the prompt)?\s*now\b", re.I)
_DONT_SEND_RE = re.compile(r"\b(don't|do not|dont)\s+send\s+(it|this|that|yet)?\b", re.I)
_CANCEL_RE = re.compile(
    r"\b(cancel that|never mind|nevermind|ignore (?:the|this) "
    r"(?:screenshot|image|picture|file|document|link|attachment))\b",
    re.I,
)
_CONTEXT_REFERENCE_RE = re.compile(
    r"\b("
    r"this (?:screenshot|image|picture|file|document|link|attachment)|"
    r"the (?:screenshot|image|picture|highlighted part|file|document|link|attachment)|"
    r"what i uploaded|look at this|read this|don't read this out loud|"
    r"do not read this out loud|use this with what i'm saying"
    r")\b",
    re.I,
)
_PREFERENCE_RE = re.compile(
    r"\b(?:always|never|don't|do not|dont|default to|when i|remember|call this|call the|treat them)\b",
    re.IGNORECASE,
)


def utc_now_iso() -> str:
    """Return a compact UTC timestamp for persisted prompt bundles."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@dataclass
class VoiceContext:
    """Voice transcript metadata held inside a shared prompt bundle."""

    transcript: str = ""
    audio_file: str = ""
    confidence: float | None = None
    language: str = ""
    started_at: str = ""
    ended_at: str = ""

    def to_dict(self) -> dict:
        """Return a JSON-compatible representation."""
        return {
            "audio_file": self.audio_file,
            "audio_file_id": self.audio_file,
            "transcript": self.transcript,
            "confidence": self.confidence,
            "language": self.language,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }


@dataclass
class TextContext:
    """Typed-note metadata held inside a shared prompt bundle."""

    raw_text: str = ""
    edited_text: str = ""

    def to_dict(self) -> dict:
        """Return a JSON-compatible representation."""
        return {
            "raw_text": self.raw_text,
            "edited_text": self.edited_text,
            "typed_notes": self.edited_text or self.raw_text,
        }


@dataclass
class PromptAttachment:
    """A link, image, or file reference attached to the prompt bundle."""

    type: str
    reference: str
    caption: str = ""
    user_note: str = ""
    title: str = ""
    attachment_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    def order_key(self) -> str:
        """Return the stable key used by ``context_order``."""
        return f"{self.type}_{self.attachment_id}"

    def to_dict(self) -> dict:
        """Return a JSON-compatible representation."""
        row = {
            "type": self.type,
            "caption": self.caption,
            "user_note": self.user_note,
        }
        if self.type == ATTACHMENT_LINK:
            row.update({"url": self.reference, "title": self.title})
        elif self.type in {ATTACHMENT_IMAGE, ATTACHMENT_SCREENSHOT}:
            row.update({"file_id": self.reference})
        else:
            row.update({"file_id": self.reference, "filename": Path(self.reference).name})
        return row


@dataclass
class MultimodalPromptBundle:
    """One reviewed user submission containing every modality."""

    conversation_id: str = ""
    user_id: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    final_user_instruction: str = ""
    voice: VoiceContext = field(default_factory=VoiceContext)
    text: TextContext = field(default_factory=TextContext)
    attachments: list[PromptAttachment] = field(default_factory=list)
    context_order: list[str] = field(default_factory=list)
    send_mode: str = "manual"
    voice_mode: str = DEFAULT_VOICE_MODE
    send_reason: str = "manual_send"
    context_signals: list[str] = field(default_factory=list)

    def add_voice_transcript(
        self,
        transcript: str,
        *,
        confidence: float | None = None,
        language: str = "",
        audio_file: str = "",
        started_at: str = "",
        ended_at: str = "",
    ) -> str:
        """Store the latest voice transcript and return any voice send intent."""
        self.voice = VoiceContext(
            transcript=transcript.strip(),
            audio_file=audio_file,
            confidence=confidence,
            language=language,
            started_at=started_at,
            ended_at=ended_at,
        )
        self._remember_order("voice")
        return send_intent(transcript)

    def set_text(self, raw_text: str, edited_text: str = "") -> None:
        """Store typed notes for the prompt bundle."""
        self.text = TextContext(raw_text=raw_text.strip(), edited_text=edited_text.strip())
        if self.text.raw_text or self.text.edited_text:
            self._remember_order("typed_note")

    def set_final_instruction(self, instruction: str) -> None:
        """Store the final user request for the prompt bundle."""
        self.final_user_instruction = instruction.strip()
        if self.final_user_instruction:
            self._remember_order("final_instruction")

    def add_attachment(self, attachment: PromptAttachment) -> None:
        """Append an attachment and preserve its order."""
        self.attachments.append(attachment)
        self._remember_order(attachment.order_key())

    def remove_attachment(self, attachment_id: str) -> None:
        """Remove an attachment and its order marker."""
        removed = {
            item.order_key() for item in self.attachments if item.attachment_id == attachment_id
        }
        self.attachments = [
            item for item in self.attachments if item.attachment_id != attachment_id
        ]
        self.context_order = [item for item in self.context_order if item not in removed]

    def agent_prompt(self) -> str:
        """Render the reviewed bundle into the single prompt seen by the agent."""
        images = [
            a for a in self.attachments if a.type in {ATTACHMENT_IMAGE, ATTACHMENT_SCREENSHOT}
        ]
        links = [a for a in self.attachments if a.type == ATTACHMENT_LINK]
        files = [a for a in self.attachments if a.type == ATTACHMENT_FILE]
        return "\n\n".join(
            [
                "## User Voice Transcript\n" + (self.voice.transcript or "(none)"),
                "## Typed User Notes\n" + (self.text.edited_text or self.text.raw_text or "(none)"),
                "## Attached Images\n" + _attachment_lines(images),
                "## Links\n" + _attachment_lines(links),
                "## Files\n" + _attachment_lines(files),
                "## Context Order\n" + (", ".join(self.context_order) or "(none)"),
                "## Final User Request\n" + (self.final_user_instruction or "(none)"),
                (
                    "## Instruction to Agent\n"
                    "Use all provided materials together as one shared context. Do not answer "
                    "based on only one modality. If the voice transcript references an image, "
                    "link, file, or highlighted area, inspect that material before answering."
                ),
            ]
        )

    def preference_candidates(self) -> list[str]:
        """Return durable-preference-looking text from all modalities."""
        chunks = [
            self.voice.transcript,
            self.text.raw_text,
            self.text.edited_text,
            self.final_user_instruction,
        ]
        chunks.extend(a.user_note for a in self.attachments)
        chunks.extend(a.caption for a in self.attachments)
        return [chunk.strip() for chunk in chunks if chunk.strip() and _PREFERENCE_RE.search(chunk)]

    def to_dict(self) -> dict:
        """Return a JSON-compatible representation."""
        return {
            "type": "multimodal_user_prompt",
            "conversation_id": self.conversation_id,
            "user_id": self.user_id,
            "created_at": self.created_at,
            "voice_mode": self.voice_mode,
            "send_reason": self.send_reason,
            "final_user_instruction": self.final_user_instruction,
            "voice": self.voice.to_dict(),
            "text": {
                **self.text.to_dict(),
                "final_instruction": self.final_user_instruction,
            },
            "attachments": [item.to_dict() for item in self.attachments],
            "context_order": self.context_order,
            "send_mode": self.send_mode,
            "context_detection": {
                "has_context": bool(self.context_signals or self.attachments),
                "signals": self.context_signals,
            },
        }

    def _remember_order(self, key: str) -> None:
        if key not in self.context_order:
            self.context_order.append(key)


def attachment_from_reference(
    reference: str, *, note: str = "", caption: str = ""
) -> PromptAttachment:
    """Infer attachment type from a URL or file path."""
    reference = reference.strip()
    if _URL_RE.fullmatch(reference):
        return PromptAttachment(ATTACHMENT_LINK, reference, user_note=note)
    suffix = Path(reference).suffix.lower()
    kind = (
        ATTACHMENT_IMAGE
        if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
        else ATTACHMENT_FILE
    )
    return PromptAttachment(kind, reference, caption=caption, user_note=note)


def send_intent(text: str) -> str:
    """Return ``send``/``hold``/``cancel`` for voice commands, or empty string."""
    if _CANCEL_RE.search(text):
        return "cancel"
    if _DONT_SEND_RE.search(text):
        return "hold"
    if _SEND_NOW_RE.search(text):
        return "send"
    return ""


def context_reference_signals(text: str) -> list[str]:
    """Return voice/text signals that mean the utterance depends on context."""
    return ["context_reference"] if _CONTEXT_REFERENCE_RE.search(text) else []


def context_signals(
    text: str = "",
    *,
    has_attachments: bool = False,
    has_link: bool = False,
    upload_in_progress: bool = False,
    draft_active: bool = False,
    recently_attached: bool = False,
) -> list[str]:
    """Explain why voice should be held as part of a shared-context prompt."""
    signals = context_reference_signals(text)
    if has_attachments:
        signals.append("attachment")
    if has_link or _URL_RE.search(text):
        signals.append("link")
    if upload_in_progress:
        signals.append("upload_in_progress")
    if draft_active:
        signals.append("draft_active")
    if recently_attached:
        signals.append("recent_attachment")
    return list(dict.fromkeys(signals))


def normalize_voice_mode(value: str | None) -> str:
    """Return a supported voice mode, defaulting to normal Free Talk."""
    return value if value in VOICE_MODES else DEFAULT_VOICE_MODE


def _attachment_lines(items: list[PromptAttachment]) -> str:
    if not items:
        return "(none)"
    lines = []
    for index, item in enumerate(items, 1):
        label = item.type.title()
        reference = item.reference
        if item.type == ATTACHMENT_LINK and item.title:
            reference = f"{item.title} ({item.reference})"
        lines.append(f"- {label} {index}: {reference}")
        if item.caption:
            lines.append(f"  Caption: {item.caption}")
        if item.user_note:
            lines.append(f"  User note: {item.user_note}")
    return "\n".join(lines)
