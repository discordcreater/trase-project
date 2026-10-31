# wfk: a small durable workflow kernel

A minimal, production-minded engine that runs a four-step document-triage workflow
(**Ingest → Extract → Classify → Notify**) with durable state, retries, step-level failure
policy, safe resume after a crash, an effects ledger that keeps *notify* from firing twice,
and an append-only event log that can answer "what happened" and "what was decided, and why".

Standard library only (Python 3.9+, `sqlite3`). `pytest` for the tests.

```
wfk/
  spec.py     declarative workflow spec (dataclasses; optional JSON loader)
  store.py    SQLite persistence: tables, the two state machines, transition(), effects ledger, events
  engine.py   orchestration: Engine.start/run/cancel, StepContext, run_effect, rebuild_state (replay)
  tools.py    Tool interface, the four triage tools, FileNotifier receiver
  faults.py   fault injection for tests/demo (Flaky, CrashingSink, CrashAfterCommit); the kernel never imports it
  cli.py      python -m wfk {start,run,status,cancel,history,replay}
tests/        retry, failure policy, idempotency across a crash, restart safety, owner fencing, replay, transitions, CLI
scripts/demo.sh   a run, a retry, two crash/resume scenarios, replay, cancel
docs/DESIGN.md    the full design record (decisions, crash-point table, test plan)
```

## How to run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # pytest only
.venv/bin/python -m pytest                                             # 27 tests, < 1 s
scripts/demo.sh                                                        # end-to-end demo in a temp dir

python -m wfk start examples/doc.json --id w1          # create and run (db: ./wfk.db)
python -m wfk status w1                                # one SELECT per table, no replay needed
python -m wfk history w1 --kind DECISION               # what classify decided, and why
python -m wfk replay w1                                # rebuild state from the log, diff vs tables
python -m wfk cancel w1                                # cooperative; honoured between attempts
python -m wfk run w1 [--take-over]                     # resume after a crash
python -m wfk start doc.json --spec my_spec.json        # a JSON spec; pass the same --spec to `run` later

# fault injection (start / run):
#   --fail extract:2        extract raises RetryableError on its first 2 calls
#   --crash-after classify  die (os._exit) right after classify's success is committed
#   --crash-in-notify       die after the receiver accepted the email, before the kernel recorded it
# exit codes: 0 ok, 1 replay inconsistent, 2 workflow FAILED/CANCELLED, 3 owned by another engine,
#             4 store refused (not found / exists / illegal), 5 bad input or spec, 6 database error
```

Runs on the system Python 3.9 and on 3.14; the venv is only for `pytest`. `python -m wfk` needs no
install: run it from the repository root.

### Demo (`scripts/demo.sh`, ~2 s, everything in a temp dir)

1. a clean run: four steps, one notification, `status` shows the effect row `DONE`;
2. `--fail extract:2`: two `RETRY_SCHEDULED` events with the backoff delays, success on attempt 3,
   ingest's attempt count untouched;
3. `--crash-after classify`: the process dies (`os._exit`) after classify's success is on disk. A
   plain `run` is refused with exit 3 (the dead process still holds the claim); `run --take-over`
   finishes it. Ingest/extract/classify are not re-run, there is exactly one notification;
4. `--crash-in-notify`: the receiver has accepted the email and the process dies before the kernel
   recorded it. `status` shows notify `RUNNING`, effect `CLAIMED`, one line already in the receiver's
   file. Resume records `EFFECT_AMBIGUOUS`, re-sends under the same key, the receiver dedupes: still
   one line;
5. `history --kind DECISION` and `replay` (CONSISTENT: the log reproduces the tables);
6. `cancel` of a never-started workflow;
7. operator errors are one-line messages with exit codes, never tracebacks.

## Architecture overview

One mental model: **Engine decides, Store remembers, Tool does.** Nothing else holds state.

- **Store** (`store.py`) owns a single SQLite file (WAL, `synchronous=FULL`, schema version stamped in
  `PRAGMA user_version`; a file written by a newer kernel is refused) with four tables:
  `workflows`, `steps`, `effects` (the state the engine reads) and `events` (append-only; no
  UPDATE or DELETE statement for it exists in the module). Every state change is one
  `BEGIN IMMEDIATE` transaction that updates a row **and** appends the events that explain it, so
  tables and log cannot disagree. `transition_*` checks the move against the state machine first
  (`IllegalTransition`, nothing written). **Every** write that belongs to a workflow (transitions,
  effect claim/complete, loose events) runs behind one owner fence inside its transaction
  (`LostClaim`, rolled back): a process that lost its claim cannot write anything.
- **Engine** (`engine.py`) is a function of (database, registry). It holds nothing between
  transitions, which is what makes resume trivial: a new process reads the rows and continues.
  It imports no concrete tool; the registry `{tool_name: Tool}` is assembled by the CLI (and tests).
- **Tools** (`tools.py`) implement `run(ctx, inputs) -> dict`. `inputs` is the workflow input plus
  the persisted output of every earlier step. A tool sees only a `StepContext`: its identity and two
  kernel services, `record_decision(key, data)` and `run_effect(name, payload, fn)`. It never sees
  the store. Errors are typed: `RetryableError` (retry), `FatalError` (fail now); anything else is
  treated as retryable because that is the default that loses less work.
- **Spec** (`spec.py`): `WorkflowSpec(name, version, steps)`, `StepSpec(name, tool, max_attempts,
  on_failure, backoff_base_s)`. `TRIAGE_V1` is the triage workflow. A workflow pins its
  `spec_version` at start.

## Execution model

```
workflow:  PENDING → RUNNING → SUCCEEDED | FAILED | CANCELLED
step:      PENDING → RUNNING → SUCCEEDED | FAILED | SKIPPED
                        └──→ PENDING   (retry scheduled, or interrupted attempt found on resume)
