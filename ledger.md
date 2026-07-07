# Blockers and Required Inputs

Use this ledger to track any missing information, unverified assumptions, or blocked dependencies before running a wargame simulation or execution.

*Example:* `[Variable Placeholder: Target OS for the agent deployment]`
*Example:* `[Credential: API key for the remote endpoint]`

---

**Pending Clarifications:**
- [x] 2026-07-06T02:43:04Z [USER] [Mediator] Define a measurable CTA-routing acceptance target and maximum false-positive rate; "any conceivable CTA" is not testable.
  **ANSWER:** Use conservative confirmation routing (prompt user for confirmation on ambiguous commands). Target >95% accuracy for explicit commands.
- [x] 2026-07-06T02:43:04Z [USER] [Mediator] Decide whether contextual corrections update a running agent or cancel-and-replace it when the backend has no live-input channel.
  **ANSWER:** Cancel-and-replace. Stop the current subprocess and spawn a new one with the appended context.
- [x] 2026-07-06T02:43:04Z [ASSUMPTION] [Mediator] One coordinating backend receives compound workflows unless the request explicitly requires distinct agents.
  **ANSWER:** Agreed.
- [x] 2026-07-06T02:43:04Z [USER] [Wake words] Supply/confirm the exact openwakeword model IDs/files and their persona names; examples alone do not identify loadable models.
  **ANSWER:** Discover available local wake models on startup; if none are found for secondary personas, log a warning and skip the multi-wake-word feature gracefully.
- [x] 2026-07-06T02:43:04Z [USER] [Wake words] Define simultaneous-trigger behavior and acceptable persona-switch latency/false-activation rates.
  **ANSWER:** Highest confidence model wins. <500ms latency.
- [x] 2026-07-06T02:43:04Z [ASSUMPTION] [Wake words] The existing single-model environment keys remain supported as a one-entry mapping.
  **ANSWER:** Agreed.
- [x] 2026-07-06T02:43:04Z [USER] [WebSocket] Decide whether port bind failure aborts the voice session or starts it in a visibly degraded state.
  **ANSWER:** Start in a visibly degraded state. The voice session must continue.
- [x] 2026-07-06T02:43:04Z [USER] [WebSocket] Decide whether new clients receive an active-job snapshot/replay or only future events.
  **ANSWER:** Only future events for V1.
- [x] 2026-07-06T02:43:04Z [ASSUMPTION] [WebSocket] Version 1 binds to loopback only and exposes lifecycle metadata, not raw agent output.
  **ANSWER:** Agreed. Loopback only.