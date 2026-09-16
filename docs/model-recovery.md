# Agent model recovery

Verified 2026-07-05 against the installed CLIs and current upstream docs.

## Supported commands

| Agent | Interactive/persistent switch | Headless command used by this app |
|---|---|---|
| CodePuppy 0.0.591 | `/model chatgpt-gpt-5.5` | `code-puppy --model chatgpt-gpt-5.5 -p "<task>"` |
| Hermes-Agent 0.18.0 | `/model openai-api:gpt-5.5 --global` | `hermes chat --resume <session_id> --provider openai-api --model gpt-5.5 -q "<task>"` |
| OpenClaw 2026.9.4 | `openclaw models set openai/gpt-5.5` | Not in `AGENT_MODEL_TARGETS` yet |

CodePuppy encodes the provider in its configured model key; it has no separate
provider-only switch. Hermes uses `openai-api` for an OpenAI API key and
`openai-codex` for Codex/ChatGPT OAuth. OpenClaw accepts canonical
`provider/model` references and can verify them with `openclaw models status
--json` or `openclaw models list --provider openai`.

OpenClaw is now current (2026.9.4, previously 2026.1.29) and is configured in
`AGENT_BACKENDS` as `openclaw agent exec "{task}" --state-dir <persistent
dir>` -- verified live 2026-09-13 to run a headless turn cleanly (`"ok":
true`) without an interactive approval prompt. `--state-dir` must point at a
directory that already exists: OpenClaw does not create it, and its default
per-run temp directory hit a Windows `EBUSY` error on cleanup that turned an
otherwise-successful turn into a reported failure. Model-switch support
(`AGENT_MODEL_TARGETS`) has not been added yet; `openclaw models set` above is
unverified against the current CLI.

## Runtime behavior

The bridge classifies provider failures from streamed output:

- `quota`: `usage_limit_reached`, `insufficient_quota`, exhausted quota,
  billing hard limits, exhausted credits, or `resource_exhausted`; fatal and
  stopped immediately.
- `rate_limit`: HTTP 429, `rate_limit`, or `too many requests`; recorded, but
  the agent may continue its own retry policy.
- `capacity`: explicit provider/server overload or capacity messages.

Terminal failures are spoken directly. A quota failure records the affected
agent and task, then prompts the user to switch models. Supported follow-ups:

```text
change to OpenAI
switch Code Puppy to OpenAI
use the highest latest OpenAI model for Hermes
switch Code Puppy to OpenAI and retry
retry that task
```

“Highest/latest” is not guessed from marketing names. It resolves through the
explicit `AGENT_MODEL_TARGETS` allowlist in `config.py`; as of this verification
that target is GPT-5.5. A switch without “retry” only changes the next-run
override. “And retry” or a separate “retry” replays the remembered task once;
the recovery record is consumed before launch, preventing an automatic loop.

## Limits

- A failure can only be classified immediately when the child CLI emits it.
  A completely silent or buffered CLI is stopped by the five-minute job limit.
- Generic 429s may be transient, so they are not killed on the first line.
- Switching providers does not create credentials or restore quota. Missing
  auth fails normally and is reported; secrets are never accepted by voice.
- The historical CodePuppy quota record named GPT-5.5 on a Plus plan. Switching
  back to OpenAI will not help until that OpenAI usage window resets or a
  different authenticated OpenAI route has capacity.

Upstream references:

- CodePuppy: <https://github.com/mpfaffenberger/code_puppy>
- Hermes CLI: <https://github.com/NousResearch/hermes-agent/blob/main/website/docs/reference/cli-commands.md>
- OpenClaw models: <https://docs.openclaw.ai/cli/models>
