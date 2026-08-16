"""
Mock agent backend -- pretends to be Hermes/OpenClaw for testing the bridge.

Usage: python scripts/mock_agent.py "<task text>"

Special task prefixes (for tests):
  sleep:<secs> ...   -> sleep that long between steps (cancel testing)
  fail ...           -> exit non-zero after printing an error line
"""

import json
import sys
import time


def main() -> int:
    # Tasks arrive as speech, and speech carries curly quotes, dashes, and the
    # occasional emoji. Windows' console codec cannot encode those, so echoing
    # the task crashed the agent mid-job (jess_agent_history 2026-08-08 19:00).
    # The bridge already reads this stream as UTF-8.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    task = sys.argv[1] if len(sys.argv) > 1 else "(no task)"
    delay = 0.05
    if task.startswith("sleep:"):
        head, _, rest = task.partition(" ")
        delay = float(head.split(":", 1)[1])
        task = rest or task

    print(f"[mock-agent] accepted task: {task}", flush=True)
    time.sleep(delay)
    print("[mock-agent] working on it...", flush=True)
    time.sleep(delay)

    if task.startswith("fail"):
        print("[mock-agent] ERROR: simulated failure", flush=True)
        return 1

    result = f"Mock agent simulated completion for: {task}"
    print(f"[mock-agent] RESULT: {result}", flush=True)
    print(
        "@@JESS_STATUS "
        + json.dumps({"state": "completed", "summary": "Mock task completed", "result": result}),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
