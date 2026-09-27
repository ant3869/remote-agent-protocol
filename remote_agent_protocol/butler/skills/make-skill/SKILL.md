---
name: make-skill
description: When the user asks you to learn, save, or create a new skill, routine, or way of handling something
---

# Making a skill

1. Work out with the user, briefly, when the skill applies and what should happen.
   Ask at most one question if something essential is missing.
2. Write it:
   - name: lowercase words joined by hyphens, e.g. "weekly-review".
   - description: one line saying *when* it applies. You choose skills from this
     line alone, so name the situations and phrases that should trigger it.
   - instructions: short numbered steps naming the tools to use (start_task,
     check_agents, list_tasks, remember, web_search...) and what to say. They can
     only use the tools you have.
3. Call `create_skill`. If one with that name exists, tell the user and only
   replace it if they say so.
4. Tell the user its name and when you'll use it.
