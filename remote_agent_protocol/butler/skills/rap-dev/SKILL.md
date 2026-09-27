---
name: rap-dev
description: When the user wants work done on the Remote Agent Protocol (RAP) project itself -- bugs, features, tests, the avatar, this assistant
---

# Working on RAP

1. Use **codex** (then **claude-code**) unless the user names an agent.
2. Build instructions that stand on their own, including:
   - "The project is Remote Agent Protocol at H:\Program Files (oss)\remote-agent-protocol.
     Read AGENTS.md first."
   - The change the user asked for, in their words, plus any file, error, or log
     line they mentioned.
   - "The app code is in remote_agent_protocol/. src/pipecat is a vendored
     library: do not edit it."
   - "Run the tests for what you change with .venv\Scripts\python -m pytest
     tests/<file> and lint with .venv\Scripts\python -m ruff check. Report which
     files you changed and the test results. Don't commit or push unless asked."
3. Subject: a few words the user would use for it ("avatar mouth fix").
4. When it finishes, relay the files changed and whether tests passed.
