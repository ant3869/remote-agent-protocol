"""Status checks for external coding-agent CLIs (Codex, Claude Code).

Read-only: reports whether each CLI is on PATH, its version, and whether it
appears authenticated. Never installs, configures, or logs anything in.

Every subprocess.run() below pins encoding="utf-8", errors="replace" --
without it, Python decodes captured output with the OS locale codec (cp1252
on this app's target platform), and a CLI that prints any character outside
that codec crashes subprocess's internal reader thread with an unhandled
UnicodeDecodeError (2026-07-11: happened via Codex/Claude's own rich
terminal output, silently corrupting the status check instead of raising
where get_status()'s own try/except could catch it).
"""

import shutil
import subprocess
from dataclasses import dataclass
from typing import Protocol


@dataclass
class CodingAgentStatus:
    """Snapshot of one coding-agent CLI's availability and auth state."""

    available: bool
    version: str | None
    auth_ok: bool | None
    executable_path: str | None
    error: str | None

    def to_dict(self):
        """Plain-dict form for JSON responses."""
        return {
            "available": self.available,
            "version": self.version,
            "auth_ok": self.auth_ok,
            "executable_path": self.executable_path,
            "error": self.error,
        }


class CodingAgentBackend(Protocol):
    """A coding-agent CLI this app can check the status of."""

    id: str
    label: str

    def is_available(self) -> bool:
        """True if the CLI executable is on PATH."""
        ...

    def get_status(self) -> CodingAgentStatus:
        """Query version and auth state by running the CLI briefly."""
        ...


class CodexCliBackend:
    """Status checks for the `codex` CLI."""

    id = "codex"
    label = "Codex CLI"

    def is_available(self) -> bool:
        """True if `codex` is on PATH."""
        return bool(shutil.which("codex"))

    def get_status(self) -> CodingAgentStatus:
        """Run `codex --version` and a no-op exec to infer auth state."""
        exe = shutil.which("codex")
        if not exe:
            return CodingAgentStatus(
                False, None, None, None, "Executable 'codex' not found in PATH"
            )

        try:
            proc = subprocess.run(
                [exe, "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
            version = proc.stdout.strip() if proc.returncode == 0 else None

            auth_proc = subprocess.run(
                [exe, "exec", "--sandbox", "danger-full-access", "echo ping"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            auth_out_str = auth_proc.stdout.lower() + auth_proc.stderr.lower()

            auth_ok = True
            error = None
            if auth_proc.returncode != 0:
                if (
                    "login" in auth_out_str
                    or "unauthorized" in auth_out_str
                    or "token" in auth_out_str
                ):
                    auth_ok = False
                    error = "Not authenticated. Run 'codex login' in terminal."
                else:
                    error = f"Auth check failed (exit {auth_proc.returncode}): {auth_out_str[:100]}"

            return CodingAgentStatus(True, version, auth_ok, exe, error)
        except Exception as e:
            return CodingAgentStatus(True, None, None, exe, f"Error getting status: {e}")


class ClaudeCodeCliBackend:
    """Status checks for the `claude` CLI."""

    id = "claude-code"
    label = "Claude Code CLI"

    def is_available(self) -> bool:
        """True if `claude` is on PATH."""
        return bool(shutil.which("claude"))

    def get_status(self) -> CodingAgentStatus:
        """Run `claude --version` and a no-op prompt to infer auth state."""
        exe = shutil.which("claude")
        if not exe:
            return CodingAgentStatus(
                False, None, None, None, "Executable 'claude' not found in PATH"
            )

        try:
            proc = subprocess.run(
                [exe, "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
            version = proc.stdout.strip() if proc.returncode == 0 else None

            auth_proc = subprocess.run(
                [exe, "-p", "echo ping"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            auth_out_str = auth_proc.stdout.lower() + auth_proc.stderr.lower()

            auth_ok = True
            error = None
            if auth_proc.returncode != 0:
                if (
                    "login" in auth_out_str
                    or "unauthorized" in auth_out_str
                    or "token" in auth_out_str
                ):
                    auth_ok = False
                    error = "Not authenticated. Run 'claude login' in terminal."
                else:
                    error = f"Auth check failed: {auth_out_str[:100]}"

            return CodingAgentStatus(True, version, auth_ok, exe, error)
        except Exception as e:
            return CodingAgentStatus(True, None, None, exe, f"Error getting status: {e}")


def get_all_cli_agents() -> list[CodingAgentBackend]:
    """Return every known coding-agent CLI backend."""
    return [CodexCliBackend(), ClaudeCodeCliBackend()]
