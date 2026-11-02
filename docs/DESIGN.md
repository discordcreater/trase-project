# wfk design notes

These notes cover what was built: the data model, the two state machines, how a run
proceeds, how side effects are kept from happening twice, and what I decided not to
build. The scaling, versioning and multi-tenancy discussion is in the README under
"Design"; I have not repeated it here.

## The problem

Run a four-step document triage (ingest, extract, classify, notify) so that a crash
at any instruction leaves the database in a state the next process can continue
from, retries and step-level failure policy work as specified, the notify effect is
never performed twice when the kernel can prove it and the kernel says so when it
cannot, and the history can answer both "what happened" and "what was decided, and
why".

The part that needs real thought is the window between an external side effect
returning and the local commit that records it. No single-process design closes
that window. I sketched three shapes before writing code: a transactional outbox
with a dispatcher that owns every send; a dedupe-only sink with no kernel
bookkeeping; and an effects ledger in the kernel with the receiver deduping on a key
the kernel mints. All three can offer the same guarantee (with a receiver
that honours the key, at-least-once invocation and exactly-once effect; without one,
at-most-once invocation; and when the outcome is unknowable, say so). I chose the
ledger because it makes the ambiguity a row and an event that an operator can query,
instead of something buried inside a dispatcher or a sink.

## Shape of the solution

One package, `wfk/`, with six modules: `spec.py`, `store.py`, `engine.py`,
`tools.py`, `faults.py` and `cli.py`. Standard library plus `sqlite3`; `pytest` for
the tests. I considered a `tools/` sub-package with a dispatcher and an `engine/`
package split by concern, and rejected both as more layout than the problem needs.
The one structural rule that matters is that `engine.py` imports no concrete tool
and nothing from `faults.py`; the registry `{name: Tool}` is assembled in `cli.py`
and in the tests. The price is that there is no plugin discovery; tools are
registered in one dict.

The code is written for Python 3.9 (`from __future__ import annotations`, no
`match`, no `slots=True`, no `X | Y` at runtime) so it runs on a stock macOS
interpreter as well as on 3.14. The idioms are slightly older as a result.

## Data model

One SQLite file, WAL, `synchronous=FULL`, `foreign_keys=ON`.

```sql
CREATE TABLE workflows (
  id            TEXT PRIMARY KEY,
  spec_name     TEXT NOT NULL,
  spec_version  INTEGER NOT NULL,
  input_json    TEXT NOT NULL,
  state         TEXT NOT NULL,              -- PENDING|RUNNING|SUCCEEDED|FAILED|CANCELLED
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  owner         TEXT,                       -- claim token of the live engine, NULL when free
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE steps (
  workflow_id   TEXT NOT NULL,
  name          TEXT NOT NULL,
  idx           INTEGER NOT NULL,           -- position in the spec
  state         TEXT NOT NULL,              -- PENDING|RUNNING|SUCCEEDED|FAILED|SKIPPED
  attempt       INTEGER NOT NULL DEFAULT 0, -- attempts started so far
  max_attempts  INTEGER NOT NULL,
  output_json   TEXT,                       -- set on SUCCEEDED; input of later steps
  error         TEXT,                       -- last error message
  next_run_at   TEXT,                       -- backoff deadline; NULL = runnable now
  updated_at    TEXT NOT NULL,
  PRIMARY KEY (workflow_id, name)
);

CREATE TABLE events (                       -- append-only; no UPDATE/DELETE path in store.py
  seq           INTEGER PRIMARY KEY AUTOINCREMENT,
  workflow_id   TEXT NOT NULL,
  step_name     TEXT,                       -- NULL for workflow-level events
  type          TEXT NOT NULL,
  attempt       INTEGER,
  payload_json  TEXT NOT NULL DEFAULT '{}',
  created_at    TEXT NOT NULL
);

CREATE TABLE effects (                      -- side-effect ledger
  key           TEXT PRIMARY KEY,           -- sha256(workflow_id:step:effect_name)
  workflow_id   TEXT NOT NULL,
  step_name     TEXT NOT NULL,
  effect_name   TEXT NOT NULL,
  attempt       INTEGER NOT NULL,           -- attempt that claimed it (informational)
  status        TEXT NOT NULL,              -- CLAIMED|DONE
  result_json   TEXT,                       -- set on DONE; returned on re-entry without calling fn
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
```

