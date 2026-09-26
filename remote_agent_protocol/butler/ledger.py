"""What the user asked for, independent of which agent is currently doing it.

A Butler task outlives its attempts: "have Codex try" after Hermes failed is a
second attempt at the same task, so "how's the email thing?" still finds it.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

_MAX_TASKS = 50


@dataclass
class Attempt:
    """One dispatch of a task to one agent."""

    job_id: str
    agent: str


@dataclass
class ButlerTask:
    """A user-level request and every attempt made at it."""

    task_id: str
    subject: str
    instructions: str
    created_at: float = field(default_factory=time.time)
    attempts: list[Attempt] = field(default_factory=list)
    # Set while the latest attempt waits on the user's approval.
    held_token: str | None = None
    held_agent: str = ""

    @property
    def latest(self) -> Attempt | None:
        """The most recent attempt, if any was dispatched."""
        return self.attempts[-1] if self.attempts else None


class TaskLedger:
    """Butler tasks for one session, newest last, bounded."""

    def __init__(self) -> None:
        """Start empty."""
        self._tasks: dict[str, ButlerTask] = {}
        self._counter = 0

    def create(self, subject: str, instructions: str) -> ButlerTask:
        """Record a new task and return it."""
        self._counter += 1
        task = ButlerTask(
            task_id=f"t{self._counter}",
            subject=subject.strip() or _subject_from(instructions),
            instructions=instructions.strip(),
        )
        self._tasks[task.task_id] = task
        while len(self._tasks) > _MAX_TASKS:
            self._tasks.pop(next(iter(self._tasks)))
        return task

    def attach(self, task_id: str, job_id: str, agent: str) -> None:
        """Record that ``job_id`` on ``agent`` is the latest attempt at ``task_id``."""
        task = self._tasks.get(task_id)
        if task is None:
            return
        task.attempts.append(Attempt(job_id=job_id, agent=agent))
        task.held_token = None
        task.held_agent = ""

    def hold(self, task_id: str, token: str, agent: str) -> None:
        """Mark ``task_id`` as waiting on confirmation ``token`` for ``agent``."""
        task = self._tasks.get(task_id)
        if task is not None:
            task.held_token = token
            task.held_agent = agent

    def release(self, token: str) -> ButlerTask | None:
        """Clear a confirmation hold; returns the task that held it."""
        for task in self._tasks.values():
            if task.held_token == token:
                task.held_token = None
                task.held_agent = ""
                return task
        return None

    def task_for_token(self, token: str) -> ButlerTask | None:
        """The task waiting on confirmation ``token``."""
        return next((t for t in self._tasks.values() if t.held_token == token), None)

    def get(self, task_id: str) -> ButlerTask | None:
        """A task by id."""
        return self._tasks.get(task_id)

    def by_job(self, job_id: str) -> ButlerTask | None:
        """The task that ``job_id`` is an attempt of."""
        return next(
            (t for t in self._tasks.values() if any(a.job_id == job_id for a in t.attempts)),
            None,
        )

    def newest_first(self) -> list[ButlerTask]:
        """All tasks, newest first."""
        return list(reversed(self._tasks.values()))

    def resolve(self, reference: str | None) -> ButlerTask | None:
        """Find a task by id, by attempt job id, or by words from its subject.

        No reference means the newest task -- "how's it going?" is about the
        last thing asked for.
        """
        tasks = self.newest_first()
        if not tasks:
            return None
        ref = (reference or "").strip()
        if not ref:
            return tasks[0]
        lowered = ref.lower()
        if lowered in self._tasks:
            return self._tasks[lowered]
        if (by_job := self.by_job(ref)) is not None:
            return by_job
        words = {w for w in re.findall(r"[a-z0-9]+", lowered) if len(w) > 2}
        if not words:
            return None
        best, best_score = None, 0
        for task in tasks:
            haystack = set(re.findall(r"[a-z0-9]+", f"{task.subject} {task.instructions}".lower()))
            score = len(words & haystack)
            if score > best_score:
                best, best_score = task, score
        return best


def _subject_from(instructions: str) -> str:
    words = instructions.split()
    return " ".join(words[:8]) + ("..." if len(words) > 8 else "")
