---
name: check-email
description: When the user asks to check, summarize, or triage their email or inbox
---

# Checking email

1. Start the task on **openclaw** (it has the user's accounts); if it is down,
   use **hermes**. Subject: "email check".
2. Instructions to send, adjusted to what the user asked for:
   "Check the user's inbox for messages from the last 24 hours. List only what
   needs attention: messages from real people, anything with a deadline, bills or
   payments, security alerts, and replies the user is waiting on. For each give
   the sender, the subject, and one line on what it wants. Skip newsletters,
   promotions, and automated notifications. Do not reply to, delete, archive, or
   mark anything."
3. Reading and summarizing needs no permission: once start_task says it has
   started, tell the user it is under way and that the result will be
   announced. Never ask them to okay a check that is already running.
4. Sending, deleting, or changing email is different: do it only when the user
   explicitly asks, and it goes through confirmation.
