"""Where a model provider's API key actually lives.

Provider configuration (`model_providers.py`) never holds a key -- only this
module does, and only in Windows Credential Manager. There is no plain-text
fallback: a system without Credential Manager can't save a key at all, and
gets told so, rather than silently writing it to `.env` or JSON.

Keys never appear in the returned config the rest of the app persists or
serves, but a caller can still accidentally quote one in a log line (an
error message that echoes a request, for instance). ``install_log_redaction``
guards against that by scrubbing every key this module has ever stored out of
loguru's output.
"""

from __future__ import annotations

from typing import Protocol

from loguru import logger

try:
    import pywintypes
    import win32cred
except ImportError:  # pragma: no cover - exercised only off Windows
    pywintypes = None  # type: ignore[assignment]
    win32cred = None  # type: ignore[assignment]

_TARGET_PREFIX = "RAP/model-provider/"
_ERROR_NOT_FOUND = 1168  # ERROR_NOT_FOUND, from CredRead/CredDelete


class SecretBackendUnavailable(RuntimeError):
    """No secure secret store exists on this system."""


class SecretBackend(Protocol):
    """Where one target's secret value actually lives."""

    def set(self, target: str, value: str) -> None:
        """Store ``value`` under ``target``, overwriting any existing value."""
        ...

    def get(self, target: str) -> str | None:
        """The stored value for ``target``, or None if there isn't one."""
        ...

    def delete(self, target: str) -> bool:
        """Remove the stored value for ``target``. False if there was none."""
        ...


class InMemoryBackend:
    """Test-only backend. Production always goes through Credential Manager."""

    def __init__(self) -> None:
        """Start with no stored values."""
        self._values: dict[str, str] = {}

    def set(self, target: str, value: str) -> None:
        """Store ``value`` under ``target``, overwriting any existing value."""
        self._values[target] = value

    def get(self, target: str) -> str | None:
        """The stored value for ``target``, or None if there isn't one."""
        return self._values.get(target)

    def delete(self, target: str) -> bool:
        """Remove the stored value for ``target``. False if there was none."""
        return self._values.pop(target, None) is not None


class _Win32CredentialBackend:
    """One generic credential per provider.

    Follows the pattern the widely-used ``keyring`` package uses for its
    Windows backend: the blob is written as a plain string (pywin32 encodes
    it) and read back as UTF-16 bytes, with a UTF-8 fallback for anything
    written by another tool.
    """

    def __init__(self) -> None:
        """Raise immediately if Credential Manager isn't available."""
        if win32cred is None:
            raise SecretBackendUnavailable(
                "Windows Credential Manager (win32cred) is not available on "
                "this system; provider API keys cannot be stored securely here."
            )

    def set(self, target: str, value: str) -> None:
        win32cred.CredWrite(
            {
                "Type": win32cred.CRED_TYPE_GENERIC,
                "TargetName": target,
                "UserName": "RAP",
                "CredentialBlob": value,
                "Comment": "Remote Agent Protocol model-provider key",
                "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
            },
            0,
        )

    def get(self, target: str) -> str | None:
        try:
            credential = win32cred.CredRead(Type=win32cred.CRED_TYPE_GENERIC, TargetName=target)
        except pywintypes.error as exc:
            if exc.winerror == _ERROR_NOT_FOUND:
                return None
            raise
        blob = credential["CredentialBlob"]
        if not isinstance(blob, bytes):
            return str(blob)
        try:
            return blob.decode("utf-16")
        except UnicodeDecodeError:
            return blob.decode("utf-8")

    def delete(self, target: str) -> bool:
        try:
            win32cred.CredDelete(Type=win32cred.CRED_TYPE_GENERIC, TargetName=target)
        except pywintypes.error as exc:
            if exc.winerror == _ERROR_NOT_FOUND:
                return False
            raise
        return True


_backend: SecretBackend | None = None
_override: SecretBackend | None = None
_known_secrets: set[str] = set()


def use_backend(backend: SecretBackend | None) -> None:
    """Test hook: force a specific backend, or ``None`` to use the real one."""
    global _override
    _override = backend


def _resolve_backend() -> SecretBackend:
    global _backend
    if _override is not None:
        return _override
    if _backend is None:
        _backend = _Win32CredentialBackend()
    return _backend


def _target(provider_id: str) -> str:
    return f"{_TARGET_PREFIX}{provider_id}"


def set_key(provider_id: str, key: str) -> None:
    """Store ``key`` for ``provider_id``.

    Raises:
        SecretBackendUnavailable: no secure store exists on this system.
    """
    _resolve_backend().set(_target(provider_id), key)
    if key:
        _known_secrets.add(key)


def get_key(provider_id: str) -> str | None:
    """The stored key for ``provider_id``, or None if none is stored."""
    return _resolve_backend().get(_target(provider_id))


def delete_key(provider_id: str) -> bool:
    """Remove the stored key for ``provider_id``. False if there was none."""
    return _resolve_backend().delete(_target(provider_id))


def has_key(provider_id: str) -> bool:
    """Whether a key is stored for ``provider_id``, without exposing it."""
    return get_key(provider_id) is not None


def masked(provider_id: str) -> str | None:
    """``••••abcd``, or None when no key is stored."""
    key = get_key(provider_id)
    if key is None:
        return None
    if len(key) <= 4:
        return "••••"
    return f"••••{key[-4:]}"


def redact(text: str) -> str:
    """Strip any key this module has ever stored out of arbitrary text."""
    for secret in _known_secrets:
        if secret and secret in text:
            text = text.replace(secret, "[REDACTED]")
    return text


def _patch_record(record: dict) -> None:
    record["message"] = redact(record["message"])


def install_log_redaction() -> None:
    """Patch loguru so a stored key never reaches a sink verbatim."""
    logger.configure(patcher=_patch_record)
