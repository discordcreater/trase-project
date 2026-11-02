# wfk

A small workflow engine that runs a four-step document triage (ingest, extract,
classify, notify) and survives being killed at any point. State lives in one SQLite
file. Steps retry with backoff, a step can be told to fail the workflow or to be
skipped, a new process can pick up where a dead one stopped, and the notify step
cannot send the same email twice unless the mail provider itself ignores
idempotency keys. Every state change is also written to an append-only event log,
so `history` can say what happened and what classify decided, and `replay` can
rebuild the tables from the log and check that they agree.

Python 3.9 or newer, standard library only (`sqlite3`). `pytest` is the only
development dependency.

```
wfk/
  spec.py     workflow spec as dataclasses, plus a small JSON loader
  store.py    SQLite: schema, the two state machines, transitions, effects ledger, events
  engine.py   Engine.start/run/cancel, StepContext, run_effect, rebuild_state (replay)
  tools.py    the Tool interface, the four triage tools, the FileNotifier receiver
  faults.py   fault injection for tests and the demo; the kernel never imports it
  cli.py      python -m wfk {start,run,status,cancel,history,replay}
tests/        31 tests in seven files
scripts/demo.sh
docs/DESIGN.md   design notes: data model, state machines, crash points, what I left out
```

## How to run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # pytest only
.venv/bin/python -m pytest                                             # 31 tests, about a second
scripts/demo.sh                                                        # everything below, in a temp dir

python -m wfk start examples/doc.json --id w1     # create and run; database is ./wfk.db
python -m wfk status w1
python -m wfk history w1 --kind DECISION          # what classify decided, and on which rule
python -m wfk replay w1                           # rebuild state from the log, diff against the tables
python -m wfk cancel w1                           # cooperative; honoured between attempts
python -m wfk run w1 [--take-over]                # resume after a crash
python -m wfk start doc.json --spec my_spec.json  # a JSON spec; pass the same --spec to run later
```

`python -m wfk` needs no install; run it from the repository root. The venv is only
for pytest. It runs on the system Python 3.9 as well as 3.14.

`start` and `run` take three fault flags for the demo and for poking at it by hand:
`--fail extract:2` makes extract raise `RetryableError` on its first two calls,
`--crash-after classify` kills the process (`os._exit`) right after classify's success
is committed, and `--crash-in-notify` kills it after the mail receiver accepted the
message but before the kernel recorded that.

Exit codes: 0 ok, 1 replay found an inconsistency, 2 the workflow ended FAILED or
CANCELLED, 3 the workflow is owned by another engine, 4 the store refused (not found,
already exists, illegal transition), 5 bad input or spec, 6 database error.

### What the demo shows

`scripts/demo.sh` takes about two seconds and leaves its files in a temp directory.

1. A clean run. `status` lists the four steps as SUCCEEDED and the email effect as
   DONE.
2. `--fail extract:2`. `history --kind RETRY_SCHEDULED` prints two rows with delays
   of 0.05 s and 0.1 s; extract succeeds on attempt 3 and ingest's attempt count is
   still 1.
3. `--crash-after classify`. The process dies after classify's success is on disk. A
   plain `run` is refused with exit 3 because the dead process still holds the claim;
   `run --take-over` finishes the workflow. The first three steps are not re-run and
   there is one line in the receiver's file.
4. `--crash-in-notify`. The receiver has accepted the email and the process dies
   before the kernel records it. `status` shows notify RUNNING with the effect
   CLAIMED and one line already in the receiver's file. Resume writes an
   `EFFECT_AMBIGUOUS` event, re-sends under the same key, and the receiver reports a
   duplicate. Still one line.
5. `history --kind DECISION` prints classify's label, confidence and the keyword rule
   that fired. `replay` prints CONSISTENT.
6. `cancel` on a workflow that was created with `--no-run`.
7. Operator mistakes (a duplicate id, an unknown workflow) are one-line errors with
   exit code 4.

## How it works

There are three parts. The store is the only module that touches SQLite. The engine decides
what runs next and holds nothing in memory between transitions. Tools do the work
and never see the database.

The store owns one SQLite file in WAL mode with `synchronous=FULL`, so every commit
is an fsync. Four tables: `workflows`, `steps` and `effects` are what the engine
reads; `events` is append-only (there is no UPDATE or DELETE statement for it
anywhere in `store.py`). Each state change is a single `BEGIN IMMEDIATE` transaction
that updates one row and inserts the events that explain it, which is what keeps the
tables and the log from drifting apart. A transition is checked against the state
machine before anything is written, and every write that belongs to a workflow first
checks, inside the same transaction, that the calling engine still owns it. The
schema version is stamped in `PRAGMA user_version` and a file written by a newer
kernel is refused before any DDL runs.

The engine is a function of the database and a registry of tools. `Engine.run`
reads the rows and continues from them, which is the whole resume story. It imports
no concrete tool; the CLI (and the tests) assemble the `{name: Tool}` registry.

A tool implements `run(ctx, inputs) -> dict`. `inputs` is the workflow input merged
with the persisted output of every earlier step, keyed by step name. `ctx` is a
`StepContext`: the tool's identity plus two services, `record_decision(key, data)`
and `run_effect(name, payload, fn)`. Errors are typed. `RetryableError` means try
again, `FatalError` means fail the step now, and anything else is treated as
retryable. I went back and forth on that last one; retrying a `KeyError` three times
is wasteful, but failing fast on an unexpected exception loses work when the cause
was transient, and the backoff is small.

A spec is a `WorkflowSpec(name, version, steps)` of `StepSpec(name, tool,
max_attempts, on_failure, backoff_base_s)`. `TRIAGE_V1` is the triage workflow:
ingest and extract get three attempts, classify two, notify five with
`on_failure="skip"`. A workflow records the spec version it started with and keeps
it.

## Execution model

```
workflow:  PENDING -> RUNNING -> SUCCEEDED | FAILED | CANCELLED
step:      PENDING -> RUNNING -> SUCCEEDED | FAILED | SKIPPED
                        '----> PENDING   (retry scheduled, or interrupted attempt found on resume)
