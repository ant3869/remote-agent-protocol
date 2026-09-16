# Real-world orchestration evaluation checklist

Purpose: watch the Local / Cloud / Hybrid orchestrator (`remote_agent_protocol/orchestration/`)
handle real conversation, so its actual behavior — not a scripted assertion —
can inform threshold tuning later. Current defaults: **mode=hybrid,
quota_strategy=balanced**.

**Do not treat any row's "what to watch" column as the required outcome.**
The point is to observe what the system actually does with an ordinary
phrasing of that situation, and write it down. A "wrong-looking" result is
data, not a failure — note it and move on.

## Where to look while you talk

Open the web GUI's **Status** view. The "Persona orchestration" panel there
(added for this) shows:

- current mode / quota strategy, Copilot auth status
- **Recent routing decisions** — a live list, newest first, of every routed
  turn: local/cloud, harness (with `original → refined` if Copilot changed
  the pick), risk score, reasoning time, and the reason text; paired outcome
  rows show success/failed/cancelled once a job finishes.
- aggregate stats (local resolution %, cloud escalation %, routing
  failures %, fallback rate)

Everything in that panel comes straight from `data/orchestration_telemetry.jsonl`
(gitignored) — the full row detail (risk factor breakdown, confidence,
etc.) is in that file if you want to look closer than the panel shows.

## How to record results

For each turn: say the line (in your own words is fine — don't read it
verbatim if it feels unnatural), then fill in what the panel actually
showed. A simple table works:

| # | What you said (paraphrase OK) | Route shown | Harness shown | Risk score | Outcome | Anything surprising? |
|---|---|---|---|---|---|---|
| 1 | | | | | | |

## The 18 turns

Run them roughly in this order within one longer conversation — several
turns deliberately depend on the one before.

1. **Ordinary conversation.** Ask something with no real-world component —
   e.g. "what's a good name for a golden retriever puppy?" Watch: no routing
   decision should fire at all (this is chat, not a delegation turn).

2. **Persona opinion/style.** "What do you think about pineapple on pizza?"
   Same expectation as #1 — confirms ordinary chat doesn't get routed
   regardless of phrasing.

3. **Simple explicit delegation.** "Check tomorrow's weather for me." No
   harness named — watch which one it picks and via which tier (the panel's
   reason text should say something like "keyword net matched" or similar).

4. **Explicit harness selection (A).** "Tell Hermes to look up the latest AI
   news." Watch: harness should be exactly what you named, regardless of
   mode.

5. **Explicit harness selection (B), a different harness.** "Have Code Puppy
   check my project folder for obvious syntax errors." Same check as #4,
   different harness — confirms explicit selection isn't harness-specific
   behavior.

6. **Ambiguous harness selection.** "Something's going on with my computer,
   can someone take a look?" No harness named, vague goal. Watch which
   harness gets picked, at what confidence, and whether it escalates to
   cloud reasoning to decide.

7. **Multiple constraints.** "Search my Downloads folder for PDF files over
   10MB modified in the last week and list them." Watch the risk score's
   constraint-complexity contribution and which harness handles a
   multi-condition task like this.

8. **Vague capability reference.** "There's some tool that organizes photos
   by date, I forget the name — can you check if I have it?" This should
   hit the dedicated capability-request path, not the generic classifier.

9. **A duplicate-task attempt.** While the job from #7 or #8 is still
   running (check the Agents panel), ask for the *same* task again. Watch:
   should be refused with a "not dispatched" message, not a second job.

10. **Follow-up on an active job.** While something is still running, ask
    "is that done yet?" or "what's the status on that?" Watch: should
    answer from job state, not start anything new.

11. **Cancellation.** "Cancel that" / "never mind, stop it." Watch: the
    active job should stop, and nothing new should dispatch as a result of
    the cancellation itself.

12. **Correction of an earlier request.** Start a task ("clean up my
    downloads folder"), then immediately say "actually, use my Desktop
    folder instead." Watch: should revise/replace the same job, not launch
    a second one alongside it.

13. **Contextual reference — "same thing, other project."** After a task
    completes (e.g. one from earlier), say "now do the same thing with my
    documents folder instead." Watch whether it correctly reconstructs the
    task from context rather than dispatching something generic or
    misfiring on a pronoun with no antecedent.

14. **Prior-job context, no restated task.** After a job finishes and its
    result is read back, ask a follow-up that only makes sense with that
    result in hand — e.g. "what were the top 3 of those?" Watch: should
    answer from the completed job's result, not start a new job.

15. **Conversation immediately following a completed job.** Right after a
    job finishes, say something purely conversational — "thanks! how's your
    day going?" Watch: confirms a just-completed job doesn't make ordinary
    chat look like a delegation.

16. **A turn that should plausibly escalate to Copilot.** Say something
    genuinely underspecified and low-confidence — e.g. "I don't know, maybe
    look into whether something's off with the files, not sure what
    exactly." Watch the risk score and whether it actually escalates (it
    may not — that's useful data either way).

17. **A turn that should plausibly stay local.** A confident, simple,
    explicitly-named request — e.g. "tell Hermes to check today's weather."
    Watch that it resolves entirely locally with a low risk score and near-
    zero reasoning latency.

18. **Malformed/failing harness output.** Give a task likely to come back
    empty, garbled, or erroring — e.g. reference a folder that doesn't
    exist, or ask a harness to do something it plainly can't. Watch: the
    spoken/relayed result should say the job failed or returned nothing
    useful — never present an empty/garbled result as a success.

## After the session

- Skim `data/orchestration_telemetry.jsonl` for anything the panel's summary
  hides (e.g. exact risk factor breakdowns on turns 6, 7, 16).
- Note which of the 18 turns felt like the routing "did the right thing" vs.
  surprised you, and *why* (score too low/high, wrong harness, escalated
  when it didn't need to, or vice versa). That's the raw material for
  threshold tuning later — not something to fix mid-session.

## How much usage before tuning thresholds

Treat this checklist as one data point, not the whole dataset. Recommended
before revisiting `ORCHESTRATION_LOCAL_THRESHOLD` / `ORCHESTRATION_CLOUD_THRESHOLD`
/ `ORCHESTRATION_HARNESS_CONFIDENCE_FLOOR`:

- **At least 3-5 sessions** like this one, spread across different days/moods
  of phrasing (not all 18 turns back-to-back once) — routing that looks fine
  in one focused test run can behave differently in your normal, less
  deliberate speech patterns.
- **At least ~50-100 routed turns total** in `orchestration_telemetry.jsonl`
  (`stage: "route"` rows) before the aggregate percentages in the Status
  panel are stable enough to mean something — with only a handful of rows,
  one unusual turn swings the percentage a lot.
- Specifically wait until you've seen **at least a few real cloud
  escalations** (not just forced via mode="cloud") — hybrid mode may
  legitimately escalate rarely if your phrasing tends to be direct, and
  that's fine, but you need a few real examples to judge whether the
  escalations that did happen were worth it.