```

The whole machine is two set literals in `store.py` (`WF_ALLOWED`, `STEP_ALLOWED`), used by the
store on every write and by the replay reducer on every read. `SKIPPED` is the one addition to the
brief's four states: it is what `on_failure: skip` produces, and keeping it distinct from `SUCCEEDED`
keeps the audit trail honest. Terminal states are absorbing.

`Engine.run(workflow_id)`:

1. **Claim** the workflow (`UPDATE workflows SET owner=? WHERE id=? AND (owner IS NULL OR owner=?)`).
   Rowcount 0 means another engine holds it (`AlreadyOwned`). Every later write, state
   transitions, the effects ledger and loose events alike, re-checks `owner` inside its
   `BEGIN IMMEDIATE` transaction, so an engine that lost its claim cannot commit anything
   (`test_fenced_out_engine_cannot_finish_the_effect`: engine A stalls inside the receiver call,
   an operator takes over with B, B finishes; when A wakes up its `DONE` is refused). A dead
   process leaves a stale owner; the operator clears it with `run --take-over` (the demo shows
   this). There is no TTL stealing on purpose: stealing without a fencing token at commit time
   would let a slow tool and a new owner both commit the same attempt. Assumption: one live engine
   per database file. A terminal workflow is never claimed; `run` just reports its state.
2. **Resume**: any step found `RUNNING` was interrupted mid-attempt. It gets an `ATTEMPT_INTERRUPTED`
   event and goes back to `PENDING` with its attempt count intact (the interrupted attempt is
   charged against the budget). Steps already `SUCCEEDED` are never re-run: their `output_json` is
   on disk and is what later steps read.
3. **Per step**, in spec order: check the cancel flag; if attempts are exhausted apply the failure
   policy; wait until the persisted `next_run_at` (a restart during a backoff waits the remainder,
   not zero and not the full delay; the wait is clamped to the backoff cap so a clock that stepped backwards
   cannot stall the engine for hours), re-check the cancel flag if it waited; `PENDING → RUNNING` (`ATTEMPT_STARTED`, attempt += 1); call the
   tool outside any transaction; then one of:
   - returned a JSON object → `RUNNING → SUCCEEDED`, output persisted (a non-JSON output is a tool bug and
     is treated as `FatalError`, not retried);
   - `FatalError` → `RUNNING → FAILED` (reason `fatal`), no more attempts;
   - any other exception with attempts left → `RUNNING → PENDING` with `RETRY_SCHEDULED`,
     `next_run_at = now + min(base · 2^(attempt−1), 2 s)`; otherwise `FAILED` (reason `exhausted`).
   A `FAILED` outcome becomes `SKIPPED` when the step's `on_failure` is `skip` (step-level failure
   handling: notify is `skip`, so a dead mail provider cannot fail a triage that already classified).
   Two resume-only paths: a step found `FAILED` at entry means the process died between
   `STEP_FAILED` and the workflow's own `FAILED` commit, so the engine just finishes that; a step
   interrupted on its *last* attempt comes back `PENDING` with no budget and goes straight to the
   failure policy (`PENDING → FAILED | SKIPPED`), with no `ATTEMPT_FAILED` event because the
   attempt was interrupted, not failed. A kernel error raised inside `ctx.*` (a lost claim) is
   never mistaken for a tool failure: it propagates, the engine stops.
4. **Finish**: `FAILED` as soon as a `fail_workflow` step fails (later steps stay `PENDING`, visible in
   `status`); `CANCELLED` when the flag is seen between attempts (never mid-tool); `SUCCEEDED` when
   every step is `SUCCEEDED` or `SKIPPED`. Then release the claim.

Every retry, skip, interruption, claim and effect decision is an event with a payload (delay,
reason, attempt, key), and tools write their own `DECISION` events, so
`history <id> --kind DECISION` is a query, not a log grep.

`replay <id>` is **read-only**: it folds the event log through `rebuild_state()` (the same
`ALLOWED` sets; an illegal transition in the log is itself a finding), prints the narrative, and
diffs the projection against the stored rows, exiting 1 on any difference. It never calls a tool,
which is the only definition of replay that cannot violate "no duplicate side effects". It doubles
as the consistency/tamper check (tested: editing a row by hand makes replay fail).

## Idempotency approach

The hard part is the window between an external side effect returning and the local commit that
records it. No single-process design can close it, so the kernel makes the ambiguity a recorded,
queryable fact instead of hiding it.

Only effectful tools use the ledger; `ingest`, `extract`, `classify` are `side_effect_free` and are
simply re-run on retry. `notify` does:

```python
result = ctx.run_effect("email", payload, self.sink.send)     # sink.send(key, payload) -> dict
```

and the kernel does, for `key = sha256(workflow_id:step:effect_name)` (**no attempt number**, so every
attempt of the same logical effect, across retries and restarts, presents the same key):

1. ledger row `DONE` → return the stored result, never call `fn` again;
2. ledger row `CLAIMED` but not `DONE` → the effect may or may not have happened. Record
   `EFFECT_AMBIGUOUS`. If the tool declares `idempotent=True` (the receiver honours the key, as
   SES/SendGrid/Stripe do), re-call with the **same key** and let the receiver dedupe. If not, raise
   a fatal `ambiguous_effect`: the step fails with that reason and the ledger row stays `CLAIMED`
   for an operator. The kernel never guesses and never forks a new execution that would mint a new
   key;
3. no row → commit `CLAIMED`, call `fn(key, payload)`, commit `DONE` with the result.

The demo receiver (`FileNotifier`) appends one line per key to `notifications.jsonl` through its
own file handle, outside the database, so the crash window is real; `CrashingSink` dies after the
receiver accepted and before `DONE` commits (the worst case), and the tests check that exactly one
line exists after resume, in both the idempotent and the non-idempotent variants.

> The kernel guarantees **at-least-once invocation** of an effect, **exactly-once recording** of its
> outcome, and **exactly-once effect iff the receiver honours the idempotency key** the kernel passes
> it. When a crash makes the outcome of a non-idempotent effect unknowable, the kernel records
> `EFFECT_AMBIGUOUS`, fails the step with that reason, and stops: it never guesses and never forks a
> new execution around it.

Crash points for the notify step and what resume does at each are tabulated in
`docs/DESIGN.md` §5.2 (C0–C7); the tests cover C2/C3–C4 (`test_notify_effect_once_across_crash`,
`test_ambiguous_non_idempotent_fails_safely`), C5 (`test_done_effect_is_not_resent_on_resume`) and
the crash between steps (`test_restart_resumes_without_rerunning`).

## What the tests prove

27 tests, one temp SQLite file each, a fake clock so backoff takes no real time. Every crash is
injected at a boundary where it happens in production (inside the receiver call, right after a
commit, during a sleep), never by a hook inside the kernel.

| File | Proves |
|---|---|
| `test_retry.py` | exponential backoff `[0.05, 0.1]`, `next_run_at` persisted then cleared, earlier steps untouched by a later step's retries |
| `test_failure_policy.py` | exhaustion fails the workflow and never starts later steps; `on_failure: skip` lets the workflow succeed with the step `SKIPPED` after 5 attempts |
| `test_idempotency.py` | crash after the receiver accepted: one effect across two invocations (idempotent receiver); the same crash with a non-idempotent receiver fails the step with `ambiguous_effect` and leaves the row `CLAIMED`; a `DONE` row is returned without calling the receiver |
| `test_restart.py` | crash between steps: new process re-runs nothing; the dead claim blocks a plain `run` and `--take-over` clears it; a crash during backoff waits only the remainder; a clock that stepped back an hour costs at most one capped backoff; a stalled engine that lost its claim cannot commit anything after a take-over |
| `test_replay.py` | the log reproduces every row after retry + crash + resume; a hand-edited row makes `replay` exit 1; a log that skips a state is rejected by the reducer |
| `test_transitions.py` | illegal transitions write nothing; a stale owner cannot transition, claim/complete an effect, or append an event; cancel between attempts, before first run, and during a backoff wait; `FatalError` fails without retry; a non-JSON tool output fails once, not N times; input validation; `run` on a terminal workflow does not claim |
| `test_cli.py` | every operator error is a message with an exit code; a JSON spec round-trips through `start` and `run`; a database file from a newer schema version is refused |

## Design

### A. Scaling and system evolution

**State ownership.** A workflow is owned by exactly one shard, chosen by `hash(workflow_id)`. The
shard's `events` log is the authority for that workflow; `workflows`/`steps`/`effects` stay the
materialized projection written in the same transaction. Storage moves from one SQLite file to
Postgres (or one SQLite per shard behind a partition map for an embedded deployment); the schema
does not change. 10k concurrent workflows is a few hundred transitions per second and ~50 events of
history each: a single Postgres with `events` partitioned by month is comfortable; archival to object
storage is a retention decision, not a capacity one.

**Execution model.** The in-process loop becomes a pool of stateless workers pulling tasks
("run step X of workflow W, attempt N") from a queue (SQS, Redis Streams, or a
`SELECT … FOR UPDATE SKIP LOCKED` table). The claim column generalizes to a lease
(`owner, lease_until, lease_token`): workers heartbeat to extend it and **every commit carries
`WHERE lease_token = ?`**, so a worker that lost its lease during a slow tool cannot commit attempt N
after another worker already did. That fencing token is exactly what this single-process design
leaves out (a claim without expiry). `next_run_at` becomes a delayed message or a scheduler sweep.
Per-workflow ordering holds because a workflow has at most one runnable task at a time.

**Tools as services.** Tool execution moves behind RPC with the effect key sent as
`Idempotency-Key`; the orchestrator holds no tool code and the registry becomes a service catalogue
keyed by `tool@version`. Timeouts map to `RetryableError`, validation 4xx to `FatalError`.

**Failure boundaries.** Worker dies → lease expires → task redelivered → the `ATTEMPT_INTERRUPTED`
path, fenced by the token. Tool service dies → typed error → retry policy, ledger untouched.
Receiver dies → `RetryableError` before the claim, or ambiguity after it; the ledger makes the
difference visible. One shard's database down → that shard pauses, others unaffected. Queue loss →
tasks are re-derived from `steps` (`PENDING`, `next_run_at <= now`, no owner): the queue is an
accelerator, never the source of truth. Stuck `CLAIMED` rows are listed by a periodic reconciler for
operators; the `events` append is partitioned by (shard, month) with an index on `(workflow_id, seq)`.

### B. Failure semantics: crash during the notify side effect

**Scenario.** Attempt 1 of `notify`: the kernel commits `CLAIMED` for key K, calls the email
provider, the provider accepts, and the process is killed before `DONE` commits (C4; C3, killed
during the HTTP call, is indistinguishable on disk).

**What goes wrong naively.** The step is `RUNNING` with no record that anything was sent. A resume
re-runs the tool and sends a second email: the duplicate the brief names.

**How this system handles it.** (1) On resume the step is `RUNNING` → `ATTEMPT_INTERRUPTED` → back to
`PENDING`, attempt count kept. (2) Attempt 2 calls `run_effect`; the ledger holds K as `CLAIMED`; the
kernel records `EFFECT_AMBIGUOUS{key, claimed_by_attempt, tool_idempotent, action}`: it knows that it
does not know. (3) `FileNotifier` declares `idempotent=True`, so the kernel re-calls `send(K, …)`;
the receiver recognises K and drops the duplicate; `DONE` commits; the step succeeds. The history
shows two invocations, one effect, and one ambiguity event explaining why. (4) With a receiver that
cannot dedupe (`idempotent=False`, a legacy SMTP relay), the step fails with reason
`ambiguous_effect` (with `on_failure=skip` the workflow still completes, notify `SKIPPED`); a human
checks the provider log and resolves the ledger row. The kernel refuses to guess.

**Tradeoffs.** Three commits per effectful step instead of one. The at-least-once vs at-most-once
choice is made per tool, explicitly and auditably. Exactly-once *effect* is a property of kernel +
receiver together, never of the kernel alone, and the README says so instead of claiming
exactly-once. A transactional outbox with a relay would move the same window into the relay without
closing it, and would let `status` say "notify SUCCEEDED" while nothing has left the building.

**Retry after partial success.** Extract succeeds and commits; classify fails twice with a transient
error. Nothing about extract is touched: its `output_json` is read from the store on every classify
attempt and on every restart, and the retry budget belongs to classify alone. Partial progress is
never lost and never redone (`test_retry_then_success` checks ingest's attempt count stays 1).

### C. Abstractions and versioning

- **Pinning.** `workflows.spec_version` is set at `start` and never changes. The registry holds every
  published version (`SPECS[(name, version)]`), so a running workflow always resolves the spec it
  started with. When a tool's contract changes it gets a versioned registry key (`notify@2`) and a
  spec references `tool@version`.
- **Rules.** Never edit a published spec version in place: adding, removing or reordering a step, or
  changing `max_attempts`/`on_failure`, is a new spec version. Tool output changes are additive (new
  fields only); a reshaped output is a new tool version with a reader-side adapter, never a rewrite
  of stored `output_json` or events. Outputs are plain JSON, so a v2 reader can upgrade a v1 output on
  read.
- **Draining.** Old versions stay registered until no `PENDING`/`RUNNING` workflow references them.
  Migrating an in-flight workflow is an explicit, logged operation (`MIGRATED{from, to}` event),
  never implicit.
- **Why running workflows do not break.** The engine is a function of (database, registry).
  Deploying a registry that still contains the old versions changes nothing for workflows in
  flight; it only changes what `start` creates.

### D. Multi-tenancy and compliance

- **Isolation.** A `tenant_id` on every table and as the first component of the effect key
  (`sha256(tenant:workflow:step:name)`), so two tenants can never collide on a key even with
  client-supplied workflow ids; a `Store(tenant)` wrapper adds the tenant predicate to every query;
  per-tenant concurrency and rate limits in the scheduler; a separate shard for regulated tenants.
  Not built: one column and one predicate are cheap but would be unexercised here.
- **Audit.** `events` is append-only by construction (no UPDATE/DELETE path in `store.py`; a trigger
  enforces it in Postgres). Every row carries workflow, step, attempt, type, payload and timestamp, so
  an auditor can reconstruct why a document got its label (`DECISION`), when the notification was
  claimed and completed (`EFFECT_*`), and every retry decision with its reason. `replay` is the tamper
  check: the log must reproduce the tables.
- **PII.** A redaction hook before `append_event` for fields marked sensitive; per-tenant retention
  that tombstones `payload_json` while keeping the event skeleton; `prev_hash` chaining for a
  tamper-evident log if a regulator requires it. Described, not built.

## Production roadmap

What I would build next, in order, and why that order:

1. **Lease with a fencing token** (`owner, lease_until, lease_token`; every write's WHERE carries the
   token). Removes the human from crash recovery, which is the only step in this design that needs one.
   Everything else in Design A is a consequence of this.
2. **Reconciler for stuck effects**: a periodic query for `CLAIMED` rows older than N minutes, surfaced to
   operators with an `effects resolve <key> --done|--not-sent` verb. Today the kernel records the
   ambiguity correctly but gives the operator no verb to close it.
3. **Payload hash in the ledger** (see the comment in `Engine._run_effect`): a resend whose payload differs
   from the claimed one is refused, so a receiver that dedupes by key can never silently keep a stale body.
4. **Backoff on interruption and jitter**: once resume is automatic, an interrupted attempt must back off
   like a failed one, and a fleet needs jitter so retries do not synchronise.
5. **Postgres behind the same `Store` interface**, `events` partitioned by month, `tenant_id` on every table
   and in the effect key. The schema does not change; the fence becomes `WHERE lease_token = ?`.
6. **Observability off the event funnel** (`Store._append`): counters per event type, a span per attempt,
   and a change feed; one instrumentation point because every state change already passes through it.

## Time log

| Where | Time | Notes |
|---|---|---|
| Design record (`docs/DESIGN.md`): data model, state machines, crash-point table, test plan | ~1.5 h | Decided before writing code; the table of crash points drove the tests |
| `store.py` + `engine.py` | ~1.5 h | Transition fencing on state + owner; effects ledger; reducer |
| Tools, CLI, demo | ~0.75 h | |
| Tests (17) | ~0.75 h | Injected clock/sleep so backoff tests run in milliseconds |
| README | ~0.5 h | |
| Self-review pass (+6 tests) | ~1 h | Reproduced every README claim and raced two engines. Found the effects ledger was not owner-fenced (a stalled engine could overwrite the record of the one that took over; fixed with one `_fence` inside every workflow-scoped transaction and a race test); moved crash injection out of the engine into `faults.py`; operator errors became exit codes |
| Final documentation pass | ~0.5 h | This README, demo narration, test map |
| Review pass (+4 tests) | ~0.75 h | Read the kernel as an operator would run it for a year: a cancel during a backoff wait was not seen until after the next attempt (re-checked after the wait); a tool output that cannot be serialized was retried N times as if transient (now `FatalError`); a clock stepping backwards could stall a resume for hours (wait clamped to the backoff cap); schema version stamped in `user_version`. Comments at the non-obvious decisions (fence vs lease, effect-key stability, payload hash, poison-pill bound, jitter) |

**Intentionally simplified / cut** (each would be a real feature, none earns its lines here):
transactional outbox + dispatcher; lease TTL and stealing (needs the fencing token described in A);
DAG/branching specs; YAML; hash-chained events; encryption at rest; `run --follow`; `tenant_id`;
`schema_version` in outputs; preemptive cancellation (a cancel during a long tool waits for it);
metrics; an operator `effects resolve` verb for ambiguous rows; `start --derived-from <id>` to
re-execute a failed workflow as a *new* execution with its own effect keys (deliberately not called
`replay`). Unexpected exceptions are retried up to `max_attempts` rather than failed fast: a genuine
bug costs N quick attempts, a transient one loses no work.
