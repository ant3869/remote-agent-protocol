---
name: pick-agent
description: Choosing which agent should do a task when the user doesn't name one
---

# Picking the agent

1. `recall` "agent preference" first: if the user has said who they want for this
   kind of work, use that agent.
2. Otherwise match the work:
   - Code in a repository (fix, build, refactor, review, tests): **codex** first,
     then **claude-code**, then **code-puppy**.
   - Research, writing, planning, long multi-step work: **hermes**.
   - Email, calendar, messages, anything in the user's accounts: **openclaw**,
     then **hermes**.
3. Skip an agent the latest `check_agents` or `list_agents` says is down. An agent
   that is only checking is fine to use.
4. Say which agent you chose and why in a few words, then call `start_task` with
   instructions that stand on their own (the agent sees none of this
   conversation) and a short subject the user would recognize.
