"""
Mock agent backend -- pretends to be Hermes/OpenClaw for testing the bridge.

Usage: python scripts/mock_agent.py "<task text>"

Special task prefixes (for tests):
  sleep:<secs> ...   -> sleep that long between steps (cancel testing)
  fail ...           -> exit non-zero after printing an error line
"""

import sys
import time


def main() -> int:
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

    print(f"[mock-agent] RESULT: completed '{task}' successfully", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
