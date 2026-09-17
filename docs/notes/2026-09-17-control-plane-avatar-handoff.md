# Control-plane and Butler handoff

Status as of 2026-09-17. This note covers the work completed after the
orchestration handoff; retain `docs/notes/orchestration-handoff.md` as the
source of truth for cloud-routing decisions.

## What is settled

- The live control plane probes each configured harness directly and reports
  source-backed availability, status, and RAP-owned task evidence. A failed or
  quota-limited harness is isolated from the others.
- Inspection/status work remains local to RAP. Do not delegate inspection to
  Code Puppy, and do not represent a CLI probe as access to an external agent
  session.
- Cancellation and redirection are limited to RAP-owned `AgentBridge` jobs.
  External CLI sessions are neither inspected nor cancelled.
- The UI's intended identity treatment is ice blue for the operator plus muted
  lavender/violet for persona and harness attribution. Avoid reintroducing the
  former heavy coral labels or thick colored rails without an explicit design
  request.

## Butler startup reliability

The active Butler is `web_app/avatar/frame-avatar-scene.js`, a Canvas 2D frame
renderer, not a WebGL renderer. A blank panel is therefore not a GPU/WebGL
failure. The renderer preloads critical frame images before it attaches its
canvas; previously a browser resource that never completed could leave the
panel blank forever.

The current fix keeps the animation itself unchanged:

- versioned frame URLs bypass an obsolete immutable browser-cache entry;
- image preloads time out after six seconds and enter the existing fallback;
- static Butler frames are served as `image/webp`;
- the obsolete `/avatar.js` page request is gone.

For an existing RAP process, fully quit and relaunch once so the Python static
server picks up the media-type fix. If the panel is still blank afterward,
capture the browser console and network result for a `runtime_512_v1/*.webp`
request before changing animation behavior.

## Verification and next work

- Focused control-plane acceptance completed with direct probes, progress,
  cancellation, redirection, stale-after-restart behavior, and Code Puppy
  exclusion. A real Hermes task hit HTTP 429; this is correctly reported as a
  harness/provider failure, not a control-plane pass or routing bug.
- Avatar source verification: `node --test tests/js/frame-avatar-scene.test.mjs`
  and the focused `test_web_gui.py` avatar checks pass. Browser attachment was
  unavailable during the final source verification, so a post-relaunch visual
  confirmation remains useful.
- Do not alter the Butler art, frame sequence, or consulting-state mapping
  unless the user explicitly asks. Diagnose load/visibility/resource failures
  first.
