# Design debate record (NOT part of the submission zip)

For Ganesh's learning: the three architect proposals and the critic's attack/debate/verdict, verbatim, as received by the review chair on 2026-10-30. The binding outcome is `docs/DESIGN.md`.

---

# PROPOSAL 1

# Design Proposal, Architect #1 (correctness-first lens)

## 1. Shape of the solution

One Python package `wfk/` (stdlib + sqlite3, pytest for tests), ~600 lines. Three layers, strictly separated:

- **`store.py`**: SQLite repository. Only place that knows SQL. Every state change is one transaction.
- **`engine.py`**: orchestration. Pure state-machine logic over the store. Never calls a tool directly.
- **`tools/`**: `Tool` implementations. Know nothing about workflows, DB or retries; they get an input dict and a `ToolContext`.

Single process, single worker loop (`engine.run(workflow_id)`), SQLite in WAL mode. Concurrency across processes is handled by a per-workflow lease, not by threads. That is the intentional simplification: the brief is about correctness under failure, not throughput.

## 2. Data model (SQLite)

```
workflows   (id TEXT PK, spec_name, spec_version, state, input_json,
             current_step_idx, lease_owner, lease_until, created_at, updated_at,
             cancel_requested INT DEFAULT 0)
steps       (workflow_id, step_name, state, attempt INT, max_attempts INT,
             input_json, output_json, last_error, next_attempt_at, updated_at,
             PK(workflow_id, step_name))
events      (seq INTEGER PK AUTOINCREMENT, workflow_id, step_name NULL, ts,
             kind, attempt NULL, data_json)           -- append-only audit log
outbox      (idem_key TEXT PK, workflow_id, step_name, attempt, tool,
             payload_json, state 'PENDING'|'SENT', sent_at, result_json)
```

`events` is the history; `workflows`/`steps` are a materialized projection kept in the same transaction as the event that changed them. Readers never need to replay events to answer `status`; `history` just dumps events. Keeping both (instead of pure event sourcing) costs one extra write per transition and buys trivial queries.

## 3. State machine

Workflow: `PENDING → RUNNING → SUCCEEDED | FAILED | CANCELLED`.
Step: `PENDING → RUNNING → SUCCEEDED | FAILED | SKIPPED` with `RUNNING → PENDING` (retry scheduled) as the only backward edge.

Transitions are a static table in `engine.py` (`ALLOWED = {(PENDING, RUNNING), ...}`); `store.transition()` refuses anything not in it and writes the `events` row atomically with the update. Terminal states are absorbing: a crash-and-resume cannot un-finish a step.

## 4. Step execution and retry

```
loop:
  wf = store.acquire_lease(wf_id, owner, ttl=30s)   # UPDATE ... WHERE lease_until < now OR lease_owner = owner
  if wf.cancel_requested and step not RUNNING: transition wf -> CANCELLED; break
  step = next non-terminal step in spec order
  if step is None: wf -> SUCCEEDED; break
  if step.next_attempt_at > now: sleep/return (CLI run just reports "waiting")
  T1: step -> RUNNING, attempt += 1, event STEP_STARTED(attempt)
  result = tool.run(input, ctx)                      # outside any transaction
  T2 (on success): step -> SUCCEEDED, output stored, event STEP_SUCCEEDED
  T2 (on exception): if attempt < max_attempts: step -> PENDING, next_attempt_at = now + backoff(attempt), event STEP_RETRY_SCHEDULED(error)
                     else: step -> FAILED, wf -> FAILED, event STEP_FAILED / WORKFLOW_FAILED
```

Step-level failure policy from the spec: `on_failure: fail_workflow | skip` (skip marks the step `SKIPPED` and continues; useful for `notify`). Backoff is `base * 2**attempt`, capped; a `RetryableError` vs non-retryable distinction lets a tool short-circuit to FAILED (e.g. malformed document).

Step input = workflow input + outputs of prior steps (`{"ingest": {...}, "extract": {...}}`). Stored as JSON, so a resumed process reconstructs it from the DB, never from memory.

## 5. Idempotency and side effects (the notify problem)

The invariant: **a side effect is performed at most once per idempotency key, and the key is derived from durable state, not from the attempt.**

- `idem_key = sha256(workflow_id, step_name, spec_version)`; **not** the attempt number, because a retry after a crash must find the earlier record.
- `tools/notify.py` is split: the tool itself only *enqueues* `outbox(idem_key, payload)` with `INSERT OR IGNORE`, inside T2 of the step. So "decide to notify" and "step succeeded" commit together.
- A separate `Dispatcher` (same process, run after each engine loop and on startup) does: `SELECT PENDING outbox` → call `Notifier.send(idem_key, payload)` → `UPDATE state=SENT`. The notifier receives the key so a real provider (email API, webhook) can dedupe on its side too.

Why this is the honest answer: a process can die between `send()` returning and `UPDATE SENT`. That window is unavoidable without a transactional endpoint, so the guarantee is **at-least-once delivery with an idempotency key the receiver can dedupe on, and exactly-once *recording*. Exactly-once *effect* holds iff the receiver honours the key.** The README states this plainly rather than claiming exactly-once.

The demo notifier appends to `notifications.jsonl` keyed by `idem_key` and refuses duplicates, so the test can prove "no duplicate side effect" end to end.

## 6. Crash points (enumerated, each with the recovery)

| # | Crash after | On resume |
|---|---|---|
| C1 | `start` inserted workflow, before run | lease free, step PENDING → runs normally |
| C2 | T1 committed (step RUNNING), tool not yet called | lease expires; step found RUNNING with stale lease → treated as failed attempt, retry scheduled (event `ATTEMPT_ABANDONED`) |
| C3 | tool ran, T2 not committed | same as C2; tool re-runs. Safe for ingest/extract/classify (pure/idempotent on input). Notify never sends inside the tool, so nothing leaks |
| C4 | T2 committed (outbox PENDING), dispatcher not run | dispatcher picks it up on startup |
| C5 | `send()` succeeded, `SENT` not committed | re-send with same key; receiver dedupes (documented at-least-once window) |
| C6 | workflow SUCCEEDED, CLI printing | nothing to do |

A test (`test_restart.py`) kills the engine at C2 and C3 via an injected `crash_after` hook, reopens the DB, resumes, and asserts exactly one outbox row and one notification line.

## 7. Durability and resume