`workflows`, `steps` and `effects` are what the engine reads. `events` is written
in the same transaction as every change to them, and a reducer (`rebuild_state` in
`engine.py`) can rebuild all three from the log. I went this way rather than pure
event sourcing, where every read folds the log, because `status` should be one
SELECT per table; and rather than tables alone, because then there is no audit
trail. The cost is one extra INSERT per transition. The claim that the log is the
truth and the tables are a cache is tested, including for `effects`, which the
reducer rebuilds from `EFFECT_CLAIMED` and `EFFECT_DONE`.

Timestamps are ISO-8601 UTC strings from a single `store.now()` so tests can freeze
time. `steps.attempt` counts attempts started; moving a step from RUNNING back to
PENDING does not decrement it, which is how an interrupted attempt is charged
against the budget. The demo notifier's own record (`notifications.jsonl`, keyed by
effect key) lives outside the database and is written through its own file handle,
so the crash window between "receiver accepted" and "DONE committed" is real and the
tests exercise it.

Event types are a closed set of sixteen:

| Type | Level | Payload |
|---|---|---|
| `WORKFLOW_CREATED` | workflow | spec_name, spec_version, input, steps (name and max_attempts each) |
| `WORKFLOW_STATE` | workflow | from, to, reason |
| `CANCEL_REQUESTED` | workflow | by |
| `OWNER_CLAIMED` | workflow | owner, take_over |
| `OWNER_RELEASED` | workflow | owner |
| `ATTEMPT_STARTED` | step | attempt |
| `ATTEMPT_FAILED` | step | attempt, error, retryable |
| `RETRY_SCHEDULED` | step | attempt, next_run_at, delay_s, error, reason |
| `ATTEMPT_INTERRUPTED` | step | attempt (the step was found RUNNING on resume) |
| `STEP_SUCCEEDED` | step | attempt, output |
| `STEP_FAILED` | step | attempt, error, reason (`exhausted`, `fatal` or `ambiguous_effect`) |
| `STEP_SKIPPED` | step | attempt, error, reason, policy (`skip`) |
| `EFFECT_CLAIMED` | step | key, effect_name, attempt |
| `EFFECT_DONE` | step | key, result |
| `EFFECT_AMBIGUOUS` | step | key, claimed_by_attempt, tool_idempotent, action (`resend` or `fail`) |
| `DECISION` | step | key, data (written by the tool; classify records label, confidence and rule) |

## State machines

```
workflow:  PENDING -> RUNNING -> SUCCEEDED | FAILED | CANCELLED
           PENDING -> CANCELLED                      (cancel before the first run)

step:      PENDING -> RUNNING -> SUCCEEDED | FAILED | SKIPPED
           RUNNING -> PENDING                        (retry scheduled, or interrupted attempt found on resume)
           PENDING -> FAILED | SKIPPED               (interrupted on the last attempt; see below)
```

The whole thing is two set literals in `store.py`:

```python
WF_ALLOWED = {
    ("PENDING", "RUNNING"), ("PENDING", "CANCELLED"),
    ("RUNNING", "SUCCEEDED"), ("RUNNING", "FAILED"), ("RUNNING", "CANCELLED"),
}
STEP_ALLOWED = {
    ("PENDING", "RUNNING"),
    ("RUNNING", "SUCCEEDED"), ("RUNNING", "PENDING"),
    ("RUNNING", "FAILED"), ("RUNNING", "SKIPPED"),
    ("PENDING", "FAILED"), ("PENDING", "SKIPPED"),
}
```

`transition_workflow` and `transition_step` refuse any pair not listed, before
touching the database (`IllegalTransition`, nothing written), and the replay reducer
uses the same sets, so a log that skips a state is rejected on read. SKIPPED is the
one state the brief does not name. It is what `on_failure: skip` produces, and I
kept it distinct from SUCCEEDED so a skipped notify cannot be mistaken for a sent
one. Terminal states are absorbing.

The two `PENDING -> FAILED | SKIPPED` edges were not in my first draft. They cover
a case I missed on paper: a step interrupted on its last attempt. Resume moves it
RUNNING to PENDING with the attempt already counted, and the loop then finds
`attempt >= max_attempts` while the row is PENDING. The failure policy has to apply
from there, without starting an attempt there is no budget for.
`test_interrupted_last_attempt_goes_to_the_failure_policy` pins it: a crash inside
the last attempt, resume, FAILED with reason `exhausted` and no `ATTEMPT_FAILED`.

Workflow state is derived from step states by the engine and written through the
same transition path.

## One run, start to finish

