---
name: agent-trouble
description: When an agent is down, failing, or the user asks what is wrong with one
---

# Agent trouble

1. Call `check_agents` for that agent, and `list_tasks` (scope `recent`) to see
   its latest failures.
2. Explain the cause in plain words, then what fixes it:
   - Out of quota, rate limit, or capacity: the provider account needs credit or
     time; offer `set_agent_model` to switch the agent to another provider, or
     another agent for now.
   - Authentication: the agent needs to be logged in again on this PC.
   - Model not found: its configured model name is wrong; offer `set_agent_model`.
   - No response or a failure within a second: the agent's program or gateway
     isn't running or can't start; the user should open it once by hand.
   - Unexpected response: the agent answered but not as asked; it is probably fine
     for real work.
3. Offer to move any waiting task to a working agent. Don't do it unasked.