- SQLite `PRAGMA journal_mode=WAL; synchronous=FULL`. Every transition is `BEGIN IMMEDIATE … COMMIT`.
- Resume = `wfk run <id>` or `wfk resume --all` (any workflow in RUNNING with expired lease, or PENDING). No in-memory state is required to continue; the engine is a function of the DB.
- Leases prevent two processes running the same workflow; TTL + owner id handles a dead holder.

## 8. Audit / history

`events` kinds: `WORKFLOW_CREATED, WORKFLOW_STARTED, STEP_STARTED, STEP_SUCCEEDED, STEP_RETRY_SCHEDULED, STEP_FAILED, STEP_SKIPPED, ATTEMPT_ABANDONED, DECISION, OUTBOX_ENQUEUED, OUTBOX_SENT, CANCEL_REQUESTED, WORKFLOW_SUCCEEDED/FAILED/CANCELLED`.
`DECISION` events are how tools record "why": `ctx.record_decision("classify", {"label": "invoice", "confidence": 0.91, "rule": "keyword:invoice"})`. The brief's "query decisions made during execution" becomes `wfk history <id> --kind DECISION`.

## 9. CLI

```
wfk start  --spec triage.json --input doc.json [--id ID]   # creates + runs to completion or first wait
wfk status ID                 # workflow + per-step table (state, attempt/max, last_error, next_attempt_at)
wfk history ID [--kind K] [--json]
wfk cancel ID                 # sets cancel_requested; honoured at next step boundary (never mid-tool)
wfk replay ID [--from STEP] [--dry-run]
wfk resume --all              # restart safety entry point
```

**Replay, defined precisely:** replay creates a **new** workflow `ID~r1` with the same spec version and input, copies the recorded outputs of steps before `--from` (default: first non-SUCCEEDED step) as already-SUCCEEDED, links it via a `REPLAYED_FROM` event on both, and runs from there. Idempotency keys include the *new* workflow id, so a replay of a succeeded workflow *does* notify again, deliberately, because the operator asked for a new execution; `--dry-run` runs with the outbox dispatcher disabled. The original is never mutated: history is immutable. (Alternative rejected: in-place re-run, because it would overwrite audit data.)

## 10. Interfaces

```python
class Tool(Protocol):
    name: str
    def run(self, input: dict, ctx: ToolContext) -> dict: ...

@dataclass
class ToolContext:
    workflow_id: str; step_name: str; attempt: int
    record_decision(self, key, data) -> None
    enqueue_effect(self, payload) -> str      # outbox, returns idem_key

# spec (JSON)
{"name": "document_triage", "version": 1,
 "steps": [
   {"name": "ingest",   "tool": "ingest",   "max_attempts": 3},
   {"name": "extract",  "tool": "extract",  "max_attempts": 3},
   {"name": "classify", "tool": "classify", "max_attempts": 2},
   {"name": "notify",   "tool": "notify",   "max_attempts": 5, "on_failure": "skip"}]}
```

Tools are registered in a `ToolRegistry` by name; the engine resolves `spec.step.tool` at run time. Orchestration never imports a concrete tool.

## 11. Test plan (pytest, ~6 tests, <1 s)

1. Retry: `FlakyExtract(fail_n=2)` → attempt reaches 3, succeeds, events show 2 `STEP_RETRY_SCHEDULED`.
2. Max attempts → step FAILED, workflow FAILED, later steps untouched.
3. Idempotency: run notify step twice via crash-at-C3 → one outbox row, one notification line.
4. Restart safety: crash at C2, resume in a fresh engine instance, workflow SUCCEEDED.
5. Illegal transition raises and writes nothing.
6. Cancel: requested mid-run, honoured at step boundary, remaining steps not started.

## 12. Design sections

**A. Scaling.** State ownership stays with the workflow id: shard by `hash(workflow_id)` across Postgres partitions; each row owned by at most one lease holder. Execution model: the engine loop becomes a stateless worker pulling `(workflow_id)` from a queue (SQS/Redis streams) with visibility timeout = lease TTL; the outbox dispatcher becomes its own worker pool (classic transactional-outbox relay). Tool execution moves to separate services called over gRPC with the `idem_key` in the request; the orchestrator never holds tool code. Failure boundaries: orchestrator crash → lease expiry and requeue; tool service crash → attempt abandoned, retry; outbox relay crash → at-least-once resend keyed. 10k concurrent workflows is a few hundred rows/sec of transitions: Postgres handles it; the only hot spot is the outbox poll, fixed with `SKIP LOCKED` and partitioning by `sent_at`.

