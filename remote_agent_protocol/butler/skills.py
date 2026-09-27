"""Skills: instruction packs the Butler loads when a request calls for one.

A skill is a folder holding a ``SKILL.md`` -- the same layout Claude Code
uses -- whose frontmatter gives a ``name`` and a one-line ``description``, and
whose body says how to handle that kind of request. The Butler sees every
skill's name and description, and calls ``use_skill`` to read the body when
one fits. A skill only directs the tools the Butler already has; it grants
none.

Skills come from the packaged defaults and then the user's folder
(``BUTLER_SKILLS_DIR``); a user skill with the same name replaces a default.
The folders are rescanned when they change, so a new or edited skill is
picked up without a restart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

BUILTIN_SKILLS_DIR = Path(__file__).with_name("skills")
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_MAX_BODY_CHARS = 8000
_MAX_DESCRIPTION_CHARS = 300


@dataclass(frozen=True)
class Skill:
    """One loaded skill."""

    name: str
    description: str
    instructions: str
    path: Path


def _normalize(name: str) -> str:
    return re.sub(r"[\s_]+", "-", name.strip().lower())


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split ``---``-fenced ``key: value`` lines from the body."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    for end in range(1, len(lines)):
        if lines[end].strip() == "---":
            fields = {}
            for line in lines[1:end]:
                key, sep, value = line.partition(":")
                if sep and key.strip() and not line.startswith((" ", "\t")):
                    fields[key.strip().lower()] = value.strip().strip("\"'")
            return fields, "\n".join(lines[end + 1 :])
    return {}, text


def parse_skill(path: Path) -> Skill | None:
    """Read one ``SKILL.md``; None (logged) when it can't be a usable skill."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning(f"Skipping unreadable skill {path}: {exc}")
        return None
    fields, body = _frontmatter(text)
    name = _normalize(fields.get("name") or path.parent.name)
    body = body.strip()
    description = fields.get("description", "").strip()
    if not description and body:
        description = body.splitlines()[0].lstrip("# ").strip()
    if not _NAME_RE.match(name) or not body or not description:
        logger.warning(f"Skipping skill {path}: it needs a name, a description, and instructions")
        return None
    return Skill(
        name=name,
        description=description[:_MAX_DESCRIPTION_CHARS],
        instructions=body[:_MAX_BODY_CHARS],
        path=path,
    )


class SkillLibrary:
    """The skills in a list of folders, later folders overriding earlier ones."""

    def __init__(self, *folders: str | Path | None):
        """Remember the folders to scan; missing ones are simply empty.

        Args:
            *folders: Folders containing ``<skill>/SKILL.md``, lowest priority first.
        """
        self._folders = [Path(folder) for folder in folders if folder]
        self._stamp: tuple = ()
        self._skills: dict[str, Skill] = {}

    def _signature(self) -> tuple:
        stamp = []
        for folder in self._folders:
            for path in sorted(folder.glob("*/SKILL.md")) if folder.is_dir() else ():
                try:
                    stamp.append((str(path), path.stat().st_mtime_ns))
                except OSError:
                    continue
        return tuple(stamp)

    def _refresh(self) -> None:
        signature = self._signature()
        if signature == self._stamp:
            return
        skills: dict[str, Skill] = {}
        for path_text, _mtime in signature:
            skill = parse_skill(Path(path_text))
            if skill is not None:
                skills[skill.name] = skill
        self._skills = skills
        self._stamp = signature

    def catalog(self) -> list[Skill]:
        """Every usable skill, by name."""
        self._refresh()
        return [self._skills[name] for name in sorted(self._skills)]

    def get(self, name: str) -> Skill | None:
        """A skill by name, forgiving case, spaces, and underscores."""
        self._refresh()
        return self._skills.get(_normalize(name))

    def prompt_section(self, limit_chars: int = 2500) -> str:
        """The catalog as one line for the Butler's system prompt ('' when empty)."""
        entries = []
        used = 0
        for skill in self.catalog():
            entry = f"{skill.name} ({skill.description})"
            if used + len(entry) > limit_chars:
                break
            entries.append(entry)
            used += len(entry)
        if not entries:
            return ""
        return (
            " Skills you can load with use_skill when a request matches one, then follow"
            f" its instructions: {'; '.join(entries)}."
        )
