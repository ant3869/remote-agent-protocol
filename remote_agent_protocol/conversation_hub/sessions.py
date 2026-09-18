"""Durable channel ownership and fail-closed physical-session lifecycle."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .models import SessionBinding, SessionStrategy

if TYPE_CHECKING:
    from remote_agent_protocol.control_plane.adapters.base import ConversationSessionAdapter
    from remote_agent_protocol.control_plane.models import JobHandle

    from .context import ContextPackage


class SessionBindingManager:
    """Persist bindings before use and validate restored native sessions.

    ``persist`` must durably save the supplied binding (including the channel's
    current binding reference) or raise. No terminal-session discovery is used.
    Dispatch failures rotate but never replay a potentially executed request.
    """

    def __init__(
        self,
        *,
        persist: Callable[[SessionBinding], None],
        bindings: Iterable[SessionBinding] = (),
    ) -> None:
        """Restore RAP-owned records, invalidating native validation on every restart."""
        self._persist = persist
        self._bindings: dict[str, SessionBinding] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        for binding in bindings:
            if binding.channel_id in self._bindings:
                raise ValueError("Duplicate channel binding")
            self._bindings[binding.channel_id] = replace(
                binding, requires_validation=binding.strategy is SessionStrategy.NATIVE_RESUME
            )

    @property
    def bindings(self) -> tuple[SessionBinding, ...]:
        """Return the current binding snapshot for hub persistence and inspection."""
        return tuple(self._bindings.values())

    @staticmethod
    def _check_identity(
        channel_id: str, adapter: ConversationSessionAdapter, binding: SessionBinding | None = None
    ) -> None:
        if channel_id != f"agent:{adapter.agent_id}":
            raise ValueError("Requested channel does not match adapter identity")
        if binding is not None and (
            binding.channel_id != channel_id
            or binding.agent_id != adapter.agent_id
            or binding.adapter_id != adapter.agent_id
        ):
            raise ValueError("Session binding identity does not match channel and adapter")

    def _save(self, binding: SessionBinding) -> SessionBinding:
        self._persist(binding)
        self._bindings[binding.channel_id] = binding
        return binding

    def _invalidate(self, channel_id: str, reason: str) -> None:
        binding = self._bindings.get(channel_id)
        if binding is not None:
            invalid = replace(
                binding,
                requires_validation=True,
                validation_evidence={**binding.validation_evidence, "pending_rotation": reason},
            )
            # Keep this process fail-closed even if the durable write fails.
            self._bindings[channel_id] = invalid
            self._persist(invalid)

    async def _create(
        self, channel_id: str, adapter: ConversationSessionAdapter, reason: str | None = None
    ) -> SessionBinding:
        fresh = await adapter.create_bound_session(channel_id)
        self._check_identity(channel_id, adapter, fresh)
        previous = self._bindings.get(channel_id)
        if previous and fresh.binding_id == previous.binding_id:
            raise ValueError("Session rotation requires a new binding identity")
        if fresh.strategy is SessionStrategy.NATIVE_RESUME:
            if (
                adapter.conversation_session_strategy is not SessionStrategy.NATIVE_RESUME
                or not fresh.native_session_id
                or not await adapter.validate_bound_session(fresh)
            ):
                raise ValueError("New native session lacks validated adapter support")
            if previous and fresh.native_session_id == previous.native_session_id:
                raise ValueError("Session rotation requires a new native session")
            fresh = replace(fresh, validated_at=datetime.now(UTC), requires_validation=False)
        elif fresh.native_session_id is not None:
            raise ValueError("Rehydration cannot carry a native session ID")
        evidence = dict(fresh.validation_evidence)
        evidence.pop("pending_rotation", None)
        return self._save(replace(fresh, rotation_reason=reason, validation_evidence=evidence))

    async def _ensure(self, channel_id: str, adapter: ConversationSessionAdapter) -> SessionBinding:
        self._check_identity(channel_id, adapter)
        binding = self._bindings.get(channel_id)
        if binding is None:
            return await self._create(channel_id, adapter)
        self._check_identity(channel_id, adapter, binding)
        if reason := binding.validation_evidence.get("pending_rotation"):
            return await self._create(channel_id, adapter, str(reason))
        if binding.strategy is SessionStrategy.REHYDRATE:
            if binding.native_session_id is not None:
                raise ValueError("Rehydration cannot carry a native session ID")
            return binding
        if (
            adapter.conversation_session_strategy is not SessionStrategy.NATIVE_RESUME
            or not binding.native_session_id
        ):
            return await self._create(channel_id, adapter, "native_session_unavailable")
        if binding.requires_validation or binding.validated_at is None:
            try:
                valid = await adapter.validate_bound_session(binding)
            except Exception:
                valid = False
            if not valid:
                return await self._create(channel_id, adapter, "validation_failed")
            binding = self._save(
                replace(binding, validated_at=datetime.now(UTC), requires_validation=False)
            )
        return binding

    async def ensure_binding(
        self, channel_id: str, adapter: ConversationSessionAdapter
    ) -> SessionBinding:
        """Return a durably owned binding safe for this exact channel and adapter."""
        async with self._locks.setdefault(channel_id, asyncio.Lock()):
            return await self._ensure(channel_id, adapter)

    async def rotate(
        self, channel_id: str, adapter: ConversationSessionAdapter, *, reason: str
    ) -> SessionBinding:
        """Create a clean binding after context limits, explicit reset, or failure."""
        if not reason.strip():
            raise ValueError("Session rotation requires a reason")
        async with self._locks.setdefault(channel_id, asyncio.Lock()):
            self._check_identity(channel_id, adapter, self._bindings.get(channel_id))
            self._invalidate(channel_id, reason)
            return await self._create(channel_id, adapter, reason)

    async def dispatch(
        self, channel_id: str, adapter: ConversationSessionAdapter, context: ContextPackage
    ) -> JobHandle:
        """Dispatch once after durable validation; leave completion monitoring to the hub."""
        async with self._locks.setdefault(channel_id, asyncio.Lock()):
            binding = await self._ensure(channel_id, adapter)
            binding = self._save(replace(binding, last_used_at=datetime.now(UTC)))
            try:
                return await adapter.dispatch_in_session(binding, context)
            except asyncio.CancelledError:
                self._invalidate(channel_id, "cancelled")
                raise
            except Exception:
                self._invalidate(channel_id, "failure")
                await self._create(channel_id, adapter, "failure")
                raise
