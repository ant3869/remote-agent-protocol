# Mission Brief: The Omniscient Mediator & Universal Delegation Router

## Objective
Upgrade the primary voice assistant (the "Mediator") so it can accurately detect *any* conceivable call-to-action (CTA) from the user and seamlessly delegate the work to the appropriate background agent (Hermes, Code Puppy, OpenClaw, etc.). The Mediator must act as the ultimate conversational router, handling ambiguous requests, multi-step workflows, and implicit commands without forcing the user into rigid syntax.

## Requirements

### 1. Universal CTA Detection
The Mediator must accurately identify when it should handle a response itself (e.g., casual conversation, quick facts) versus when it must spawn a background agent. It must parse:
- **Direct Commands:** "Jarvis, write a Python script to scrape this site."
- **Implicit Commands:** "Man, I really wish this project had unit tests."
- **Compound Workflows:** "Can you research the latest OpenAI models and then write a blog post about them?"
- **Contextual Corrections:** "No, don't use requests, use httpx." (This must route directly into an already running agent's context).

### 2. Intelligent Delegation & Prompt Injection
When delegating, the Mediator cannot just pass raw STT text to the agent. It must:
- Extract the core objective from the user's speech.
- Inject relevant context from the persistent memory (mem0/Qdrant) or the current GUI state.
- Select the optimal agent backend for the specific task.

### 3. Graceful Handoffs and Homing
- The Mediator must explicitly announce the delegation out loud so the user knows work has started (e.g., "I'll have Hermes start writing those tests now").
- If an agent blocks on a destructive action, the Mediator must naturally ask the user for spoken confirmation.

## Wargaming Focus (Crucial Failure Points to Anticipate)
This feature touches the absolute core of the STT → LLM → Delegation pipeline. When simulating this move-by-move, the wargame must deeply explore these failures:
- **False Positives:** The Mediator thinks a casual statement ("I'm going to write some code today") is a command and accidentally spawns a rogue coding agent.
- **Context Loss:** The user gives a command that relies on something said 5 minutes ago, but the Mediator delegates a context-less prompt to the agent, causing it to fail.
- **Race Conditions:** The user speaks a rapid follow-up command ("Wait, actually do X") *while* the Mediator is mid-delegation, resulting in two conflicting agents running simultaneously.
- **Silent Failures:** The background agent crashes immediately, but the Mediator doesn't realize it and leaves the user waiting in silence.
- **Ambiguity:** The user asks for something that requires two different agents, or no configured agent has the capability to fulfill it.