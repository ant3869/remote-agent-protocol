"""Deterministic bounded context for RAP-owned channels and task transfers.

Inputs and packages are ephemeral. Durable channels, turns and memories retain
the Task 1 schema. This does not enable mem0 semantic-memory writes in Brain mode.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime

from .memory import MemoryRepository, safe_context_text
from .models import ConversationTurn, MemoryConfidence


@dataclass(frozen=True)
class ContextBudget:
    """Character ceilings, including section headers, without a tokenizer dependency."""

    total_chars: int = 48_000
    recent_turns_chars: int = 20_000
    summary_chars: int = 8_000
    active_task_chars: int = 8_000
    memory_chars: int = 8_000
    live_state_chars: int = 4_000

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if type(value) is not int or value < 0:
                raise ValueError("Context budgets must be non-negative integers")


@dataclass(frozen=True)
class SummaryItem:
    """A caller-classified summary statement whose confidence is never upgraded."""

    text: str
    confidence: MemoryConfidence = MemoryConfidence.USER_STATED


@dataclass(frozen=True)
class ChannelSummary:
    """A structured summary belonging to exactly one channel."""

    channel_id: str
    user_statements: tuple[SummaryItem, ...] = ()
    verified_observations: tuple[SummaryItem, ...] = ()
    decisions: tuple[SummaryItem, ...] = ()
    open_questions: tuple[SummaryItem, ...] = ()
    active_tasks: tuple[SummaryItem, ...] = ()

    def __post_init__(self) -> None:
        if any(
            item.confidence is not MemoryConfidence.VERIFIED for item in self.verified_observations
        ):
            raise ValueError("Verified observations must carry verified confidence")
        if any(
            item.confidence is not MemoryConfidence.USER_STATED for item in self.user_statements
        ):
            raise ValueError("User statements must carry user-stated confidence")


@dataclass(frozen=True)
class TaskContext:
    """The explicitly selected active task package, including artifact references."""

    task_id: str
    text: str


@dataclass(frozen=True)
class LiveObservation:
    """Control-plane output with timestamp and at least one relevant scope binding."""

    text: str
    observed_at: datetime
    channel_id: str | None = None
    task_id: str | None = None
    project_id: str | None = None

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("Live observations require timezone-aware timestamps")


@dataclass(frozen=True)
class ContextRequest:
    """Trusted caller inputs; no prior channel is implicitly copied on a switch.

    ``selected_material`` is a deliberate caller/user selection, not an inferred
    transcript transfer. Control-plane observations must come from live probes.
    """

    channel_id: str
    current_request: str
    communication_contract: str
    task_id: str | None = None
    project_id: str | None = None
    active_task: TaskContext | None = None
    recent_turns: tuple[ConversationTurn, ...] = ()
    summary: ChannelSummary | None = None
    selected_material: tuple[str, ...] = ()
    live_state: tuple[LiveObservation, ...] = ()
    previous_channel_id: str | None = None


@dataclass(frozen=True)
class ContextPackage:
    """Rendered bounded sections and identifiers of omitted optional sections."""

    sections: tuple[tuple[str, str], ...]
    omitted_sections: tuple[str, ...] = ()

    def render(self) -> str:
        """Render the exact text whose length is checked against the total budget."""
        return "\n\n".join(text for _, text in self.sections)

    def section(self, name: str) -> str:
        """Return an individual rendered section, or an empty string if omitted."""
        return dict(self.sections).get(name, "")


def _confidence(value: MemoryConfidence) -> str:
    return "inferred; not access evidence" if value is MemoryConfidence.INFERRED else value.value


def _safe_turn(turn: ConversationTurn) -> bool:
    return (
        turn.speaker_role not in ("tool", "system")
        and not any(
            turn.metadata.get(key)
            for key in ("raw_tool_output", "contains_secret", "hidden_reasoning", "heartbeat")
        )
        and safe_context_text(turn.full_text)
    )


class ContextAssembler:
    """Assemble contract/request first, then optional scoped evidence in fixed order."""

    def __init__(self, memories: MemoryRepository, budget: ContextBudget | None = None) -> None:
        """Use the hub's scoped repository and optional character-budget overrides."""
        self.memories = memories
        self.budget = budget or ContextBudget()

    def assemble(self, request: ContextRequest) -> ContextPackage:
        """Build context or reject required content that cannot safely fit intact.

        Optional records are included whole so labels, facts and provenance never
        become misleading fragments. A skipped section is exposed to the hub for
        compaction/rotation decisions, without copying hidden text to UI events.
        """
        if not all(
            safe_context_text(text)
            for text in (request.communication_contract, request.current_request)
        ):
            raise ValueError(
                "Required contract/request contains secret or hidden reasoning content"
            )
        sections = [
            ("contract", "[Communication contract]\n" + request.communication_contract),
            ("request", "[Current request]\n" + request.current_request),
        ]
        used = len(ContextPackage(tuple(sections)).render())
        if used > self.budget.total_chars:
            raise ValueError("Required contract and request exceed the total context budget")
        omitted: list[str] = []

        def append(
            name: str, title: str, records: list[str], ceiling: int, newest_first: bool = False
        ) -> None:
            nonlocal used
            if not records:
                return
            header = f"[{title}]\n"
            available = min(ceiling, self.budget.total_chars - used - 2)
            chosen: list[str] = []
            size = len(header)
            for record in reversed(records) if newest_first else records:
                if not safe_context_text(record):
                    if name not in omitted:
                        omitted.append(name)
                    continue
                required = len(record) + bool(chosen)
                if size + required <= available:
                    chosen.append(record)
                    size += required
                elif name not in omitted:
                    omitted.append(name)
            if chosen:
                if newest_first:
                    chosen.reverse()
                text = header + "\n".join(chosen)
                sections.append((name, text))
                used += len(text) + 2

        active = request.active_task
        task_records = [active.text] if active and active.task_id == request.task_id else []
        task_records.extend(f"Explicitly selected: {text}" for text in request.selected_material)
        append(
            "active_task",
            "Active task and selected material",
            task_records,
            self.budget.active_task_chars,
        )

        turns = sorted(
            (
                turn
                for turn in request.recent_turns
                if turn.channel_id == request.channel_id and _safe_turn(turn)
            ),
            key=lambda turn: (turn.created_at, turn.turn_id),
        )
        append(
            "recent_turns",
            "Recent channel turns",
            [
                f"{turn.created_at.isoformat()} {turn.speaker_id} ({turn.speaker_role}): {turn.full_text}"
                for turn in turns
            ],
            self.budget.recent_turns_chars,
            newest_first=True,
        )

        summary_records = []
        if request.summary and request.summary.channel_id == request.channel_id:
            for key, title in (
                ("user_statements", "User statements"),
                ("verified_observations", "Verified observations"),
                ("decisions", "Decisions"),
                ("open_questions", "Open questions"),
                ("active_tasks", "Active tasks"),
            ):
                for item in getattr(request.summary, key):
                    summary_records.append(f"{title}: [{_confidence(item.confidence)}] {item.text}")
        append("summary", "Compacted channel summary", summary_records, self.budget.summary_chars)

        memories = self.memories.eligible(
            channel_id=request.channel_id, task_id=request.task_id, project_id=request.project_id
        )
        append(
            "memories",
            "Eligible memories (historical; not live access proof)",
            [
                f"{memory.memory_id} [{memory.scope.value}; {_confidence(memory.confidence)}; "
                f"observed {memory.observed_at.isoformat()}; sources {','.join(memory.source_turn_ids)}] "
                f"{memory.subject}: {memory.value}"
                for memory in memories
            ],
            self.budget.memory_chars,
        )

        observations = sorted(
            (
                item
                for item in request.live_state
                if (
                    (item.channel_id and item.channel_id == request.channel_id)
                    or (item.task_id and item.task_id == request.task_id)
                    or (item.project_id and item.project_id == request.project_id)
                )
            ),
            key=lambda item: (
                item.observed_at,
                item.channel_id or "",
                item.task_id or "",
                item.project_id or "",
                item.text,
            ),
        )
        append(
            "live_state",
            "Relevant live control-plane state",
            [f"{item.observed_at.isoformat()}: {item.text}" for item in observations],
            self.budget.live_state_chars,
        )
        return ContextPackage(tuple(sections), tuple(omitted))
