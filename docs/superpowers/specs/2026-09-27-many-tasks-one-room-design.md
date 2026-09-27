# Many tasks, one room (roadmap phase D)

With the tool-calling Butler in place, phase D is data and limits.

## Task subjects that outlive a restart

The Butler's source of truth for a task is `butler.TaskLedger`: the subject
the model gave `start_task`, the instructions, and every attempt across
agents. The conversation hub's `TaskReference` records one attempt's ownership
and is not where the Butler resolves "the email thing", so the subject lives
on the ledger rather than being duplicated onto `TaskReference`.

The ledger is written to `BUTLER_TASKS_FILE` (default
`data/butler_tasks.json`) atomically after every create, attach, and amend,
and loaded at startup. It stays bounded at 50 tasks. Confirmation holds are
never written: the confirmation belongs to the session that raised it. An
unreadable file is logged and ignored, never fatal. Tests and the replay eval
use a private file or none.

## Per-harness caps

`ConcurrencyGuard` takes `harness_caps` (from
`ORCHESTRATION_HARNESS_JOB_CAPS_JSON`), overriding the default per-harness
cap for the named harnesses only. The global default rises from 2 to 4 so
several agents can work at once. Hermes keeps its own one-session lock in the
bridge regardless.

## "What's running" roll-up

`list_tasks(scope="recent")` returns open work plus work that finished within
`BUTLER_RECENT_TASK_SECS` (default 7200), and its summary leads with counts
("1 running, 1 finished, 1 failed in the last 2 hours.") followed by one line
per task with how long ago it ended. A finish time RAP cannot read keeps the
row rather than dropping it. `scope="active"` is unchanged apart from the
count prefix.
