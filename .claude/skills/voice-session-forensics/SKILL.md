---
name: voice-session-forensics
description: Diagnose a real spoken session from its logs -- find where the user got frustrated, what they asked, what the app actually did, and which layer caused the gap. Use when told "it's not working like I envisioned", "check the logs from my last conversation", "it cut me off", "it keeps delegating everything", or any complaint about a live voice session rather than a specific crash.
---

# Voice session forensics

A spoken complaint ("it's not working right") never names a layer. This skill turns
one into a specific defect, in a specific file, with the log line that proves it.

The user's own words in the transcript are the bug report. Quote them.

## Read these five, in this order

| Source | Answers |
| --- | --- |
| `logs/speech-to-speech-client.log` | The conversation: `USER:` / `ASSISTANT:` / `WAKE WORD:` / `TURN TIMING:` |
| `logs/speech-to-speech-server.log` | Wake detection, VAD segments, STT transcripts |
| `data/jess_runtime.log` | `Brain routing[...]`, `orchestration[...]`, `delegation ->` |
| `data/jess_agent_history.json` | What each job was asked and what it returned |
| `data/orchestration_telemetry.jsonl` | Per-turn risk factors and route |

Check freshness first (`ls -la --time-style=...`) and confirm the session postdates
whatever you last changed -- logs are truncated per run, so a restart erases the
previous run's evidence. Aggregate with a script; never paste raw logs into context.

Windows note: these logs are UTF-8 with box-drawing separators. Use
`PYTHONIOENCODING=utf-8` and split `data/jess_runtime.log` on `"│"`, not `|`.

## The four questions, in order

Work outward from the microphone. Each layer's output is the next layer's input, so
a defect upstream masquerades as a defect downstream.

1. **Did it hear the whole sentence?** In the server log, every `Speech soft-ended`
   line carries `segment=` and `active=`. `segment - active` is the silence that
   ended the turn. If that constant is near `S2S_VAD_MIN_SILENCE_MS`, a thinking
   pause is closing turns. `rev=1`, `rev=2` on one turn id means it closed and
   reopened -- the tail nearly became its own turn. A fragment committed on its own
   ("I don't know that.") is the smoking gun, because the router then acts on it.
2. **Did it understand the goal?** Every `Brain routing[tier]` line carries the
   tier, intent, confidence and reason. The reason is the classifier's own words --
   when it says "does not provide clear task" and still dispatches, the prompt is
   contradicting itself.
3. **Did it pick the right harness, or answer itself?** A question RAP can answer
   from local state must never become a job. Liveness ("is X running?") is the
   classic: `parse_agent_rollcall` answers it instantly, and delegating it asks a
   possibly-broken agent to report on its own health.
4. **Did the answer come back?** In the job history, `status: done` with an empty
   `result` is worse than a failure -- the user hears "it delivered nothing".

## Frustration signatures

Search the transcript for the user repeating, correcting, or asking what happened.
Each maps to a layer:

| What they say | What it means | Look at |
| --- | --- | --- |
| "I was supposed to have X do Y" | wrong harness, or announcement names the wrong agent | `delegation ->` lines |
| "All I wanted was to know if it works" | a status question became a job | `parse_agent_rollcall` |
| "What does that mean? Did it work?" | an announcement reported without answering | job `result` field |
| "I don't understand" | async results interleaved with a live thread | `<response started>` ordering |
| half a sentence as its own turn | VAD cut them off | `segment`/`active`/`rev=` |
| the same request twice | first attempt returned nothing | empty `result` |

## Latency: separate the two kinds

`TURN TIMING` gives `stt=` and `first_audio=`. Compare a pure chat turn against a
delegating one. If a chat turn with no job is already slow, the cost is LLM+TTS and
routing changes will not fix it. If only delegating turns are slow, the fix is to
stop delegating things the persona can answer.

## Fix at the layer that caused it

- **Cut off mid-sentence** -> `S2S_VAD_*` in `config.py`, forwarded by
  `voice_stack.build_stages`. The launcher in the sibling checkout forwards `%*` and
  keeps the last duplicate, so RAP owns these. Raise the reopen windows first: they
  are checked only when speech actually arrives, so they cost nothing on turns the
  user really had finished. `min_silence_ms` is paid on every reply.
- **Delegates what it could answer** -> `_CLASSIFIER_SYSTEM` in `intent_router.py`.
  A small model steers on the examples far more than the prose; add the missing
  shape as an example rather than only arguing with it in the instructions.
- **Misses a spoken command shape** -> the parsers in `voice_commands.py`. They
  anchor on a verb, so politeness in front of it ("can you", "all I wanted you to
  do is") hides the command. Strip the lead-in; never loosen into matching real work.
- **Job returns nothing** -> `agent_bridge.py`; a `done` job with an empty result
  needs the reason relayed, not silence.

Never tune orchestration thresholds to paper over a routing bug. The score is
downstream of the classifier: if the wrong turns are being dispatched, the risk
score of those turns is not the problem.

## Prove it against the real utterances

Before and after, run the user's own sentences through the parser or the live
classifier and show the table. This is the difference between a plausible fix and a
verified one.

```bash
# the classifier, against Ollama, on their actual words
PYTHONIOENCODING=utf-8 .venv/Scripts/python -c "
import asyncio, sys; sys.path.insert(0,'.')
from remote_agent_protocol import config as cfg
from remote_agent_protocol.intent_router import classify_with_ollama
async def main():
    for t in ['<their utterance>']:
        v = await classify_with_ollama(t, host=cfg.OLLAMA_HOST, model=cfg.INTENT_MODEL, timeout_secs=25)
        print(v['intent'], v['confidence'], t)
asyncio.run(main())"
```

Then: focused tests, `python -m voice_probe run --classifier stub` (the 131-case
routing corpus), and the full sweep from `docs/notes/orchestration-handoff.md`.

## Report it as their complaint, answered

Lead with the sentence they said, then the log line that explains it, then the fix.
A table of before/after on their own utterances beats any description. Say plainly
which complaints you did **not** address -- an unfixed frustration they raised is
worse than an unmentioned one.
