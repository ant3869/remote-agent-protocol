"""Smoke test: run a real backend through AgentBridge end-to-end.

Usage: python scripts/smoke_agent_bridge.py [agent] [task]
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from remote_agent_protocol import agent_bridge  # noqa: E402
from remote_agent_protocol import config as cfg  # noqa: E402


async def main(agent: str, task: str) -> None:
    events: list[dict] = []
    done = asyncio.Event()

    async def fin(_job):
        done.set()

    bridge = agent_bridge.AgentBridge(cfg.AGENT_BACKENDS, events.append, fin)
    await bridge.start(agent, task)
    await asyncio.wait_for(done.wait(), timeout=120)

    print("events:", [e["event"] for e in events])
    print("status:", events[-1]["status"])
    print("summary:", events[-1].get("summary"))


if __name__ == "__main__":
    agent = sys.argv[1] if len(sys.argv) > 1 else "hermes"
    task = sys.argv[2] if len(sys.argv) > 2 else "Reply with exactly: JESS BRIDGE LIVE"
    asyncio.run(main(agent, task))