```
Engine.run(workflow_id, take_over=False):
  wf = store.load_workflow(wf_id)
  if wf.state is terminal: return wf.state      # never claimed; --take-over only clears a stale owner
  spec = resolve(wf.spec_name, wf.spec_version) # operator errors come before the claim, not after
  owner = uuid4()[:8]
  store.claim(wf_id, owner, take_over)          # UPDATE ... WHERE owner IS NULL OR owner = ?; rowcount 0 -> AlreadyOwned
  try:
    wf = store.load_workflow(wf_id)             # re-read under the claim
    if wf.state is terminal: return wf.state    # a cancel landed between the first read and the claim
    if wf.state == PENDING: transition(wf, RUNNING)

    for s in steps where state == RUNNING:      # at most one after a crash
        event ATTEMPT_INTERRUPTED(attempt); transition(s, PENDING)   # attempt already counted

    for step in spec.steps (idx order):
        row = store.load_step(wf_id, step.name)
        if row.state in (SUCCEEDED, SKIPPED): continue
        if row.state == FAILED: return finish(FAILED)   # died between STEP_FAILED and the workflow's FAILED
        loop:
            if cancel_requested: return finish(CANCELLED)
            if row.attempt >= max_attempts: give_up(exhausted); break
            waited = wait_until(row.next_run_at)        # clamped to the backoff cap
            if waited and cancel_requested: return finish(CANCELLED)
            transition(row, RUNNING, ATTEMPT_STARTED, attempt+1, next_run_at=NULL)
            try:
                out = tool.run(ctx, inputs)             # no transaction open
                encode(out)                             # not a JSON object -> FatalError
                transition(row, SUCCEEDED, STEP_SUCCEEDED, output=out); break
            except FatalError:  give_up(fatal, or ambiguous_effect); break
            except StoreError:  raise                   # the kernel's own failure inside ctx.*, not the tool's
            except Exception:                           # RetryableError and anything unexpected
                if attempts left:
                    delay = min(base * 2 ** (attempt-1), cap)
                    transition(row, PENDING, [ATTEMPT_FAILED, RETRY_SCHEDULED], next_run_at=now+delay)
                    continue
                give_up(exhausted); break
        if the step ended FAILED: return finish(FAILED)
    return finish(SUCCEEDED)
  except Exception:
    store.release(wf_id, owner); raise              # an error is not a crash: free the claim
  store.release(wf_id, owner)
  # A BaseException (SimulatedCrash, KeyboardInterrupt) is the process dying. It propagates
  # without a release and leaves the stale owner a real crash leaves. There is no crash hook.
```

`inputs` is the workflow input merged with `{step_name: output_json}` for every
SUCCEEDED step, read from the store on each attempt, never from memory. A resumed
process therefore sees exactly what the first run produced.

`give_up` is the one place `on_failure` is read. With `fail_workflow` the step goes
to FAILED and the workflow finishes FAILED; with `skip` it goes to SKIPPED and the
loop moves on. Classify in `TRIAGE_V1` has two attempts and fails the workflow;
notify has five and is skipped.

## Retries and the failure policy

`RetryableError` means the tool is asking for another go. `FatalError` means this
input can never work, so the step fails on the spot with attempts to spare.
Anything else is treated as retryable. I could have made unexpected exceptions
fatal; under "correctness under failure" I think the default that loses less work
is the right one, and a tool that knows better raises `FatalError`. The cost is that
a real bug, a `KeyError` say, gets retried up to `max_attempts` times before the
step fails. With a base delay of 50 ms that costs almost nothing.

The delay is `min(backoff_base_s * 2^(attempt-1), 2 s)`, with `backoff_base_s` per
step in the spec (default 0.05) and the cap a class constant on the engine.
`next_run_at` is persisted on the row, so a restart during a backoff waits the
remainder. The wait itself is clamped to the cap, because the deadline is wall-clock
time that may have been written by another process; if the clock stepped backwards
by an hour the engine should lose at most one maximum backoff, not an hour. There
is no jitter: one process does not contend with itself, and the retry test checks
exact delays. A fleet would need it.

Step-level failure handling is `on_failure: fail_workflow | skip` in the spec. I
left out `compensate` and any branching; the point was to make failure policy
visibly separate from retries, and that is about ten lines.

## Side effects and the ledger

Only effectful tools use the ledger. The three pure tools declare
`side_effect_free = True` and are re-run freely on retry. Notify does
`ctx.run_effect("email", payload, self.sink.send)`, and the kernel does roughly
this:

```python
def run_effect(ctx, name, payload, fn):
    key = sha256(f"{ctx.workflow_id}:{ctx.step_name}:{name}")
    row = store.get_effect(key)
    if row and row.status == "DONE":
        return row.result                                  # never call fn again
    if row and row.status == "CLAIMED":
        store.event(EFFECT_AMBIGUOUS, key, claimed_by_attempt=row.attempt,
                    tool_idempotent=ctx.tool.idempotent, action=...)
        if not ctx.tool.idempotent:
            raise AmbiguousEffect(...)                     # a FatalError; reason ambiguous_effect
        # idempotent: fall through and call fn again with the SAME key
    else:
        store.claim_effect(key, ...)                       # INSERT CLAIMED + EFFECT_CLAIMED, commit
    result = fn(key, payload)                              # external call, no transaction open
    store.complete_effect(key, result)                     # UPDATE DONE + EFFECT_DONE, commit
    return result
```

The key leaves out the attempt number on purpose. Attempts 1, 2 and 3 of "notify
for workflow W" share one key, so the receiver can dedupe across retries and across
restarts. It also leaves out the spec version, so a migrated workflow presents the
same key; the consequence is that renaming a step changes the key and a migration
that renames one would need an alias.

The tool's `idempotent` flag is the receiver's. `NotifyTool` copies it from its
sink. The demo sink, `FileNotifier`, appends `{key, payload}` to a file and, when
`idempotent=True`, answers `duplicate` for a key it has already written; with
`idempotent=False` it ignores the key and every call is a delivery. There is a test
that this flag means what it says, because the kernel trusts it when deciding
whether to resend.

`CrashingSink` in `faults.py` raises `SimulatedCrash` after the inner receiver
accepted and before the kernel's DONE commit. That is the worst case, and it is
what the demo's step 4 and `test_notify_effect_once_across_crash` run.

Crash points for the notify step, and what the next process does at each:

