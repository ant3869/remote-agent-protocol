from __future__ import annotations

import runpy
from pathlib import Path

fix_path = Path(__file__)
task_path = fix_path.with_name("task7.py")
source = task_path.read_text(encoding="utf-8")
old = "background: radial-gradient(circle at 50% 35%, var(--surface-raised), var(--surface-app));"
if source.count(old) != 1:
    raise RuntimeError(f"expected one forbidden gradient in task7, found {source.count(old)}")
source = source.replace(old, "background: var(--surface-raised);", 1)
old_add = '["git", "add", "remote_agent_protocol/web_app", "tests/test_web_gui.py", ".github/avatar_tasks/task7.py"],'
new_add = '["git", "add", "remote_agent_protocol/web_app", "tests/test_web_gui.py", ".github/avatar_tasks/task7.py", ".github/avatar_tasks/task7_fix.py"],'
if source.count(old_add) != 1:
    raise RuntimeError("could not extend task7 cleanup staging")
source = source.replace(old_add, new_add, 1)
task_path.write_text(source, encoding="utf-8")
fix_path.unlink()
runpy.run_path(str(task_path), run_name="__main__")
