---
name: briefing
description: A catch-up briefing when the user asks what they missed, for a rundown, or says good morning
---

# Briefing

1. Call `list_tasks` with scope `recent`, and `check_agents` for every agent.
2. Speak at most four short sentences:
   - Anything that failed or is waiting on the user comes first, by subject.
   - Then what finished, by subject, with the agent that did it.
   - Then what is still running.
   - Then one line on the agents: which are up, and which are down and why.
3. Leave out anything with nothing to report; don't say "no failures".
4. End by offering the single most useful next step, if there is one, such as
   retrying a failed task on an agent that is up. Don't start it until the user
   says so.