**B. Failure semantics: crash during notify.** Naive design sends inside the step and marks success after: crash between send and commit → resume re-runs notify → duplicate email. This design: the step only enqueues an outbox row (committed with the step's success), the dispatcher sends with a key. Crash between `send()` and `SENT`: second send carries the same key; a receiver that dedupes drops it. Tradeoff: at-least-once at the edge, extra table, slight latency; and we depend on the receiver honouring keys. For a receiver that cannot, we would wrap it with a dedupe cache of our own in front of it, accepting a small window.

**C. Versioning.** Spec is pinned per workflow (`spec_version` stored at start); a running workflow finishes on the version it started with. New versions are additive; a tool keeps its name and gets `tool_version` in its registry key (`notify@2`); specs reference `tool@version`. Breaking changes = new tool name. Replay uses the recorded version. Migrations of step input/output shapes are done by a reader-side adapter, never by rewriting events.

**D. Multi-tenancy / compliance.** `tenant_id` on every table, included in `idem_key`, and in every query via a `Store(tenant)` wrapper; events are append-only and hash-chained (`prev_hash` column) so an auditor can detect tampering; PII in payloads stored encrypted at rest with per-tenant keys and a retention job that tombstones `payload_json` while keeping the event skeleton.

## 13. Tradeoffs and intentional simplifications

- Single worker, SQLite, polling dispatcher: correct and demonstrable; not fast. Documented path to queues.
- No mid-tool cancellation; tools are short.
- Backoff is time-based with a sleep in `run`; no scheduler process.
- Outbox gives at-least-once at the boundary; honestly stated, not hidden.
- No spec DAGs: linear steps only, which is all the brief needs.
- Pure-ish tools (ingest/extract/classify) are allowed to re-execute on resume; only effectful tools go through the outbox.

---

# PROPOSAL 2, PROPOSAL 3 and the CRITIC'S DEBATE

> NOTE FOR THE ORCHESTRATOR: the chair's transcription of this file was cut off partway through Proposal 2 and could not be resumed in the same session. Proposal 1 above is complete and verbatim. Proposals 2 and 3 and the critic's report (attacks, debate transcript, verdict) must be appended here verbatim from the orchestrator's own input text (sections "=== PROPOSAL 2 ===", "=== PROPOSAL 3 ===", "=== DEBATE ==="). The verdict's decisions 1-13 are reflected one-for-one in `docs/DESIGN.md` §1 (D1-D15), so the binding outcome is not affected.


---
## Appendix: Proposals 2 and 3 and the critic's debate, verbatim (labels corrected Oct 31)

### Proposal 2: Architect #2 (simplicity-first)

# Design Proposal — Architect #2 (Simplicity-first)

## 1. Shape of the codebase

Four nouns, one file each, ~500 lines total:

```
wfk/
  tools.py     # Tool protocol + 4 triage tools (pure functions over a context)
  spec.py      # WorkflowSpec: ordered steps, per-step max_attempts
  store.py     # Store: sqlite single file, 3 tables, all transitions here
  engine.py    # Engine: the loop; owns state machine, retry, resume
  cli.py       # start / status / cancel / replay
tests/
```

Rule of thumb for the interview: **Engine decides, Store remembers, Tool does.** Nothing else holds state.

## 2. Data model (sqlite, one file, WAL mode)

```sql
workflows(id TEXT PK, spec_name, spec_version, input_json,
          state, created_at, updated_at)
steps(workflow_id, step_name, state, attempt INT DEFAULT 0,
      output_json, error, idem_key, updated_at,
      PRIMARY KEY(workflow_id, step_name))
events(seq INTEGER PK AUTOINCREMENT, workflow_id, step_name,
       ts, kind, attempt, detail_json)     -- append-only audit
```

Tradeoff — event-sourced vs state table: I keep **both, but the state tables are authoritative and `events` is append-only audit**. Pure event sourcing is elegant but forces a fold on every read and makes "what is the state now?" a derived question; a 3–5 h kernel should answer `status` with one `SELECT`. The two stay consistent because every transition is written in one transaction: `UPDATE steps … ; INSERT INTO events …`. `replay` later proves they agree.

## 3. State machine

Workflow: `PENDING → RUNNING → SUCCEEDED | FAILED | CANCELLED`.
Step: `PENDING → RUNNING → SUCCEEDED | FAILED` (step `FAILED` only after `attempt == max_attempts`; a failed attempt with budget left goes back to `PENDING` with `attempt` incremented).

Transitions are a single `ALLOWED = {(from, to), …}` set checked in `Store.transition()`; an illegal transition raises. Terminal states are never left. The workflow becomes `FAILED` when any step is `FAILED`; `SUCCEEDED` when the last step succeeds.

## 4. Step execution and retry

```
Engine.run(workflow_id):
  wf = store.load(wf_id); if terminal: return
  store.transition(wf, RUNNING)
  for step in spec.steps:
     row = store.step(wf_id, step.name)
     if row.state == SUCCEEDED: continue            # resume skips done work
     while row.attempt < step.max_attempts:
        if store.is_cancelled(wf_id): transition CANCELLED; return
        store.begin_attempt(row)                    # RUNNING, attempt+1, event ATTEMPT_STARTED
        try:
           out = tool.run(ctx)                       # tool execution, outside the engine
           store.succeed(row, out)                   # SUCCEEDED + event
           break
        except RetryableError as e: store.fail_attempt(row, e)   # PENDING + event
        except Exception as e:  store.fail_step(row, e); transition wf FAILED; return
     else: fail_step; FAILED; return
  transition wf SUCCEEDED
```

Retries are linear, synchronous, with a tiny backoff (`0.1 * attempt`s). Retryable vs fatal is the tool's decision (two exception types), the policy (max attempts) is the spec's; the engine only mediates. Step inputs are the outputs of prior steps, read from `steps.output_json`, so a resumed run sees exactly what the first run produced.

## 5. Idempotency / side effects (Notify)

The only step with an external effect is `notify`. Strategy:

1. **Deterministic idempotency key** = `sha256(workflow_id + step_name + spec_version)`, computed by the engine and stored in `steps.idem_key` **before** the attempt starts (same transaction as `begin_attempt`).
2. `NotifyTool.run(ctx)` passes `ctx.idem_key` to the sink. The sink (an `OutboxSink` writing to a `notifications(idem_key PK, payload, sent_at)` table in the same sqlite file — stands in for email/Slack) does `INSERT OR IGNORE`. A second attempt with the same key is a no-op.
3. The engine records `SUCCEEDED` only after the sink returns.

The fundamental limit: a crash **between** the sink accepting and the DB commit means the next attempt re-calls the sink — safe only because the sink dedupes on the key. In other words: *at-least-once execution + idempotent receiver = effectively-once.* I'd state this plainly in the README rather than pretend exactly-once.

## 6. Durability and resume

- Every transition is a committed sqlite transaction; no in-memory state survives a call.
- A crash leaves at most one step in `RUNNING`. On `engine.run` (called by `start` again, or a `resume`-style re-run), the engine treats `RUNNING` as "attempt interrupted": it emits `ATTEMPT_INTERRUPTED`, moves the step back to `PENDING` (attempt already counted) and continues. Non-idempotent tools therefore run at most `max_attempts` times across restarts.
- Single process, single writer: I use a `BEGIN IMMEDIATE` on `begin_attempt` as the lease, so two concurrent `start` calls on the same id cannot both execute a step. No heartbeat/lease-expiry — intentionally simplified.

## 7. Audit model and CLI

`events` kinds: `WF_CREATED, WF_STATE, ATTEMPT_STARTED, ATTEMPT_FAILED, ATTEMPT_INTERRUPTED, STEP_SUCCEEDED, STEP_FAILED, DECISION, CANCEL_REQUESTED`. `DECISION` carries tool-level reasoning (e.g. classify: `{"label":"invoice","confidence":0.91,"rule":"keyword"}`) so "what did it decide and why" is queryable, not just "it succeeded".

CLI (`python -m wfk …`):

- `start <doc.json> [--spec triage]` → creates workflow + 4 PENDING steps, runs engine, prints id and final state. Re-running `start --id X` is **resume**.
- `status <id>` → workflow row + per-step table (state, attempt, idem_key, error).
- `cancel <id>` → sets `cancel_requested=1`; the engine checks the flag between attempts; a terminal workflow refuses. (Cooperative, not preemptive.)
- `replay <id>` → **defined precisely as**: print the ordered `events` for the workflow and *re-fold them in memory* into a derived (workflow_state, step_states, attempts) using the same `ALLOWED` table; compare against the `steps`/`workflows` rows and print `CONSISTENT` or the diff. Replay is read-only, never re-executes tools and never re-sends notifications. (`--verbose` prints decisions.) This gives one word two useful meanings — history inspection and self-audit — without inventing a "re-run" that would conflict with idempotency.

## 8. Interfaces

```python
class Tool(Protocol):
    name: str
    def run(self, ctx: StepContext) -> dict: ...   # raise RetryableError / FatalError

@dataclass(frozen=True)
class StepContext:
    workflow_id: str; step_name: str; attempt: int; idem_key: str
    inputs: dict          # outputs of previous steps, by step name
    sink: NotificationSink

@dataclass(frozen=True)
class StepSpec: name: str; tool: str; max_attempts: int = 3
@dataclass(frozen=True)
class WorkflowSpec: name: str; version: int; steps: tuple[StepSpec, ...]
```

The triage spec is code-defined (`SPECS = {"triage": WorkflowSpec(...)}`) plus a `from_json` loader; tools are registered in a dict `TOOLS: dict[str, Tool]`. Orchestration (`engine.py`) imports neither triage tools nor the CLI; tools import nothing from the engine. Failure injection for tests is a tool-level `FlakyTool(fail_times=2)` and a `CrashingSink` that raises after insert.

## 9. Test plan (pytest, tmp sqlite per test)

1. `test_retry_then_success`: Extract fails twice, succeeds on attempt 3; assert `attempt==3`, 2 `ATTEMPT_FAILED` events, workflow `SUCCEEDED`.
2. `test_max_attempts_exhausted`: `max_attempts=2`, always fails → step `FAILED`, workflow `FAILED`, Notify never ran.
3. `test_notify_idempotent_across_restart`: sink raises *after* `INSERT`; engine crashes; re-run `engine.run`; assert exactly one `notifications` row.
4. `test_resume_skips_succeeded_steps`: kill after Classify (simulate by raising in Notify), re-run, assert Ingest/Extract/Classify tools called once total (counting tool stubs).
5. `test_replay_consistent`: after any run, `replay` reports `CONSISTENT`.
6. `test_illegal_transition_raises`.

Restart safety is also demoed: `start`, `kill -9` mid-run via a `--crash-after classify` flag, `start --id`, `replay`.

## 10. Design sections

**A. Scaling.** State ownership stays with one store (Postgres instead of sqlite); the engine becomes a stateless worker pool pulling `(workflow_id)` from a queue. Leases: `claimed_by, lease_until` on `workflows`, claimed with `UPDATE … WHERE lease_until < now() RETURNING`; heartbeat extends. Partition by `workflow_id` hash so one workflow is only ever executed by one worker at a time. Multi-service: tools become RPCs; the engine keeps the idem key and treats the tool boundary as the failure boundary — a tool call that times out is `RetryableError`, and every external tool must accept the key. 10k concurrent workflows is trivially within Postgres row counts; the real limit is notification-sink throughput, handled by the outbox pattern (already in the design) drained by a separate publisher.

**B. Failure: crash during Notify.** Sink accepts the message, process dies before `STEP_SUCCEEDED` commits. On restart the step is `RUNNING`, engine marks `ATTEMPT_INTERRUPTED`, retries, the sink sees the same idem key and ignores. Tradeoff: we depend on the sink deduping; a sink without idempotent semantics would require a two-phase outbox (write intent, publish, mark) and would still only give at-least-once. We accept one duplicate call, never a duplicate effect.

**C. Versioning.** `workflows.spec_version` is pinned at creation; the registry holds every version, so a running workflow finishes on the spec it started with. Tool changes: additive output fields only; renames = new tool name. Changing `max_attempts` or step order = new spec version. Migrating in-flight workflows is an explicit, logged `MIGRATE` event, never implicit.

**D. Compliance.** `events` is append-only (no UPDATE/DELETE path in `Store`), every row timestamped with attempt number and decision payload, so an auditor can reconstruct why a document got its label and when the notification went out. Tenancy: add `tenant_id` to every table and scope all queries; the idem key already includes `workflow_id`, which would embed the tenant.

## 11. Intentional simplifications

Sequential steps only (no DAG); synchronous single-process engine; cooperative cancel; no lease expiry; sqlite outbox as the "real" sink; backoff is cosmetic; no spec loader for YAML beyond JSON. Each is a named gap in the README, with the "next step" from section A.

### Proposal 3: Architect #3 (evolution-first)

# Design Proposal — Architect #3 (Evolution-first lens)

**Thesis.** Build the smallest kernel whose *data model* is already the one Temporal/Step Functions converge on: an append-only event log is the source of truth, materialized state tables are a cache, and every side effect is guarded by a durable, pre-written idempotency key. If those three choices are right on day one, going from one SQLite file to 10k concurrent workflows across services is a storage/transport swap, not a rewrite.

## 1. Data model (SQLite, 4 tables)

```sql
workflows(id TEXT PK, spec_name, spec_version INT, input_json, status,
          created_at, updated_at, cancel_requested INT DEFAULT 0,
          tenant_id TEXT DEFAULT 'default')
steps(workflow_id, name, idx INT, status, attempt INT DEFAULT 0, max_attempts INT,
      input_json, output_json, error, next_run_at, PK(workflow_id, name))
events(seq INTEGER PK AUTOINCREMENT, workflow_id, step_name NULL, type, attempt,
       payload_json, created_at)              -- append-only; never UPDATE/DELETE
side_effects(key TEXT PK, workflow_id, step_name, attempt, status, result_json,
             created_at)                      -- status: CLAIMED | DONE
```

`events` is the history (`WORKFLOW_STARTED, STEP_SCHEDULED, STEP_STARTED, STEP_SUCCEEDED, STEP_FAILED, RETRY_SCHEDULED(delay, reason), STEP_EXHAUSTED, EFFECT_CLAIMED, EFFECT_DONE, CANCEL_REQUESTED, WORKFLOW_SUCCEEDED/FAILED/CANCELLED`). Every "decision" (retry or not, why, which key) is an event with a payload, so *query decisions* = `SELECT` on events. `workflows`/`steps` are derivable from `events` (and a test asserts that: rebuild from log == tables).

## 2. State machine

Workflow: `PENDING → RUNNING → SUCCEEDED | FAILED | CANCELLED`.
Step: `PENDING → RUNNING → SUCCEEDED | FAILED(terminal, attempts exhausted)`, with `RUNNING → PENDING` on a retryable failure (attempt++, `next_run_at` set). Transitions live in one `transition(from, to)` table in `state.py`; illegal transitions raise, so a bug cannot silently corrupt a row. Workflow status is a pure function of step statuses.

## 3. Step execution and retry

The orchestrator is a loop: `pick next runnable step → mark RUNNING (+event) → call tool → record result → advance`. Tool exceptions are typed: `RetryableError` → schedule attempt N+1 with exponential backoff (`base * 2^n`, capped) if `attempt < max_attempts`, else step FAILED and workflow FAILED; `FatalError` → immediate FAILED. Any unexpected exception is treated as retryable (safer default under "correctness under failure"). Step-level `on_failure: fail_workflow | skip` in the spec covers the brief's "step-level failure handling" without growing a DAG engine.

Every tool call is wrapped as `(workflow_id, step, attempt)`; a step whose `RUNNING` row is found at startup with no terminal event is a **crash-in-flight** and is re-run as a new attempt (see §5).

## 4. Idempotency / side-effects (notify)

Two layers, both cheap:

1. **Deterministic effect key.** `key = sha256(workflow_id:step_name:effect_name)` — *not* including attempt, so retries of the same logical effect share one key. The kernel *claims* the key in `side_effects` (INSERT, status CLAIMED, committed) **before** invoking the effect, then marks DONE after. Insert conflict on DONE → skip, return stored result (exactly-once from the kernel's view). Conflict on CLAIMED → the previous process died mid-effect: ambiguous; the kernel logs `EFFECT_AMBIGUOUS` and re-sends **only if the tool declares `idempotent=True`** (passes the key downstream as `Idempotency-Key`, which real email/SMS APIs honor); otherwise fails the step with a clear reason. This is the honest answer: exactly-once at the boundary requires the receiver to dedupe by key.
2. **Pure tools are retried freely.** Ingest/Extract/Classify are declared `side_effect_free=True` and skip the ledger.

## 5. Durability and resume after restart

Every transition is `BEGIN; INSERT event; UPDATE steps/workflows; COMMIT` in one transaction (WAL mode). On `start`/`resume`, the kernel scans for workflows `RUNNING` and steps `RUNNING` or `PENDING` with `next_run_at <= now`, emits `RESUMED_AFTER_CRASH`, and continues. Because tool output is persisted per step, resume never re-runs completed steps — the Temporal "replay from history" property, achieved here by materialized step outputs rather than re-executing code. Restart test: run with a `CRASH_AFTER=classify` env hook (`os._exit(1)` after commit), restart, assert ingest/extract/classify not re-run and notify sent exactly once.

## 6. Audit/history and CLI

- `start <spec> --input file.json [--id X]` → prints id (client-supplied id makes *start* itself idempotent).
- `status <id>` → workflow + per-step table (attempt/max, last error, next_run_at).
- `history <id>` → the events in seq order, with payloads (the "query decisions" requirement).
- `cancel <id>` → sets `cancel_requested`, event; the loop checks the flag *between* steps (cooperative; never kills a step mid-effect). Terminal states are immutable.
- `replay <id>` — **precise definition:** *rebuild the derived state from the event log and compare it with the stored tables, printing the step-by-step narrative and any divergence (exit 1 on mismatch).* It is read-only. This is what "replay" means in Temporal (re-deriving state from history), and it doubles as a consistency check. Re-executing a failed workflow is a different verb: `start --from <id>` (new workflow id, same input, with its own effect keys, provenance event `DERIVED_FROM`). Not conflating the two is a deliberate design statement.
- `run` → worker loop (processes all runnable work, then exits or `--follow`).

## 7. Interfaces

```python
class Tool(Protocol):
    name: str; version: int = 1
    side_effect_free: bool          # skip ledger when True
    idempotent: bool = False        # may re-send under an ambiguous CLAIMED key
    def run(self, ctx: StepContext, inp: dict) -> dict: ...
# ctx exposes workflow_id, step, attempt, effect_key(name), logger — never the DB.

WORKFLOW = WorkflowSpec(name="document_triage", version=1, steps=[
  Step("ingest",   tool="ingest",   max_attempts=3),
  Step("extract",  tool="extract",  max_attempts=3),
  Step("classify", tool="classify", max_attempts=2),
  Step("notify",   tool="notify",   max_attempts=5, backoff_base_s=0.1)])
```

Linear steps, each step's input = previous step's output merged with workflow input. Code-defined spec (typed, testable); a YAML loader is a 20-line adapter if wanted.

**Separation:** `engine/` (store, state machine, scheduler, effect ledger) imports no tool; `tools/` imports only `Tool`/`StepContext`; `cli.py` wires them via a registry `{name: Tool}`. Fault injection for tests is a tool-side wrapper (`FlakyTool(inner, fail_times=2)`), so the engine has zero test hooks besides `CRASH_AFTER`.

## 8. Test plan (pytest, ~8 tests, < 1 s)

retry succeeds after N failures and history shows N `RETRY_SCHEDULED`; max-attempts exhaustion → FAILED with reason; notify effect runs once across two retries of the notify step; CLAIMED-but-not-DONE key with non-idempotent tool fails safely; crash-after-classify restart (subprocess) → no duplicate notify; cancel between steps; `replay` rebuild equals tables; illegal transition raises.

## 9. Design sections

**A. Scaling & evolution.** State ownership: each workflow is owned by exactly one *shard* (hash of workflow id); the shard's event log is the authority — same schema, moved to Postgres (or one SQLite per shard behind a partition map). Execution model: the in-process loop becomes workers pulling *tasks* (`run step X attempt N`) from a queue with visibility timeouts; a task is leased (`lease_owner, lease_until`) and a worker that loses its lease (heartbeat) is fenced by a `lease_token` checked at commit — the DB conflict-detects, so two workers never both commit attempt N. Tool execution moves to separate services called over RPC with the effect key in the request; the orchestrator stays stateless apart from the log. Failure boundaries: orchestrator crash → lease expiry → task redelivered; tool service crash → typed error → retry policy; DB partition → that shard pauses, others unaffected. At 10k concurrent workflows a single Postgres with the events table partitioned by `(tenant, month)` is comfortable; history grows ~50 events/workflow, so retention/archival to object storage is a compliance decision, not a capacity one.

**B. Failure semantics — crash during notify.** Timeline: key CLAIMED committed → SMTP call → process killed before DONE. On restart the step is RUNNING with a CLAIMED key: we *cannot know* whether the email left. Handling: with `idempotent=True` we re-send with the same `Idempotency-Key` and the provider dedupes; with `False` we fail the step with `EFFECT_AMBIGUOUS` and surface it in `status` for a human, rather than guess. Tradeoff: at-least-once vs at-most-once is chosen per tool, explicitly, and the ledger makes the choice auditable. The alternative (claim+send+mark in one transaction) is impossible because the send is external — the two-phase ledger is the minimum honest mechanism.

**C. Versioning.** `WorkflowSpec.version` and `Tool.version` are pinned on the workflow row at start; the registry holds multiple versions, and a running workflow always resolves the version it started with (Temporal's "workflow versioning", Step Functions' definition revisions). Rules: never edit a published version in place; adding a step = new spec version; tool output schema changes = new tool version with an adapter; step outputs are stored as JSON with a `schema_version`, so a v2 reader can upgrade v1 outputs on read. Draining: old versions stay registered until `SELECT count(*) FROM workflows WHERE spec_version=1 AND status='RUNNING'` is zero.

**D. Multi-tenancy & compliance.** `tenant_id` on every table and in every effect key (so tenants can never collide on a key); row-level filtering in the store; per-tenant rate/concurrency limits in the scheduler; separate storage per regulated tenant if required. The append-only `events` table *is* the audit trail: who/what/when/why for every transition, immutable by construction (no UPDATE path in code; a trigger can enforce it in Postgres). Payload redaction hook for PII, retention policy per tenant, and `replay` as the tamper check (log ⇔ state).

## 10. Intentional simplifications

Linear workflows only (no DAG/branching); single process/single worker (lease columns designed, not exercised); backoff without jitter; cooperative cancel only; tools are local fakes (notify writes to an outbox file); no spec YAML; no metrics. Each is named in README with the one-line path to the real thing.

### Critic's report and debate

# Critic's report: three proposals for the Trase durable workflow kernel

Brief read (`/Users/bb-8-m5-pro/claude-code-ws/trase-takehome/_private/brief.txt`). The grader asked for: state machine PENDING→RUNNING→SUCCEEDED|FAILED, retries with max-attempts, step-level failure handling, persist + safe resume, no duplicate notify, history that can answer "what was decided and why", Tool interface + spec, orchestration separated from execution, CLI start/status/cancel/replay, one test (retry OR idempotency), restart safety demonstrated or described, README with sections A–D. "Clarity and correctness over features." 3–5 hours.

## Part 1: attacks

### Proposal 1 (outbox + dispatcher + leases + fork-replay)

1. **The outbox does not close the window it claims to address.** C5 ("send() succeeded, SENT not committed") is exactly the same hole as "sink accepted, STEP_SUCCEEDED not committed" in the other two designs. The outbox moves the hole from the step boundary to the dispatcher boundary and adds a table, a dispatcher loop and a second retry mechanism. Net correctness gain: zero. Net explainability cost: the interviewer now asks "why does notify have two retry policies, max_attempts on the step and the dispatcher's re-poll?"
2. **`enqueue_effect` runs inside the tool, which runs outside any transaction.** The text says "inside T2 of the step", but the tool is called between T1 and T2. Either the context buffers the enqueue and flushes it in T2 (then say so; it is the only correct reading), or the INSERT commits while the step is still RUNNING and a dispatcher can send for a step whose attempt then raises and is retried/failed. Ambiguous spec = bug in 3-hour implementation.
3. **Lease expiry without fencing is a double-execution generator.** "RUNNING with stale lease → treated as abandoned, retry" is unsafe if the original holder is merely slow (a 31-second tool with a 30-second TTL): two processes execute the same attempt and both try to commit T2. P3's section A mentions a `lease_token` checked at commit; P1 implements the lease but not the fence. Implemented half-mechanisms are worse than documented assumptions.
4. **Replay re-notifies by design.** `replay` of a SUCCEEDED workflow creates `ID~r1` with a new idem key and *sends the email again*. The brief's headline requirement is "no duplicate side effects (especially notify)". A reviewer typing `replay <succeeded-id>` and getting a second notification will read it as a failure of the core requirement regardless of the footnote.
5. **Over-build for the time box:** `tools/` package, dispatcher, lease TTLs, `ATTEMPT_ABANDONED`, `resume --all`, `history --kind`, `--dry-run`, hash-chained events, per-tenant encryption. Section D proposes `prev_hash` columns; if it is in the schema it must be tested; if not, it is prose and belongs in README only.
6. **Section B is good but hedged twice** ("exactly-once effect iff receiver honours the key... for a receiver that cannot, a dedupe cache of our own"). The second clause reintroduces a window and weakens the answer. Pick one guarantee and state it.

### Proposal 2 (simplicity-first, dedupe sink in the same SQLite file)

1. **The idempotency proof is vacuous as written.** The "sink" is a `notifications` table in the same SQLite file. If the sink INSERT and the step's SUCCEEDED UPDATE share a connection, they are one transaction and there is no crash window at all, so the test proves nothing about external effects. If they use separate connections, say so explicitly and explain that the sink stands in for SMTP, otherwise the interviewer's first question ("what if the sink is an HTTP API?") exposes that the design never modelled the real boundary.
2. **"BEGIN IMMEDIATE as the lease" is a check-and-set, not a lease.** It prevents two concurrent `begin_attempt`s on a PENDING row; it does nothing for the case where process A is still executing the tool and process B sees RUNNING, declares `ATTEMPT_INTERRUPTED` and re-runs it. The design must say "single live process per DB is an assumption" in plain words, or add a lease.
3. **Section A claims "the outbox pattern (already in the design)".** It is not; the design has a dedupe sink. Internal inconsistency will be noticed.
4. **Test 3 is mis-specified.** "Sink raises after INSERT; engine crashes; re-run." If the sink raises a `RetryableError`, the engine retries in the same process and no restart happens. The crash must be a process-level abort (`os._exit`, or a `SimulatedCrash` exception that the engine does not catch, followed by a fresh `Engine` on the same file).
5. **No step-level failure policy beyond retries.** The brief lists "step-level failure handling" separately from retries. `on_failure: skip | fail_workflow` is ten lines and makes the requirement visibly addressed.
6. **Unexpected exceptions are fatal.** Under "correctness under failure" a transient `ConnectionResetError` surfacing from a tool should not kill the workflow permanently; the safer default is retry up to max_attempts.
7. **Backoff is cosmetic and not persisted.** Fine for the demo, but then do not list "retry after restart honours backoff" anywhere.
8. Strengths worth keeping: the one-line mental model (Engine decides, Store remembers, Tool does), replay as a fold with a CONSISTENT check, the smallest schema.

### Proposal 3 (claim ledger, event log as truth, evolution-first)

1. **`EFFECT_AMBIGUOUS` with `idempotent=False` ends in a dead end.** It fails the step, so the workflow becomes FAILED (terminal, absorbing). The only way out is `start --from <id>`, which generates a *new* effect key and sends again, which is exactly the duplicate the ledger was meant to prevent. Either the operator path is "resolve the ambiguous key by hand" (`effects resolve <key> --done|--not-done`, which is more code) or the honest statement is "a non-idempotent effect that crashes mid-flight needs a human; the kernel refuses to guess and refuses to fork". Say the latter; do not build the former.
2. **Where does the claim/DONE live relative to the step transaction?** Three commits per effectful step (CLAIMED, DONE, STEP_SUCCEEDED). Crash between DONE and STEP_SUCCEEDED: resume sees RUNNING with a DONE key, returns stored result, marks step SUCCEEDED. Correct, but the proposal never spells out that the DONE row must carry the result so the step can succeed without re-calling the tool. Make it explicit.
3. **Tool API leaks.** `ctx.effect_key(name)` gives the tool a key but who claims? If the tool claims, the tool knows about the ledger. The clean form is `ctx.run_effect(name, payload, fn)`: the kernel claims, calls `fn(key, payload)`, marks DONE. Tools stay DB-ignorant.
4. **"Rebuild from log == tables" test is the best test in all three proposals, but only if the reducer is the same code `replay` uses.** Two reducers is twice the bugs.
5. **Scope creep:** `tenant_id` column (unused), `run --follow`, `start --from`, `schema_version` in outputs, `Tool.version`, backoff scheduler with `next_run_at`. Each is a defensible sentence in README and an indefensible 20 lines in a 4-hour codebase.
6. **"Any unexpected exception is retryable" is right but needs one guard:** `FatalError` for the demo tools (malformed input) so the FAILED path is reachable without exhausting attempts.
7. The thesis ("the data model is already the one Temporal converges on") is a strong interview line, but only if the event log is *actually* sufficient to rebuild state. The `side_effects` table must then be derivable from `EFFECT_CLAIMED/DONE` events too, or the thesis has an exception on day one.

### Cross-cutting

- All three honestly conclude the same thing: a crash between external send and local commit is unavoidable; the guarantee is at-least-once delivery + a stable key the receiver dedupes on. The difference is purely in how visible and how cheap that admission is. P3's CLAIMED state makes the ambiguity a first-class, queryable fact; P1 hides it inside a dispatcher; P2 hides it inside a sink.
- All three agree: state tables authoritative, append-only `events` in the same transaction, `ALLOWED` transition set, terminal states absorbing, key = `sha256(workflow_id, step, ...)` excluding attempt, cooperative cancel, linear steps, typed exceptions.
- The real fork is `replay`. P1 = re-execute as a fork (and re-notify). P2/P3 = read-only rebuild from history. The brief lists replay beside start/status/cancel, so a reviewer may expect an action; but the same brief says no duplicate side effects. A read-only replay that prints the narrative and verifies the projection is the only definition that cannot violate the brief.
- Python 3.9 compatibility: no `X | Y` in runtime annotations without `from __future__ import annotations`, no `match`, no `dataclass(slots=True)`, `tuple[StepSpec, ...]` only under the future import. Pytest via venv, `.venv` git-ignored.

## Part 2: debate transcript

**Critic:** Start with the thing you all hedged on. Crash after the email leaves, before the commit. Who guarantees what?

**A1:** At-least-once at the edge, keyed. The outbox gives the step a clean commit and a relay that retries independently of the step. That is how every production system does it.

**Critic:** Every production system has a separate relay because the producer and the sender are different services. Here they are one loop in one process. You added a table and a second retry policy and the crash window is byte-for-byte the same as A2's. What does the interviewer get from it?

**A1:** Decoupling: step success is not hostage to the provider being up.

**A3:** But the step *is* "notify". If the provider is down, the right observable state is "notify, attempt 2 of 5, next at 10:04", not "notify SUCCEEDED, outbox PENDING". Your design makes `status` lie about the thing the brief cares most about.

**A2:** My version is cheapest: the sink dedupes on the key, done.

**Critic:** Your sink is a table in the same file. If the test shares a connection, the crash window does not exist and the test is theatre. Convince me it models SMTP.

**A2:** Separate connection, own commit. I will state it. The point stands: the kernel's job is to hand a stable key to an idempotent receiver.

**A3:** Agreed on the key, but the receiver cannot always dedupe, and the kernel should *know* when it does not know. CLAIMED-but-not-DONE is a fact; recording it lets `status` say "ambiguous" instead of "retrying" and lets the tool declare whether re-sending with the key is safe.

**Critic:** And when it is not safe, your workflow is FAILED and the only exit is a fork that mints a new key and sends again.

**A3:** Fair. The fork must not be the exit. The honest rule is: non-idempotent effect, ambiguous outcome, kernel stops and a human resolves it. I will not build the resolver; I will write the sentence.

**Critic:** Replay. A1, you re-execute and re-notify.

**A1:** Replay as an action is what an operator expects. New workflow id, provenance event, nothing overwritten. Re-notifying on a *new execution* is correct, not a duplicate.

**A2:** The brief says "no duplicate side effects, especially notify", and then lists `replay` in the CLI. The only replay that cannot be read as violating the first line is one that never calls a tool. Mine folds the events and checks they agree with the tables. It is the audit requirement and the CLI requirement in one command.

**A3:** Same, and it is what the word means in Temporal. Re-running a failed workflow is `start` with a `--derived-from` provenance tag, if at all.

**Critic:** A1?

**A1:** I concede the default. Keep a fork behind a different verb or leave it in the README.

**Critic:** Leases. A1 implemented expiry without a fence; A2 has a BEGIN IMMEDIATE and calls it a lease; A3 designed columns and did not use them.

**A1:** Expiry with retry is what SQS visibility timeout does.

**A3:** SQS plus an idempotent consumer. Without a fencing token at commit, a slow tool and an expired lease produce two committed attempts. Either fence or do not expire.

**A2:** Then assume one live process per database file, say it in the README, and let `start` on a RUNNING workflow with a RUNNING step mean "the previous process is dead". That is the 3-to-5-hour truth.

**Critic:** I want one UPDATE-WHERE claim so two CLI invocations cannot both run a workflow, and a `WHERE owner = ?` on every transition so a lost claim cannot commit. Fifteen lines, and it answers the inevitable "two workers" question with code, not prose. No TTL-based stealing.

**A1:** Acceptable.

**Critic:** Unexpected exceptions. A2 says fatal, A3 says retryable.

**A2:** A `KeyError` is a bug; retrying it three times is noise.

**A3:** A `ConnectionResetError` is not a bug, and the tool author will not have typed it. Under "correctness under failure" the default that loses less is retry-to-exhaustion; `FatalError` exists for the tool that knows better.

**Critic:** A3 wins; backoff is small so the cost is nothing. Step-level failure handling: `on_failure: skip`?

**A2:** Ten lines, and it makes "step-level failure handling" visibly distinct from "retries". I will take it.

**Critic:** Scope. A1 has six CLI verbs, a dispatcher, a tools package, hash chains. A3 has tenant columns, schema versions, `--follow`. A2 has nothing to cut but nothing that proves the restart.

**A1:** The README can carry the hash chain and the encryption; the code will not.

**A3:** Tenant and schema_version go to section D and C as sentences. `next_run_at` stays: it is one column and it is what makes backoff survive a restart.

**A2:** My restart test becomes a real crash: an uncaught `SimulatedCrash` after the post-classify commit, a fresh `Engine` on the same file, tool call counters asserting zero re-runs and one notification.

**Critic:** Last: event log as truth or tables as truth?

**A3:** Log is truth, tables are a cache, and the `replay` test proves they agree. That is the Temporal line and it costs one reducer that `replay` already needs.

**A2:** Tables answer `status` in one SELECT; nobody folds on the read path.

**Critic:** Those are compatible. Tables are what the engine reads; events are what an auditor and `replay` read; one transaction keeps them equal, and the reducer proves it. Keep both and say exactly that sentence.

## Part 3: verdict (decisions to build)

1. **Shape:** single package `wfk/` with `spec.py`, `store.py`, `engine.py`, `tools.py`, `cli.py`, `tests/`; ~500–650 lines. P2's mental model in the README: *Engine decides, Store remembers, Tool does.* Python 3.9-compatible (`from __future__ import annotations`, no `match`, no `slots=True`).
2. **Schema (4 tables):** `workflows`, `steps` (with `attempt`, `max_attempts`, `output_json`, `error`, `next_run_at`), `events` (append-only, written in the same transaction as every state change), `effects(key PK, workflow_id, step_name, status CLAIMED|DONE, result_json, created_at)`. No `tenant_id`, no hash chain, no outbox, no dispatcher.
3. **State machine:** workflow `PENDING→RUNNING→SUCCEEDED|FAILED|CANCELLED`; step `PENDING→RUNNING→SUCCEEDED|FAILED|SKIPPED`, `RUNNING→PENDING` on retry. One `ALLOWED` set in `store.transition()`, illegal transition raises and writes nothing. Terminal states absorbing.
4. **Retry:** `RetryableError` and any unexpected exception → `attempt+1`, `next_run_at = now + min(base*2**attempt, cap)` persisted, back to PENDING; `FatalError` → FAILED immediately; exhaustion → FAILED. Per-step `on_failure: fail_workflow | skip` (skip → SKIPPED, continue). Sleep in-process only when `next_run_at` is in the future.
5. **Side effects (P3's ledger, trimmed):** `ctx.run_effect(name, payload, fn)`: key = `sha256(workflow_id:step:name)` (no attempt); kernel INSERTs CLAIMED and commits, calls `fn(key, payload)`, UPDATEs DONE with result and commits. On re-entry: DONE → return stored result without calling `fn`; CLAIMED → log `EFFECT_AMBIGUOUS`; if the tool declares `idempotent=True`, re-call with the same key; else raise `FatalError("ambiguous effect; needs operator")`. The demo notifier dedupes on the key and writes to a file via its own connection/handle, never inside the step transaction. README states the guarantee as: *at-least-once invocation, exactly-once recorded, exactly-once effect iff the receiver honours the key; ambiguity is surfaced, never guessed.*
6. **Resume:** `start --id X` / `run X` on a RUNNING workflow finds the RUNNING step, emits `ATTEMPT_INTERRUPTED`, moves it to PENDING (attempt already counted) and continues; completed steps are never re-run because outputs are persisted. Restart test: uncaught `SimulatedCrash` after the post-classify commit, new `Engine` on the same file, assert tool call counts and exactly one notification. One subprocess `--crash-after classify` path for the demo.
7. **Claim, not lease-with-expiry:** `UPDATE workflows SET owner=? WHERE id=? AND (owner IS NULL OR owner=?)`; every transition includes `AND owner=?`; owner cleared at terminal/exit. No TTL stealing; README states "one live process per DB file; a dead owner is cleared by `run --take-over`" (or just documented).
8. **Replay = read-only.** `replay <id>` folds `events` through the same reducer `rebuild_state()` used by the test, prints the step-by-step narrative with decisions, compares with the tables, prints CONSISTENT or the diff, exit 1 on mismatch. Never calls a tool, never notifies. Fork-and-re-execute lives in the README as a future `start --derived-from`.
9. **Decisions:** `ctx.record_decision(key, data)` → `DECISION` event; retry/skip/ambiguity decisions are events with payloads. `history <id> [--kind]` dumps events.
10. **CLI:** `start`, `status`, `cancel` (cooperative, flag checked between attempts; terminal refuses), `replay`, `history`. Spec is code-defined dataclasses with an optional JSON loader only if it stays under 20 lines.
11. **Tests (6, <2 s):** retry-then-success with N `RETRY_SCHEDULED`; exhaustion → FAILED, notify never called; notify effect once across a mid-notify crash (CLAIMED/DONE paths); ambiguous + non-idempotent fails safely; restart after classify re-runs nothing and notifies once; `rebuild_state(events) == tables`; illegal transition raises. Test tools are wrappers (`Flaky`, `Crashing`), engine has no test hooks except `crash_after`.
12. **README sections A–D:** A = P3's text (shard by workflow id, workers pull tasks with leases + fencing token at commit, tools as RPC with effect key, failure boundaries listed). B = the notify crash timeline through the CLAIMED/DONE ledger, including the non-idempotent dead end and why the kernel refuses to guess. C = spec and tool versions pinned on the workflow row, additive changes only, drain old versions. D = tenant_id in every table and effect key, append-only events as the audit trail, `replay` as the tamper check; prose only.
13. **Explicitly cut and named in the Time Log:** outbox/dispatcher, lease expiry, DAGs, YAML, hash chains, encryption, `--follow`, `tenant_id`, schema_version, preemptive cancel.
