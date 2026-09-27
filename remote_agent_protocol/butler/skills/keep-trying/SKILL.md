---
name: keep-trying
description: When the user wants a failed task done anyway, or says to keep trying agents until one works
---

# Keep trying until one works

1. Find the task with `list_tasks` (scope `recent`) or `task_status`; don't ask the
   user which one if the most recent failed task is the obvious one.
2. Note which agents already tried it (the task's earlier attempts).
3. Pick the next agent that suits the work and isn't known to be down, using the
   order from the pick-agent skill, and call `retry_task` on it. Never retry on an
   agent that already failed this task with quota, auth, or model-not-found.
4. Tell the user in one sentence: who failed and why, and who is trying now.
5. If every suitable agent has failed, stop and say so with each agent's reason.
   Don't loop.
