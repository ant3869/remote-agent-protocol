"""Centralized resolution of external CLI executables for a shell-less launch.

Windows' ``CreateProcess`` (what ``asyncio.create_subprocess_exec`` and
``subprocess.Popen(shell=False)`` both use) only auto-appends ``.exe`` to a
bare command name -- never ``.cmd``/``.bat`` -- so an npm-installed shim like
``codex.cmd`` fails to launch with ``WinError 2`` unless resolved to its real
path first. ``shutil.which`` already walks PATH using PATHEXT the way
cmd.exe does, so resolving through it before spawning is sufficient; this
module centralizes the one call that used to be duplicated in
``agent_bridge.py`` and ``remote_host.py``, plus a diagnostic for
``doctor.py`` that flags a same-named ``.exe`` sitting elsewhere on PATH --
see docs/notes/2026-09-25-harness-audit.md for the investigation that found
one (an unrelated third-party installer squatting on the name ``openclaw``).
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


def resolve_executable(name: str) -> str:
    """Resolve a bare executable name to an absolute path via PATH/PATHEXT.

    Returns ``name`` unchanged when it can't be found, so a caller's existing
    "could not launch <name>" error handling still reports the name the
    config asked for.
    """
    return shutil.which(name) or name


@dataclass(frozen=True)
class ExecutableShadow:
    """A same-named ``.exe`` found elsewhere on PATH than the resolved shim."""

    resolved: str
    shadow: str


def find_shadow_executable(
    name: str,
    *,
    path_env: str | None = None,
    is_windows: bool | None = None,
) -> ExecutableShadow | None:
    """Return a shadowing same-named ``.exe`` if one exists on PATH, else None.

    Only meaningful when the resolved executable is *not* itself a ``.exe``
    (a ``.cmd``/``.bat`` shim, or a POSIX script with no extension): a
    same-named ``.exe`` elsewhere on PATH is the binary a shell-less launch
    would silently run instead, regardless of PATH order, because Windows'
    implicit search only ever tries ``.exe``. Never touches the real
    ``os.environ``/``os.name`` unless the caller omits the keyword overrides,
    which keeps this deterministic and platform-independent under test.
    """
    is_windows = (os.name == "nt") if is_windows is None else is_windows
    if not is_windows:
        return None
    resolved = shutil.which(name)
    if not resolved:
        return None
    resolved_path = Path(resolved)
    if resolved_path.suffix.lower() == ".exe":
        return None
    path_env = os.environ.get("PATH", "") if path_env is None else path_env
    for directory in path_env.split(os.pathsep):
        if not directory:
            continue
        candidate = Path(directory) / f"{name}.exe"
        try:
            if not candidate.is_file():
                continue
            if candidate.resolve() == resolved_path.resolve():
                continue
        except OSError:
            continue
        return ExecutableShadow(resolved=str(resolved_path), shadow=str(candidate))
    return None
