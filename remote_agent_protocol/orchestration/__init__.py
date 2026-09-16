"""Local / Cloud / Local+Cloud persona orchestration.

The persona is a dispatcher, not the worker: this package decides whether an
utterance's ROUTING DECISION (already produced by ``intent_router``) needs
cloud-grade reasoning to resolve -- which harness, how much to trust it, how
to read back an ambiguous result -- on top of the existing local
``intent_router``/``agent_bridge`` dispatch pipeline. Nothing here replaces
that pipeline; ``agent_bridge.AgentBridge`` remains the sole source of truth
for job state, and ``intent_router.IntentRouter`` remains the sole source of
the tiered dispatch/confirm/none decision.

Lifecycle: UNDERSTAND -> ROUTE -> DISPATCH -> TRACK -> INTERPRET -> RELAY,
implemented end to end by :class:`orchestration.orchestrator.PersonaOrchestrator`.

Modules:
    models       -- plain data (modes, states, the persisted StructuredDecision).
    risk         -- pure risk-scoring heuristic (tunable, not authoritative).
    providers/   -- ModelProvider abstraction: LocalProvider (Ollama) and
                    CopilotProvider (the official ``github-copilot-sdk``).
    concurrency  -- global/per-harness caps and duplicate-task detection.
    quota        -- quota-aware routing strategy (Economy/Balanced/
                    Performance/Cloud Preferred).
    telemetry    -- orchestration metrics, kept separate from persona memory.
    orchestrator -- the PersonaOrchestrator itself.
"""