```

Both machines are set literals in `store.py` (`WF_ALLOWED`, `STEP_ALLOWED`), used by
the store on every write and by the replay reducer on every read. SKIPPED is my one
addition to the four states in the brief; it is what `on_failure: skip` produces, and
I wanted it distinct from SUCCEEDED so the history does not lie about a step that
never ran to completion. Terminal states are absorbing.

`Engine.run(workflow_id)` goes like this.

First it claims the workflow: `UPDATE workflows SET owner=? WHERE id=? AND (owner IS
NULL OR owner=?)`. A rowcount of zero means another engine holds it and the CLI exits
3. From then on every write re-checks the owner inside its transaction, so an engine
that lost its claim cannot commit anything. The test for this stalls engine A inside
the receiver call, has an operator take over with engine B, and checks that when A
wakes up its DONE is refused and B's record stands. A dead process leaves its owner
behind; the operator clears it with `run --take-over`. I did not add a lease with a
timeout, because stealing a claim without a fencing token at commit time would let a
slow tool and a new owner both commit the same attempt. One live engine per database
file is the assumption. A finished workflow is never claimed: `run` just reports its
state.

Then it resumes. Any step found RUNNING was interrupted mid-attempt. It gets an
`ATTEMPT_INTERRUPTED` event and goes back to PENDING with its attempt count intact,
so the interrupted attempt counts against the budget. Steps already SUCCEEDED are
left alone; their output is on disk and that is what later steps read.

Then, for each step in spec order: check the cancel flag; if the attempts are used
up, apply the failure policy; otherwise wait until the persisted `next_run_at` (a
restart during a backoff waits the remainder, and the wait is clamped to the 2 s
backoff cap so a clock that stepped backwards cannot stall things for an hour), check
the cancel flag again if it waited, move the step to RUNNING, and call the tool with
no transaction open. The tool either returns a JSON object (step SUCCEEDED, output
persisted), raises `FatalError` (step FAILED with reason `fatal`), or raises
something else. With attempts left that schedules a retry at `now + min(base *
2^(attempt-1), 2 s)`; without, the step FAILED with reason `exhausted`. A return
value that is not a JSON object is a tool bug and is treated as fatal; retrying
would produce the same bad value. When a step would be FAILED and its `on_failure` is `skip`, it becomes
SKIPPED instead and the workflow continues. That is why a dead mail provider cannot
fail a triage that already classified the document.

Two paths only happen on resume. A step found FAILED at entry means the process died
between the step's FAILED commit and the workflow's own, so the engine just finishes
the workflow. A step interrupted on its last attempt comes back PENDING with no
budget left and goes straight to the failure policy, without an `ATTEMPT_FAILED`
event, since no attempt actually failed. A store error raised inside
`ctx.*` (a lost claim, for instance) is never mistaken for a tool failure; it
propagates and the engine stops.

Finally the workflow ends: FAILED as soon as a `fail_workflow` step fails (later
steps stay PENDING and `status` shows them that way), CANCELLED when the flag is
seen between attempts, SUCCEEDED when every step is SUCCEEDED or SKIPPED. Then the
claim is released. Cancellation is cooperative and never interrupts a running tool.

Every retry, skip, interruption, claim and effect decision is an event with a
payload (the delay, the reason, the attempt, the key), and tools write their own
`DECISION` events, so `history <id> --kind DECISION` is a plain query.

`replay <id>` is read-only. It folds the event log through `rebuild_state()`, which
uses the same ALLOWED sets as the store (an illegal transition in the log is itself a
finding), prints the narrative one line per event, and diffs the result against the
stored rows, exiting 1 on any difference. It never calls a tool. That is the only
definition of replay I could find that cannot violate "no duplicate side effects",
and it doubles as a tamper check; one of the tests edits a row by hand and expects
replay to fail.

## Idempotency

The hard part is the window between an external side effect returning and the
local commit that records it. A single process cannot close that window. What it
can do is make the ambiguity a recorded fact and decide per tool what to do about
it.

Only effectful tools use the ledger. Ingest, extract and classify are pure and are
simply re-run on retry. Notify does

```python
result = ctx.run_effect("email", payload, self.sink.send)   # sink.send(key, payload) -> dict
```

and the kernel computes `key = sha256(workflow_id:step:effect_name)`. There is no
attempt number in the key, so every attempt of the same logical effect, across
retries and restarts, presents the same key to the receiver. Then:

- If the ledger has the key as DONE, return the stored result and never call `fn`.
- If the ledger has the key as CLAIMED but not DONE, the effect may or may not have
  happened. Write an `EFFECT_AMBIGUOUS` event. If the tool declares
  `idempotent=True` (its receiver dedupes on the key, the way an API with an
  `Idempotency-Key` header such as Stripe's does), call `fn` again with the same key
  and let the receiver sort it out. If not, raise a fatal `ambiguous_effect`: the
  step fails with that reason and the row stays CLAIMED for an operator to look at.
  The kernel does not guess, and it does not start a fresh execution that would mint
  a new key.
- Otherwise commit CLAIMED, call `fn(key, payload)`, commit DONE with the result.

The demo receiver, `FileNotifier`, appends one line per key to `notifications.jsonl`
through its own file handle, outside the database, so the crash window is real.
`CrashingSink` dies after the receiver accepted and before DONE commits, which is the
worst case, and the tests check that exactly one line exists after resume, for both
an idempotent and a non-idempotent receiver.

So the guarantee depends on the receiver. With one that honours the key, an effect
is invoked at least once, its outcome is recorded exactly once, and the effect
happens exactly once. With one that cannot dedupe, invocation is at most once: when a
crash makes the outcome unknowable the kernel records `EFFECT_AMBIGUOUS`, fails the
step with that reason and stops.

The crash points for the notify step, and what resume does at each, are in the table
in `docs/DESIGN.md`. The tests cover the crash inside the receiver call
(`test_notify_effect_once_across_crash`, `test_ambiguous_non_idempotent_fails_safely`),
the crash after DONE but before the step's own commit
(`test_done_effect_is_not_resent_on_resume`), and the crash between steps
(`test_restart_resumes_without_rerunning`).

## Tests

31 tests, each on its own temp SQLite file, with a fake clock so backoff takes no
real time. Crashes are injected where they would happen in production: inside the
receiver call, right after a commit, during a sleep. The engine has no test hooks.

`test_retry.py` checks the backoff sequence `[0.05, 0.1]`, that `next_run_at` is
persisted and then cleared, and that an earlier step's attempt count is untouched by
a later step's retries. `test_failure_policy.py` checks that exhausting classify
fails the workflow without starting notify, and that notify on `skip` lets the
workflow succeed with the step SKIPPED after five attempts.

`test_idempotency.py` runs the crash after the receiver accepted, with an idempotent
receiver (one effect across two invocations) and a non-idempotent one (the step
fails with `ambiguous_effect`, the row stays CLAIMED, the receiver was called once).
It also checks that a DONE row is returned without calling the receiver and that the
demo receiver only dedupes when it says it does.

`test_restart.py` is the largest file: a crash between steps where the new process
re-runs nothing and the dead claim blocks a plain `run` until `--take-over`; a crash
during a backoff where the new process waits only the remaining 0.03 s of 0.05 s; a
clock stepped back an hour that costs only the 2 s cap; the stalled
engine that cannot commit after a take-over; a crash inside a step's last attempt
reaching the failure policy from PENDING; and a crash between a step's FAILED and the
workflow's FAILED.

`test_replay.py` checks that the log reproduces every row after retry plus crash
plus resume, that a hand-edited row makes `replay` exit 1, and that the reducer
rejects a log that skips a state. `test_transitions.py` covers illegal transitions
writing nothing, a stale owner unable to transition, touch the ledger or append an
event, cancel in its three timings (between attempts, before the first run, during a
backoff), `FatalError` failing without retry, a non-JSON output failing once, input
validation, `run` on a finished workflow not claiming it, and a cancel that races
`start`. `test_cli.py` checks that operator errors are messages with exit codes, that
a JSON spec round-trips through `start` and `run`, that a database from a newer
schema is refused before any DDL and left untouched, and that `history` of an
unknown id is an error.

## Design

### Scaling and system evolution

At 10k concurrent workflows the numbers are not frightening: a few hundred
transitions per second and around fifty events of history per workflow. What changes
is who owns what.

Each workflow would be owned by one shard, chosen by hashing the workflow id. The
shard's `events` log is the authority for its workflows and the `workflows`,
`steps` and `effects` tables stay the materialized projection, written in the same
transaction as now. Storage moves from one SQLite file to Postgres with `events`
partitioned by month, or to one SQLite file per shard behind a partition map if the
deployment is embedded. The schema does not change. Archival to object storage is a
retention decision.

The in-process loop becomes a pool of stateless workers pulling tasks ("run step X of
workflow W, attempt N") from a queue: SQS, Redis Streams, or a `SELECT ... FOR UPDATE
SKIP LOCKED` table all work. The claim column grows into a lease of `owner,
lease_until, lease_token`. Workers heartbeat to extend the lease and every commit
carries `WHERE lease_token = ?`, so a worker that lost its lease during a slow tool
cannot commit attempt N after another worker already did. That fencing token is the
thing this single-process version leaves out; here a claim has no expiry and an
operator is the recovery path. `next_run_at` becomes a delayed message or a
scheduler sweep. Per-workflow ordering holds because a workflow has at most one
runnable task at a time.

Tool execution moves behind RPC with the effect key sent as `Idempotency-Key`. The
orchestrator holds no tool code; the registry becomes a service catalogue keyed by
`tool@version`. A timeout maps to `RetryableError` and a validation 4xx to
`FatalError`.

Taking the failure boundaries one at a time: a worker dies, its lease expires, the task is
redelivered, and the next worker takes the `ATTEMPT_INTERRUPTED` path fenced by the
token. A tool service dies: typed error, retry policy, ledger untouched. The mail
receiver dies: notify claimed before it called, so the next attempt finds CLAIMED;
an idempotent receiver gets the same key again, a non-idempotent one is treated as
ambiguous, and because notify is on `skip` the workflow ends SUCCEEDED with notify
SKIPPED after two of its five attempts and no email. That is the safe direction;
letting a receiver report "definitely not sent" and releasing the claim on that
word is where I would expect a duplicate to come from. One shard's database goes
down: that shard pauses, the others carry on. The queue is lost: tasks are re-derived
from `steps` (PENDING, `next_run_at <= now`, no owner), because the queue is an
accelerator and never the source of truth. Stuck CLAIMED rows get a periodic
reconciler that lists them for operators. The `events` table is partitioned by
(shard, month) with an index on `(workflow_id, seq)`.

### Failure semantics: a crash during the notify side effect

Attempt 1 of notify: the kernel commits CLAIMED for key K, calls the email provider,
the provider accepts, and the process is killed before DONE commits. (Being killed
during the HTTP call looks identical on disk.)

In a naive design the step is RUNNING with no record that anything was sent, so a
resume re-runs the tool and a second email goes out.

Here, resume finds the step RUNNING, writes `ATTEMPT_INTERRUPTED` and moves it back
to PENDING with the attempt count kept. Attempt 2 calls `run_effect`, finds K as
CLAIMED, and writes `EFFECT_AMBIGUOUS` with the key, the attempt that claimed it,
whether the tool is idempotent, and the action it is about to take. `FileNotifier`
declares `idempotent=True`, so the kernel calls `send(K, ...)` again; the receiver
recognises K and drops it; DONE commits; the step succeeds. The history shows two
invocations, one effect, and one ambiguity event explaining why. With a receiver that
cannot dedupe (`idempotent=False`, say a bare SMTP relay) the step fails with reason
`ambiguous_effect`, which under notify's `skip` policy leaves the workflow SUCCEEDED
with notify SKIPPED, and a human checks the provider's log and resolves the ledger
row.

The cost is three commits per effectful step instead of one. The at-least-once
versus at-most-once choice is made per tool, and the ledger makes it visible
afterwards. Exactly-once effect is a property of kernel and receiver together, never
of the kernel alone. I considered a transactional outbox with a relay process; it
moves the same window into the relay without closing it, and it would let `status`
report notify SUCCEEDED while the email is still sitting in the outbox.

A second, smaller scenario: retry after partial success. Extract succeeds and
commits; classify fails twice with a transient error. Nothing about extract is
touched. Its output is read from the store on every classify attempt and on every
restart, and the retry budget belongs to classify alone. `test_retry_then_success`
checks that ingest's attempt count stays at 1 while extract retries.

### Abstractions and versioning

`workflows.spec_version` is set at `start` and never changes. The registry holds
every published version under `(name, version)`, so a running workflow always
resolves the spec it started with. When a tool's contract changes it gets a
versioned registry key (`notify@2`) and a spec references `tool@version`.

A published spec version is never edited in place. Adding, removing or reordering a
step, or changing `max_attempts` or `on_failure`, is a new version. Tool output
changes are additive, new fields only; a reshaped output is a new tool version with
a reader-side adapter, never a rewrite of stored `output_json` or events. Outputs are
plain JSON, so a v2 reader can upgrade a v1 output when it reads it.

Old versions stay registered until no PENDING or RUNNING workflow references them.
Migrating an in-flight workflow is an explicit, logged operation (a `MIGRATED{from,
to}` event), never something that happens because a deploy went out. Running
workflows do not break on deploy because the engine is a function of the database
and the registry: a registry that still contains the old versions changes nothing
for workflows in flight, only what `start` creates from now on.

### Multi-tenancy and compliance

For isolation I would put `tenant_id` on every table and make it the first component
of the effect key, `sha256(tenant:workflow:step:name)`, so two tenants cannot collide
on a key even with client-supplied workflow ids. A `Store(tenant)` wrapper adds the
tenant predicate to every query; per-tenant concurrency and rate limits live in the
scheduler; a regulated tenant can get its own shard. None of this is built. One
column and one predicate are cheap, but they would be unexercised here.

For audit, `events` is append-only (no UPDATE or DELETE path in `store.py`; in
Postgres a trigger would enforce it). Every row carries workflow, step, attempt,
type, payload and timestamp, so an auditor can reconstruct why a document got its
label (`DECISION`), when the notification was claimed and completed (`EFFECT_*`),
and every retry decision with its reason. `replay` is the tamper check: the log must
reproduce the tables.

For PII: a redaction hook before `append_event` for fields marked sensitive,
per-tenant retention that tombstones `payload_json` while keeping the event skeleton,
and `prev_hash` chaining if a regulator wants a tamper-evident log. All three are a
paragraph here and nothing in the code.

## What I would do next, in order

1. A lease with a fencing token (`owner, lease_until, lease_token`, every write's
   WHERE carrying the token). This removes the human from crash recovery, which is
   the only place this design needs one, and most of the scaling section follows
   from it.
2. A reconciler for stuck effects: a periodic query for CLAIMED rows older than N
   minutes and an `effects resolve <key> --done|--not-sent` verb. Today the kernel
   records the ambiguity correctly and then gives the operator nothing to close it
   with.
3. A payload hash in the ledger (there is a TODO at the spot in
   `Engine._run_effect`). A resend whose payload differs from the claimed one should
   be refused, so a receiver that dedupes by key can never silently keep a stale
   body. It works today only because the tools are pure functions of stored outputs.
4. Backoff on interruption, and jitter. Once resume is automatic an interrupted
   attempt should back off like a failed one, and a fleet needs jitter so retries do
   not line up.
5. Postgres behind the same `Store` interface, `events` partitioned by month,
   `tenant_id` everywhere.
6. Observability off `Store._append`: every state change already passes through
   that one function, so counters per event type, a span per attempt and a change
   feed all hang off the same hook.

## Time log

The brief suggested three to five hours. I spent about seven.

- Design notes (data model, state machines, the crash-point table, test plan),
  written before any code: about 1.5 h. The crash-point table ended up driving most
  of the tests.
- `store.py` and `engine.py`: about 1.5 h.
- Tools, CLI, demo script: about 45 min.
- The first 17 tests: about 45 min. Injecting the clock and sleep so backoff tests
  run in milliseconds was the main trick.
- README: about 30 min.
- Hardening and the last 14 tests: about 2 h. Racing two engines against each other
  showed the effects ledger was not owner-fenced, so one `_fence` now runs inside
  every workflow-scoped transaction and a race test pins it. Crash injection moved
  out of the engine into `faults.py`. Operator errors became exit codes. Then a pass
  over the paths a long-running operator would hit: a cancel during a backoff is
  re-checked after the sleep, an unserializable output is fatal instead of N
  retries, a backwards clock step is clamped to the backoff cap, the schema version
  is stamped in `user_version` and checked before DDL, a cancel racing `start` is
  reported as CANCELLED, and the two resume-only paths (interrupted last
  attempt, failed at entry) got their tests.

What I left out, each on purpose: a transactional outbox and dispatcher; lease
expiry and stealing (needs the fencing token above); DAG or branching specs; YAML;
hash-chained events; encryption at rest; `run --follow`; a `tenant_id` column; a
`schema_version` field inside step outputs; preemptive cancellation (a cancel during
a long tool waits for it); metrics; an operator verb to resolve ambiguous effect
rows; and `start --derived-from <id>` to re-execute a failed workflow as a new
execution with its own effect keys. I would call that last one something other than
`replay`, which here means read-only.