| | Crash after | On disk | On resume | Duplicate effect? |
|---|---|---|---|---|
| C0 | workflow row inserted, before run | workflow PENDING, steps PENDING | runs normally | no |
| C1 | classify SUCCEEDED committed, before notify `ATTEMPT_STARTED` | notify PENDING, attempt 0 | notify starts attempt 1 | no |
| C2 | `ATTEMPT_STARTED` committed, before `claim_effect` | notify RUNNING, no effect row | `ATTEMPT_INTERRUPTED`, back to PENDING; attempt 2 claims and sends | no (nothing was sent) |
| C3 | CLAIMED committed, before `fn()` returned (the email may or may not have left) | notify RUNNING, effect CLAIMED | `ATTEMPT_INTERRUPTED`; attempt 2 finds CLAIMED and writes `EFFECT_AMBIGUOUS`; `idempotent=True`: resend with the same key, the receiver dedupes; `idempotent=False`: the step fails with `ambiguous_effect` (SKIPPED under notify's `skip` policy) and the row is left for an operator | at most one effect; possibly two calls, with the same key |
| C4 | `fn()` returned (receiver accepted), before DONE committed | same as C3 | same as C3; this is the window no single process can close | same as C3 |
| C5 | DONE committed, before `STEP_SUCCEEDED` committed | notify RUNNING, effect DONE with result | `ATTEMPT_INTERRUPTED`; attempt 2 calls `run_effect`, finds DONE, returns the stored result without calling `fn`; step SUCCEEDED | no |
| C6 | `STEP_SUCCEEDED` committed, before the workflow's SUCCEEDED | notify SUCCEEDED, workflow RUNNING | every step terminal, so the workflow goes to SUCCEEDED | no |
| C7 | workflow SUCCEEDED committed, before `release` | workflow SUCCEEDED, owner set | nothing to do; `run --take-over` clears the stale owner | no |

C3 and C4 look the same on disk. That is why the ledger records CLAIMED and the
decision is handed to the tool's declared idempotency. The tests cover C3 through C5
and the between-steps crash at C1 (`test_restart_resumes_without_rerunning`).

The guarantee, then: with a receiver that honours the idempotency key, the kernel gives
at-least-once invocation of an effect, exactly-once recording of its outcome, and so
an exactly-once effect. With a receiver that cannot dedupe, invocation is at most
once; when a crash makes the outcome unknowable the kernel records
`EFFECT_AMBIGUOUS`, fails the step with that reason, and stops. It never starts a
new execution around the ambiguity, because that would mint a new key and the
receiver would have no way to connect the two.

One thing the ledger does not record is what was sent. A resend rebuilds the
payload from stored outputs, which matches the original only because the tools are
pure functions of those outputs. A payload hash in the ledger, with a resend refused
when it differs, is the first thing I would add; there is a TODO at the spot.

## Ownership: the claim and the fence

`store.claim` is one statement: `UPDATE workflows SET owner=? WHERE id=? AND (owner
IS NULL OR owner=?)`. A rowcount of zero raises `AlreadyOwned` and the CLI prints
"workflow X is owned by <owner>; use --take-over if that process is dead", exit 3.
`--take-over` makes the UPDATE unconditional; the operator is asserting that the
old owner is dead.

The claim on its own would not be enough, because the process to worry about is the
slow one that still thinks it owns the workflow. So every workflow-scoped write
(`transition_workflow`, `transition_step`, `claim_effect`, `complete_effect`,
`append_event`) runs `_fence(wf_id, owner)` inside its `BEGIN IMMEDIATE`
transaction: a `SELECT owner FROM workflows WHERE id = ?` that must return ours, or
`LostClaim` and a rollback. The transition statements then also carry `WHERE state =
<from>` and check `rowcount == 1`, so a row that moved underneath us is caught as
well. `BEGIN IMMEDIATE` rather than `DEFERRED` matters here: the fence has to be
evaluated under the write lock, or it is evaluated against a snapshot that another
process may already have changed. Under contention sqlite3 waits its 5 s busy
timeout and then raises, which the CLI reports as exit 6.

The first version fenced only the transitions. Racing two engines showed that the
ledger and the loose events were unfenced, so a stalled engine A could overwrite the
effect record that engine B had written after taking over.
`test_fenced_out_engine_cannot_finish_the_effect` now pins the fixed behaviour: A
stalls inside the receiver call, B takes over and finishes, and every write A tries
afterwards is refused.

I chose a claim with no expiry over a lease that can be stolen after a timeout. A
lease without a fencing token at commit time is a double-execution generator: a
tool that takes 31 seconds against a 30 second lease lets the old worker and the new
one both commit the same attempt. What a single process can promise without
overstating it is a claim that only an operator clears, with one live engine per
database file as the stated assumption. The lease with a token is described in the README as the first thing to
build for a fleet.

## Resume and durability

No in-memory state is needed to continue; `Engine.run` is a function of the
database and the registry. `run X` resumes an existing workflow and `start --id X`
for an existing id is refused with "already exists" (exit 4).

A step found RUNNING at entry gets `ATTEMPT_INTERRUPTED` and goes back to PENDING
with its attempt count intact. Its `next_run_at` is NULL (it was cleared on
`ATTEMPT_STARTED`), so it reruns immediately. That is fine while resume is a manual
`--take-over`; with automatic lease expiry an interrupted attempt should back off
like a failed one, or an input that crashes the worker every time would be retried
in a tight loop. Either way each crash costs one attempt, which is what stops such
an input from being resumed forever.

The schema version lives in `PRAGMA user_version`. It is read before any DDL runs
and stamped on a fresh file; a file written by a newer kernel is refused
(`StoreError`, exit 4) and handed back untouched.

The restart demonstration in the demo is `start --crash-after classify`, which
wraps the store in `CrashAfterCommit` so that `SimulatedCrash` is raised right after
the post-classify commit; the CLI turns that into `os._exit(1)`, like `kill -9`,
with no cleanup and the claim left on disk. `run X --take-over` then resumes,
`status` shows the first three steps untouched, and the receiver's file has one line.
`test_restart_resumes_without_rerunning` automates it with call counters on every
tool: 1/1/1/0 in the first process, 0/0/0/1 in the second.

## History and replay

`events` is the audit trail: every transition, every retry decision with its delay
and reason, every effect claim, completion and ambiguity, and every tool-level
`DECISION`. `history <id> [--kind TYPE] [--json]` prints them in `seq` order; `--kind
DECISION` answers "what did classify decide and why" with something like `{"label":
"invoice", "confidence": 0.95, "rule": "keyword:invoice,total due,payment terms"}`.

`replay <id>` loads the events for the workflow in `seq` order, folds them through
`rebuild_state()` (a pure reducer over an in-memory projection of `workflows`,
`steps` and `effects`, using the same ALLOWED sets as the store), prints the
narrative one line per event with decisions inline, then loads the stored rows and
diffs. It prints CONSISTENT, or the field-level differences and exit 1. It never
calls a tool and never touches the receiver. The reducer the CLI uses is the one the
tests use.

I thought about making `replay` re-execute, the way the word is sometimes used. That
would re-notify, which is the one thing the brief says must not happen, so re-running
a failed workflow is deliberately not `replay`. If I built it, it would be `start
--derived-from <id>`: a new workflow id, the same input, a provenance event on both
sides, and its own effect keys, because it is a new execution.

## Interfaces

```python
# wfk/tools.py
class RetryableError(Exception): ...
class FatalError(Exception): ...

class Tool(Protocol):
    name: str
    side_effect_free: bool      # True: no ledger, re-run freely (declarative; the kernel does not enforce it)
    idempotent: bool            # True: safe to re-call an ambiguous (CLAIMED) effect with the same key
    def run(self, ctx: "StepContext", inputs: dict) -> dict: ...

class Sink(Protocol):           # receiver of the notify effect
    idempotent: bool
    def send(self, key: str, payload: dict) -> dict: ...

# wfk/engine.py
class StepContext:              # built by the engine per attempt; carries the engine's claim
    workflow_id: str
    step_name: str
    attempt: int
    def record_decision(self, key: str, data: dict) -> None: ...
    def run_effect(self, name: str, payload: dict, fn: Callable[[str, dict], dict]) -> dict: ...
    # never exposes the Store or a connection to the tool

class Engine:
    def __init__(self, store, registry, specs=None, sleep=time.sleep): ...
    def start(self, spec, input, workflow_id=None) -> str     # validates, registers the spec, pins the version
    def run(self, workflow_id, take_over=False) -> str        # final state
    def cancel(self, workflow_id, by="cli") -> str            # state afterwards

# wfk/spec.py
@dataclass(frozen=True)
class StepSpec:
    name: str
    tool: str
    max_attempts: int = 3
    on_failure: str = "fail_workflow"   # or "skip"
    backoff_base_s: float = 0.05

@dataclass(frozen=True)
class WorkflowSpec:
    name: str
    version: int
    steps: Tuple[StepSpec, ...]

TRIAGE_V1 = WorkflowSpec("document_triage", 1, (
    StepSpec("ingest",   "ingest",   max_attempts=3),
    StepSpec("extract",  "extract",  max_attempts=3),
    StepSpec("classify", "classify", max_attempts=2),
    StepSpec("notify",   "notify",   max_attempts=5, on_failure="skip"),
))
```

Dependency direction: `cli` imports `engine`, `store`, `spec`, `tools` and `faults`;
`engine` imports `store`, `spec`, and only `FatalError` and the `Tool` protocol from
`tools`; `tools` imports nothing from the kernel except `StepContext` for type
checking; `faults` imports `store` and `tools`. The spec is
code-defined dataclasses because that is typed and testable with no dependency; the
JSON loader is there so a spec can also come from a file, and `--spec file.json`
works on both `start` and `run`. YAML would have meant a dependency for no gain
here.

Cancellation is cooperative. `cancel` sets a flag that the owning engine checks
between attempts and again after a backoff sleep; a PENDING workflow with no owner
is cancelled on the spot; a terminal workflow refuses. A cancel during a long tool
waits for the tool. Preempting a tool that may be in the middle of an effect is not
safe, and the tools here are short.

The demo tools: `IngestTool` records size and mime type, `ExtractTool` pulls a title
and body out of the text and raises `FatalError` on a document with no text,
`ClassifyTool` matches keyword rules to a label with a confidence and records the
decision, `NotifyTool` builds the email and sends it through the ledger. Fault
injection lives in `faults.py`: `Flaky(inner, fail_times)`, `Counting(inner)`,
`CrashingSink(sink)` and `CrashAfterCommit(Store)`, the last one because a crash
between two steps sits after a commit and cannot be reached from a tool wrapper.

## Tests

Thirty-one tests in seven files, each on a fresh temp SQLite file. `make_engine()`
in `conftest.py` opens a new `Store` on the same file each time, which is how a
second process is modelled. The clock and the sleep function are injected, so the
retry tests check exact delays without waiting. The engine has no test hooks;
crashes are injected inside a receiver call or right after a commit. The README
lists what each file checks.

## What I left out

A transactional outbox and dispatcher; lease expiry and stealing; DAG or branching
specs; YAML; hash-chained events; encryption at rest; `run --follow`; a `tenant_id`
column; a `schema_version` field inside step outputs (the database file itself does
carry `user_version`); preemptive cancellation; metrics; an operator verb to resolve
an ambiguous effect row; `start --derived-from`. Each one is a sentence in the
README, and none of them changes the answer to the question the brief asks.
