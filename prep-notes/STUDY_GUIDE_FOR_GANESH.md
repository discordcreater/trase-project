# Study guide: own every line of `wfk` (NOT SUBMITTED)

Purpose: the live round forbids AI. By the end of this guide you can rebuild any function from
memory, narrate a run event by event, explain every crash point, and extend the kernel on a
whiteboard or in an editor without hesitating. Read it in order; each section assumes the one
before. Total: ~2 hours of reading plus the drills in `INTERVIEW_PREP.md`.

The whole system in one sentence you can say in your sleep:

> **Engine decides, Store remembers, Tool does.** Every state change is one SQLite transaction that
> updates a row and appends the events that explain it, fenced on the state machine and on the
> owner; side effects go through a ledger keyed by `(workflow, step, effect)` so a crash can never
> turn into a silent duplicate.

---

## 0. Numbers to have cold

| Thing | Value |
|---|---|
| Modules in `wfk/` | 7: `spec`, `store`, `engine`, `tools`, `faults`, `cli`, `__main__` (~1,300 lines total; `store` 460, `engine` 395, `cli` 250, `tools` 155, `faults` 90, `spec` 65) |
| Tables | 4: `workflows`, `steps`, `effects`, `events` |
| Event types | 16 (closed set; listed in §3.3) |
| Workflow transitions | 5: P→R, P→C, R→S, R→F, R→C |
| Step transitions | 7: P→R, R→S, R→P, R→F, R→SK, P→F, P→SK |
| CLI verbs | 6: `start run status cancel history replay` |
| Exit codes | 0 ok, 1 replay inconsistent, 2 FAILED/CANCELLED, 3 owned, 4 store refused, 5 bad input, 6 db error |
| Tests | 31 in 7 files, 0.1 s |
| Backoff | `min(base · 2^(attempt−1), 2.0)`, base 0.05 s → 0.05, 0.1, 0.2, 0.4 … |
| Triage spec | ingest 3 attempts, extract 3, classify 2, notify 5 + `on_failure=skip` |
| Effect key | `sha256("{workflow_id}:{step_name}:{effect_name}")`, no attempt number |
| Owner token | `uuid4().hex[:8]` per `Engine.run` call; workflow id default `uuid4().hex[:12]` |
| Events in a clean run | 16 (see §3.2); the demo's crash2 has 20 |
| SQLite pragmas | `journal_mode=WAL`, `synchronous=FULL`, `foreign_keys=ON`; `isolation_level=None` |

---

## 1. Mental model and vocabulary

- **Workflow**: one execution of a spec over one input. Row in `workflows`. States
  `PENDING → RUNNING → SUCCEEDED | FAILED | CANCELLED`.
- **Step**: one named slot in a spec, bound to a tool. Row in `steps`. States
  `PENDING → RUNNING → SUCCEEDED | FAILED | SKIPPED`, plus the backward edge `RUNNING → PENDING`
  (retry scheduled, or interrupted attempt found on resume) and `PENDING → FAILED | SKIPPED`
  (interrupted on the last attempt, no budget left).
- **Attempt**: one invocation of a tool for a step. `steps.attempt` counts attempts *started*.
- **Tool**: the only code that does work. Protocol: `name`, `side_effect_free`, `idempotent`,
  `run(ctx, inputs) -> dict`.
- **StepContext (ctx)**: what a tool is allowed to know (workflow_id, step_name, attempt) and do
  (`record_decision`, `run_effect`). It carries the engine's owner token so every write it causes
  is fenced. The tool never sees the store.
- **Effect**: an external side effect performed through `ctx.run_effect(name, payload, fn)`.
  Row in `effects` keyed by the effect key. Status `CLAIMED` → `DONE`.
- **Event**: an append-only row in `events`. Written inside the same transaction as the state change
  it explains, or (for `DECISION` and `EFFECT_AMBIGUOUS`) on its own but still fenced.
- **Owner / claim**: `workflows.owner` is the token of the single live engine. Claim =
  `UPDATE … WHERE owner IS NULL OR owner = ?`. Every write re-reads `owner` inside its transaction
  (`_fence`). A process that lost the claim gets `LostClaim` and its transaction is rolled back.
- **Fence**: two of them on every step transition: (1) the owner must match; (2) the row's current
  `state` must equal the `frm` the engine thinks it is (`WHERE state = ?`, rowcount must be 1).
- **Replay**: read-only. Fold the event log through the same state machine into a projection;
  diff against the tables. Never runs a tool.
- **Resume**: `run <id>` on a non-terminal workflow. Finds any `RUNNING` step, marks it
  `ATTEMPT_INTERRUPTED`, puts it back to `PENDING`, continues from the first non-terminal step.

Layering (who imports whom):

```
cli  ──►  engine  ──►  store      (engine never imports tools' concrete classes, only the Protocol
 │          │           ▲           and the error types)
 │          └──►  spec  │
 ├──►  tools  (TYPE_CHECKING-only import of StepContext)
 └──►  faults ──► store, tools      (nothing in the kernel imports faults)
```

---

## 2. Module by module, function by function

### 2.1 `spec.py` (65 lines)

- `ON_FAILURE = ("fail_workflow", "skip")`.
- `StepSpec` (frozen dataclass): `name`, `tool` (registry key), `max_attempts=3`,
  `on_failure="fail_workflow"`, `backoff_base_s=0.05`. `__post_init__` validates
  `max_attempts >= 1` and `on_failure` in the tuple.
- `WorkflowSpec` (frozen): `name`, `version: int`, `steps: Tuple[StepSpec, ...]`. `__post_init__`
  rejects duplicate step names. `.key` → `(name, version)`.
- `TRIAGE_V1 = WorkflowSpec("document_triage", 1, (ingest 3, extract 3, classify 2, notify 5 skip))`.
- `SPECS = {TRIAGE_V1.key: TRIAGE_V1}`: the registry of published versions.
- `load_spec_json(path)`: reads `{"name","version","steps":[{name,tool,max_attempts?,on_failure?}]}`;
  `KeyError`/`TypeError` → `ValueError("malformed spec")`.

Why frozen dataclasses: a spec version is immutable once published (versioning rule C). Why
`version: int` and a tuple key: `workflows.spec_name + spec_version` pins a running workflow.

### 2.2 `store.py` (465 lines): the only module that talks to SQLite

**Constants.** `WF_ALLOWED`, `STEP_ALLOWED` (the two state machines as set literals),
`WF_TERMINAL`, `STEP_TERMINAL`, `STEP_FIELDS = {"attempt","output_json","error","next_run_at"}`
(the only columns a step transition may set besides `state`; anything else is a `ValueError`,
which also blocks SQL injection through the f-string `SET` clause).

**Errors.** `StoreError` base → `NotFound`, `AlreadyExists`, `IllegalTransition` (nothing written),
`LostClaim` (rolled back), `AlreadyOwned(workflow_id, owner)` (carries `.owner` for the CLI message).

**Schema** (§5 below).

**Helpers.** `to_iso(ts)` UTC ISO with microseconds; `from_iso(s)`; `dumps(obj)` canonical JSON
(`sort_keys`, compact separators) so stored JSON compares byte-for-byte.

**Row dataclasses.** `WorkflowRow`, `StepRow`, `EffectRow`, `Event`; `EventSpec = (type, payload)`.

**`Store.__init__(path, clock=time.time)`**: `sqlite3.connect(path, isolation_level=None)` → Python
does no implicit `BEGIN`; we issue `BEGIN IMMEDIATE`/`COMMIT` ourselves. `row_factory = sqlite3.Row`.
`PRAGMA user_version` is read FIRST: newer than `SCHEMA_VERSION` (1) → close, `StoreError` (exit 4),
file untouched; then `executescript` runs the three pragmas and `CREATE TABLE IF NOT EXISTS …`, and a
fresh file (0) is stamped. `clock` is injected so
tests control time.

**`_tx()`** (contextmanager): `BEGIN IMMEDIATE`; yield; `COMMIT` inside the `try`; on any
`BaseException` → `ROLLBACK` and re-raise. `BEGIN IMMEDIATE` takes the write lock at the start, so
every SELECT in the block (the fence included) is what the COMMIT is based on: no
read-then-write race between two engines.

**`_fence(wf_id, owner)`**: `SELECT owner FROM workflows WHERE id=?`. No row → `NotFound`.
`owner != expected` → `LostClaim`. `None` means "must be unowned" (used by `cancel` before first
run). Called inside every workflow-scoped write transaction.

**`_append(wf_id, step, attempt, events)`**: `executemany` INSERT into `events`. Only inside a tx.

**Workflows.**
- `create_workflow(wf_id, spec_name, spec_version, input, steps=[(name, max_attempts)])`: one tx:
  INSERT workflow (`IntegrityError` → `AlreadyExists`), INSERT all steps `PENDING` with `idx`,
  append `WORKFLOW_CREATED{spec_name, spec_version, input, steps}`.
- `load_workflow`, `list_workflows` (ordered by `created_at, id`).
- `claim(wf_id, owner, take_over=False)`: tx; `take_over` → unconditional `UPDATE owner`; else
  `WHERE owner IS NULL OR owner = ?`. `rowcount != 1` → load the row (raises `NotFound` if
  absent) and raise `AlreadyOwned(current)`. Append `OWNER_CLAIMED{owner, take_over}`.
- `release(wf_id, owner)`: `UPDATE owner=NULL WHERE owner=?`; append `OWNER_RELEASED` only if
  rowcount 1 (a release by a fenced-out engine is a silent no-op, by design).
- `transition_workflow(wf_id, frm, to, owner, reason)`: check `WF_ALLOWED` first (outside the tx,
  nothing written); tx: `_fence`; `UPDATE state WHERE id=? AND state=frm`; rowcount≠1 →
  `LostClaim`; append `WORKFLOW_STATE{from,to,reason}`.
- `request_cancel(wf_id, by)`: **not fenced** (anyone may ask); sets `cancel_requested=1`;
  `NotFound` if no row; append `CANCEL_REQUESTED{by}`.
- `cancel_requested(wf_id)` → bool.

**Steps.**
- `load_steps` (ordered by `idx`), `load_step` (`NotFound`).
- `transition_step(wf_id, name, frm, to, owner, events, attempt_no=None, **fields)`: check
  `STEP_ALLOWED`; check `fields ⊆ STEP_FIELDS`; build `SET state=?, updated_at=?, k=?…`; tx:
  `_fence`; `UPDATE … WHERE workflow_id=? AND name=? AND state=frm`; rowcount≠1 → `LostClaim`;
  `_append(wf_id, name, attempt_no, events)`. `attempt_no` is the attempt stamped on the events;
  `attempt=` in `fields` is the column value.

**Effects ledger.**
- `get_effect(key)` → `EffectRow | None`.
- `claim_effect(key, wf_id, step, effect_name, attempt, *, owner)`: tx; fence; INSERT status
  `CLAIMED`; append `EFFECT_CLAIMED{key, effect_name, attempt}`. (An `IntegrityError` here would
  mean the engine inserted twice; it never does because it checks `get_effect` first and holds the
  claim.)
- `complete_effect(key, result, *, owner, attempt)`: tx; `get_effect` (`NotFound`); fence on the
  row's workflow; `UPDATE status='DONE', result_json`; append `EFFECT_DONE{key, result}` stamped
  with the **completing** attempt (after a resume, that is not the claiming attempt; the table's
  `attempt` column keeps the claiming one).
- `effects_for(wf_id)`.

**Events.**
- `append_event(wf_id, step, type, attempt, payload, *, owner)`: a loose explanatory event
  (`DECISION`, `EFFECT_AMBIGUOUS`); still fenced.
- `events(wf_id, kind=None)` ordered by `seq`.
- There is no `UPDATE events` or `DELETE FROM events` anywhere in the file: append-only by
  construction.

### 2.3 `engine.py` (401 lines): orchestration

- `AmbiguousEffect(FatalError)`: raised when a `CLAIMED`-not-`DONE` effect meets a non-idempotent
  tool.
- `effect_key(workflow_id, step_name, effect_name)`: sha256 of the colon-joined triple. No attempt
  number: every attempt of the same logical effect presents the same key.
- `StepContext(engine, workflow_id, owner, step, attempt, tool)`: public attrs `workflow_id`,
  `step_name`, `attempt`; private `_engine`, `_owner`, `_tool`.
  - `record_decision(key, data)` → `store.append_event(..., "DECISION", attempt, {key, data},
    owner=_owner)`.
  - `run_effect(name, payload, fn)` → `engine._run_effect(...)`.
- `Engine(store, registry, specs=None, sleep=time.sleep)`: `specs` is a **copy** of `SPECS`
  (`start` may register a file spec); `sleep` injected for tests. `BACKOFF_CAP_S = 2.0`.

**`start(spec, input, workflow_id=None) -> wf_id`**: validate `input` is a dict; reject input keys
that equal a step name (outputs are merged by step name into later inputs); `_check_tools(spec)`
(every `step.tool` in the registry); `self.specs.setdefault(spec.key, spec)` and refuse a different
body under the same key; `create_workflow`. Does **not** run.

**`cancel(workflow_id, by="cli") -> state`**: load; terminal → `IllegalTransition`; `request_cancel`;
if `PENDING` and unowned → `transition_workflow(PENDING→CANCELLED, owner=None)` (a `LostClaim`
here means an engine claimed meanwhile; swallow it, the flag will be honoured). Return the state.

**`run(workflow_id, take_over=False) -> state`**:
1. load; terminal → (if `take_over` and a stale owner is set, release it) return state. Never claims.
2. `_spec_for(wf)`: resolve `(spec_name, spec_version)` in `self.specs`, `ValueError` if missing;
   `_check_tools`. Fails **before** claiming.
3. `owner = uuid4().hex[:8]`; `store.claim(workflow_id, owner, take_over)`.
4. `try: state = _run_owned(...) except Exception: release; raise`. A `BaseException`
   (`KeyboardInterrupt`, `SystemExit`, `SimulatedCrash`) is "the process died": propagates **without**
   release, leaving the stale owner a real crash leaves.
5. release; return.

**`_run_owned(wf_id, owner, spec)`**:
1. reload under the claim; if `PENDING` → `RUNNING` (`"started"`).
2. Resume: for every step `RUNNING` → `transition_step(RUNNING→PENDING, [ATTEMPT_INTERRUPTED
   {attempt}], attempt_no=row.attempt)`. Attempt count kept; `next_run_at` left as-is (it is always
   `None` for a `RUNNING` step because `ATTEMPT_STARTED` clears it).
3. For each step in spec order: `SUCCEEDED`/`SKIPPED` → continue; `FAILED` → `_finish(FAILED)`
   (only reachable after a crash between `STEP_FAILED` and the workflow's `FAILED`); else
   `_run_step`; result `FAILED`/`CANCELLED` → `_finish` with that state.
4. `_finish(SUCCEEDED, "all steps succeeded or skipped")`.

**`_finish(wf_id, owner, state, reason)`**: `transition_workflow(RUNNING→state)`; return state.

**`_inputs(wf_id)`**: `dict(workflow.input)` + `{row.name: row.output}` for every `SUCCEEDED` step,
read from the store every time (never cached in memory: that is what makes resume trivial).

**`_run_step(wf_id, owner, step) -> "SUCCEEDED" | "SKIPPED" | "FAILED" | "CANCELLED"`**, a `while True`:
1. `row = load_step`.
2. `cancel_requested` → return `"CANCELLED"` (checked **between** attempts, never mid-tool).
3. `row.attempt >= row.max_attempts` → only an interrupted last attempt gets here (a retry is never
   scheduled without budget). `_give_up(frm=row.state (PENDING), reason="exhausted", error="attempt N
   of N was interrupted; no attempts left (error before it: …)", events=[])`. No `ATTEMPT_FAILED`:
   it was interrupted, not failed.
4. `_wait_until(row.next_run_at)`: sleep the **remainder** (`deadline − clock()`), if positive, clamped
   to `BACKOFF_CAP_S` (a clock that stepped backwards costs at most one max backoff); returns whether
   it waited. If it waited → re-read `cancel_requested` → `"CANCELLED"` (a cancel that landed during
   the sleep must not run one more attempt).
5. `attempt = row.attempt + 1`; `transition_step(PENDING→RUNNING, [ATTEMPT_STARTED{attempt}],
   attempt_no=attempt, attempt=attempt, next_run_at=None)`.
6. `ctx = StepContext(...)`; `output = tool.run(ctx, self._inputs(wf_id))` with **no transaction
   open**.
7. Exceptions, in this order:
   - `FatalError` → `ATTEMPT_FAILED{retryable: False}`; reason `"ambiguous_effect"` if it is an
     `AmbiguousEffect` else `"fatal"`; `_give_up(frm="RUNNING")`.
   - `StoreError` → re-raise. The kernel's own failure inside `ctx.*` (a lost claim) is never
     treated as the tool's failure.
   - any other `Exception` (`RetryableError` and the unexpected) → `retryable = attempt <
     max_attempts`. Not retryable → `_give_up("exhausted")`. Else `delay = min(base·2^(attempt−1),
     2.0)`, `next_run_at = clock()+delay`; `transition_step(RUNNING→PENDING, [ATTEMPT_FAILED,
     RETRY_SCHEDULED{attempt, next_run_at, delay_s, error, reason}], error=…, next_run_at=…)`;
     `continue`.
8. Success → `_encode_output(output)` first (not a dict / not JSON-serializable → `FatalError`,
   caught by the ladder in 7: one attempt, reason `fatal`); then `transition_step(RUNNING→SUCCEEDED,
   [STEP_SUCCEEDED{attempt, output}], output_json=…, error=None)`; return `"SUCCEEDED"`.

**`_give_up(wf_id, owner, step, frm, attempt, reason, error, events)`**: `on_failure == "skip"` →
`SKIPPED` + `STEP_SKIPPED{attempt, error, reason, policy:"skip"}`; else `FAILED` +
`STEP_FAILED{attempt, error, reason}`. One `transition_step(frm→to, events+[ev], error=error)`.
Returns the new state. This is the whole of "step-level failure handling".

**`_run_effect(wf_id, owner, step_name, attempt, tool, name, payload, fn)`**:
1. `key = effect_key(...)`; `row = get_effect(key)`.
2. `row.status == "DONE"` → return `row.result` (never call `fn`).
3. `row` exists (`CLAIMED`, not `DONE`) → `action = "resend" if tool.idempotent else "fail"`; append
   `EFFECT_AMBIGUOUS{key, claimed_by_attempt, tool_idempotent, action}`; not idempotent → raise
   `AmbiguousEffect` (the ledger row stays `CLAIMED` for an operator). Idempotent → fall through,
   **no new claim row**.
4. no row → `claim_effect(...)` commits `CLAIMED`.
5. `result = fn(key, payload)` outside any transaction.
6. `complete_effect(key, result, owner, attempt)` commits `DONE`. Return result.

**Replay.** `Projection(workflows, steps, effects)`; `_move(obj, frm, to, allowed, where)` raises
`IllegalTransition` if `obj["state"] != frm` or the pair is not allowed; `rebuild_state(events)` is
a pure reducer over the 16 types (see §3.3 for what each does to the projection; `ATTEMPT_FAILED`,
`EFFECT_AMBIGUOUS`, `DECISION` change nothing). `consistency_diff(store, wf_id)` compares the
projection with the rows field by field (workflow: state, cancel_requested, owner; step: state,
attempt, output, error, next_run_at; effect: status, result) and reports steps present in the table
but absent from the log.

### 2.4 `tools.py` (164 lines)

- `RetryableError`, `FatalError` (plain `Exception` subclasses).
- `Tool` Protocol: `name`, `side_effect_free` (declaration: safe to re-run; the kernel does not read
  it, it is for reviewers and a future lint; the structural guarantee is that the only kernel-
  protected way to do an effect is `run_effect`), `idempotent` (a statement about the *receivers*
  of this tool's effects: they dedupe on the key), `run(ctx, inputs)`.
- `Sink` Protocol: `idempotent`, `send(key, payload) -> dict`.
- `IngestTool`: needs `inputs["document"]` dict else `FatalError`; returns `doc_id`, `mime`,
  `size_bytes`.
- `ExtractTool`: `document.text` must be a non-empty str else `FatalError` ("retrying cannot
  help"); returns `title` (first line, ≤80 chars), `body`, `word_count`.
- `ClassifyTool`: `RULES` = invoice / contract / resume keyword lists; best label by keyword hit
  count; `confidence = min(0.5 + 0.15·hits, 0.95)` or 0.3 if no hits; `ctx.record_decision("label",
  decision)`; returns `{label, confidence, rule}`.
- `NotifyTool(sink)`: `idempotent = sink.idempotent`; builds `{to, subject, body}` from
  `classify`/`extract`/`ingest` outputs; `return {"email": ctx.run_effect("email", payload,
  self.sink.send)}`. The only effectful step.
- `FileNotifier(path, idempotent=True)`: appends one JSON line per *key* to a file with its own
  handle (`flush` + `fsync`), keeps a `_seen` dict loaded once per process (like a provider's dedupe
  index); `calls` counts invocations including deduped ones; `send` returns
  `{"status":"duplicate", key}` on a seen key, else writes and returns `{"status":"sent", key, to}`.

### 2.5 `faults.py` (90 lines): nothing in the kernel imports it

- `SimulatedCrash(BaseException)`: stands in for `kill -9`; not an `Exception`, so no handler
  swallows it and `Engine.run` does not release the claim.
- `_Wrapper(inner)` copies `name`, `side_effect_free`, `idempotent`.
- `Flaky(inner, fail_times)`: `RetryableError` on the first N calls, then delegates. `calls` counter.
- `Counting(inner)`: counts calls.
- `CrashingSink(inner)`: `inner.send(...)` then raise `SimulatedCrash`: the receiver accepted, the
  process dies before `DONE` (crash point C4).
- `CrashAfterCommit(Store)`: overrides `transition_step`; after a `SUCCEEDED` commit for the named
  step, raise `SimulatedCrash`: the between-steps crash point no tool wrapper can reach.
- `parse_fail_spec("extract:2,notify:1") → {"extract": 2, "notify": 1}`.

### 2.6 `cli.py` (249 lines)

- `build_registry(notifications, fail=None, crash_in_notify=False)`: `FileNotifier` sink; four
  tools; `CrashingSink` wrap when asked; `Flaky` wraps from `--fail`; unknown tool → `ValueError`.
- `_engine(args, store) -> (Engine, WorkflowSpec)`: `--spec` is a JSON file path (loaded and
  registered for this process) or `name@version` (default `document_triage@1`); unknown →
  `ValueError` listing registered specs.
- `print_status`: workflow line, created/updated, a step table (state, `attempt/max`,
  `next_run_at`, output or error, truncated at 90 chars with `…`), effect rows.
- `_NARRATORS`: one lambda per event type, built once; `narrate(e)` → `seq  time  step  TYPE  text`.
- `cmd_start`: load input JSON; `engine.start`; print; `--no-run` → 0 else `_run`.
- `cmd_run`: `_run(engine, id, take_over)`.
- `_run`: `engine.run`; `AlreadyOwned` → message + exit 3; `SimulatedCrash` → message and
  `os._exit(1)` (no cleanup, no release); else print status, exit 0 if `SUCCEEDED` else 2.
- `cmd_status`: one workflow or all.
- `cmd_cancel`: `Engine(store, {}).cancel(id)` (no tools needed); prints `CANCELLED`, or who owns
  it and the `--take-over` route, or "no live engine, CANCELLED on next run".
- `cmd_history`: `load_workflow` first (unknown id → exit 4, not an empty list); `--kind`, `--json`.
- `cmd_replay`: no events → `NotFound`; print narrative; `rebuild_state` (raises on an illegal
  log); print the derived summary; `consistency_diff` → `INCONSISTENT` exit 1 or `CONSISTENT` 0.
- `main(argv)`: argparse with a shared parent parser (`--spec --fail --crash-after
  --crash-in-notify`) for `start` and `run`; store is `CrashAfterCommit` when `--crash-after`;
  error mapping `StoreError→4`, `ValueError|OSError→5`, `sqlite3.Error→6`; `finally: store.close()`.

---

## 3. The exact control flow of a run

### 3.1 `python -m wfk start examples/doc.json --id w1`

```
main → Store("wfk.db") → cmd_start
  → json.load(doc) → _engine → build_registry, specs
  → Engine.start(TRIAGE_V1, doc, "w1")
      validate input dict; no key collision; tools present; register spec
      store.create_workflow        TX#1: INSERT workflows PENDING, 4× INSERT steps PENDING, WORKFLOW_CREATED
  → _run → Engine.run("w1")
      load (PENDING, not terminal); _spec_for; owner="ab12cd34"
      store.claim                  TX#2: UPDATE owner WHERE owner IS NULL; OWNER_CLAIMED
      _run_owned
        transition_workflow        TX#3: PENDING→RUNNING; WORKFLOW_STATE
        resume loop: nothing RUNNING
        ingest:   _run_step
          load_step; cancel? no; attempt 0 < 3; wait: none
          transition_step          TX#4: PENDING→RUNNING attempt=1; ATTEMPT_STARTED
          IngestTool.run(ctx, {document, notify_to})            (no tx open)
          transition_step          TX#5: RUNNING→SUCCEEDED output_json; STEP_SUCCEEDED
        extract:  TX#6 ATTEMPT_STARTED, run, TX#7 STEP_SUCCEEDED
        classify: TX#8 ATTEMPT_STARTED, run → ctx.record_decision
                                   TX#9: DECISION (append_event, fenced)
                  TX#10 STEP_SUCCEEDED
        notify:   TX#11 ATTEMPT_STARTED
                  NotifyTool.run → ctx.run_effect("email", payload, sink.send)
                    get_effect(key) → None
                    claim_effect   TX#12: INSERT effects CLAIMED; EFFECT_CLAIMED
                    sink.send(key, payload)   → writes notifications.jsonl, fsync   (no tx open)
                    complete_effect TX#13: UPDATE effects DONE; EFFECT_DONE
                  TX#14 STEP_SUCCEEDED output={"email": {...}}
        _finish                    TX#15: RUNNING→SUCCEEDED; WORKFLOW_STATE
      store.release                TX#16: UPDATE owner=NULL; OWNER_RELEASED
  → print_status; exit 0
```

Sixteen transactions, sixteen events, each transaction fsynced (`synchronous=FULL`). Memorize the
shape: *claim, start, [attempt, work, record]×4, finish, release*.

### 3.2 Event sequence of a clean run (seq, step, type)

```
 1 workflow WORKFLOW_CREATED      9 classify DECISION
 2 workflow OWNER_CLAIMED        10 classify STEP_SUCCEEDED
 3 workflow WORKFLOW_STATE P→R   11 notify   ATTEMPT_STARTED
 4 ingest   ATTEMPT_STARTED      12 notify   EFFECT_CLAIMED
 5 ingest   STEP_SUCCEEDED       13 notify   EFFECT_DONE
 6 extract  ATTEMPT_STARTED      14 notify   STEP_SUCCEEDED
 7 extract  STEP_SUCCEEDED       15 workflow WORKFLOW_STATE R→S
 8 classify ATTEMPT_STARTED      16 workflow OWNER_RELEASED
```

With a retry on extract (`--fail extract:2`) insert after seq 6: `ATTEMPT_FAILED` + `RETRY_SCHEDULED`
(same tx), `ATTEMPT_STARTED 2`, `ATTEMPT_FAILED` + `RETRY_SCHEDULED`, `ATTEMPT_STARTED 3`, then
`STEP_SUCCEEDED` on attempt 3: 22 events. With the crash-in-notify + resume: after `EFFECT_CLAIMED`
the process dies; the resume adds `OWNER_CLAIMED (take_over)`, `ATTEMPT_INTERRUPTED 1`,
`ATTEMPT_STARTED 2`, `EFFECT_AMBIGUOUS`, then `EFFECT_DONE`, `STEP_SUCCEEDED`, `WORKFLOW_STATE`,
`OWNER_RELEASED`: 20 events, which is exactly what the demo prints.

### 3.3 The 16 event types and what the reducer does with each

| Type | Written by | Reducer effect |
|---|---|---|
| `WORKFLOW_CREATED` | `create_workflow` | new workflow `PENDING`, steps `PENDING attempt 0` |
| `WORKFLOW_STATE{from,to,reason}` | `transition_workflow` | `_move` wf |
| `CANCEL_REQUESTED{by}` | `request_cancel` | `cancel_requested = True` |
| `OWNER_CLAIMED{owner,take_over}` | `claim` | `owner = x` |
| `OWNER_RELEASED{owner}` | `release` | `owner = None` |
| `ATTEMPT_STARTED{attempt}` | `_run_step` | P→R, `attempt`, `next_run_at=None` |
| `ATTEMPT_FAILED{attempt,error,retryable}` | `_run_step` | none (explanatory) |
| `RETRY_SCHEDULED{attempt,next_run_at,delay_s,error,reason}` | `_run_step` | R→P, `next_run_at`, `error` |
| `ATTEMPT_INTERRUPTED{attempt}` | `_run_owned` resume | R→P |
| `STEP_SUCCEEDED{attempt,output}` | `_run_step` | R→S, `output`, `error=None` |
| `STEP_FAILED{attempt,error,reason}` | `_give_up` | (R or P)→F, `error` |
| `STEP_SKIPPED{attempt,error,reason,policy}` | `_give_up` | (R or P)→SK, `error` |
| `EFFECT_CLAIMED{key,effect_name,attempt}` | `claim_effect` | effect `CLAIMED` |
| `EFFECT_DONE{key,result}` | `complete_effect` | effect `DONE`, result |
| `EFFECT_AMBIGUOUS{key,claimed_by_attempt,tool_idempotent,action}` | `_run_effect` | none |
| `DECISION{key,data}` | `ctx.record_decision` | none |

---

## 4. Every crash point and why it is safe

Rule that makes all of them safe: **the engine holds nothing in memory that is not also on disk
before the next action**, and every transition is fenced on `(owner, current state)`. So a new
process can always read the rows and continue, and a zombie of the old process can never commit.

### 4.1 The notify step (DESIGN §5.2, C0–C7)

| Crash | On disk | Resume does | Duplicate? |
|---|---|---|---|
| C0 before run | wf `PENDING`, steps `PENDING` | runs normally | no |
| C1 after classify `SUCCEEDED`, before notify `ATTEMPT_STARTED` | notify `PENDING` 0/5 | notify attempt 1 | no |
| C2 after `ATTEMPT_STARTED`, before `claim_effect` | notify `RUNNING`, no effect row | `ATTEMPT_INTERRUPTED` → `PENDING`; attempt 2 claims and sends | no (nothing was sent) |
| C3 after `CLAIMED`, during `fn()` | notify `RUNNING`, effect `CLAIMED` | `ATTEMPT_INTERRUPTED`; attempt 2 finds `CLAIMED` → `EFFECT_AMBIGUOUS`; idempotent → resend same key, receiver dedupes; else step `FAILED ambiguous_effect` (with `skip` → `SKIPPED`, workflow still `SUCCEEDED`) | at most one *effect*; possibly two *calls* |
| C4 after `fn()` returned, before `DONE` | identical to C3 | identical to C3 | identical |
| C5 after `DONE`, before `STEP_SUCCEEDED` | notify `RUNNING`, effect `DONE` | attempt 2 → `run_effect` returns the stored result, `fn` never called; `STEP_SUCCEEDED` on attempt 2 | no |
| C6 after `STEP_SUCCEEDED`, before wf `SUCCEEDED` | notify `SUCCEEDED`, wf `RUNNING` | loop skips all steps → `_finish(SUCCEEDED)` | no |
| C7 after wf `SUCCEEDED`, before `release` | wf terminal, owner set | `run` reports; `run --take-over` releases | no |

C3 and C4 are indistinguishable on disk. That is **why** the ledger records `CLAIMED` before the
call: it converts "I don't know" into a durable fact and delegates the decision to the tool's
declared idempotency instead of guessing.

### 4.2 Other crash points

- **During a backoff sleep**: `RETRY_SCHEDULED` already committed `next_run_at`; the step is
  `PENDING`; resume waits only the remainder (`test_restart_during_backoff_waits_the_remainder`:
  50 ms backoff, 20 ms elapsed, sleeps 30 ms).
- **Between `STEP_FAILED` and wf `FAILED`**: step `FAILED` at entry → `_finish(FAILED)`.
- **Interrupted on the last attempt**: step comes back `PENDING` with `attempt == max`; the
  `attempt >= max_attempts` branch applies the failure policy from `PENDING` (`PENDING→FAILED|SKIPPED`
  are in `STEP_ALLOWED` for exactly this), no `ATTEMPT_FAILED` event.
- **During `claim`** (`BEGIN IMMEDIATE` … crash): rolled back by SQLite's journal; nothing claimed.
- **Between `claim` and `PENDING→RUNNING`**: wf `PENDING` with an owner; `run --take-over` reclaims
  and starts.
- **After `DECISION`, before `STEP_SUCCEEDED`** (classify): decision event is on disk, step
  `RUNNING`; resume → `ATTEMPT_INTERRUPTED`, attempt 2 re-runs classify (side-effect-free),
  writes a second `DECISION`. The history shows both; the second is the one that counts
  (same attempt as `STEP_SUCCEEDED`).
- **The zombie**: engine A stalls inside `sink.send` (GC pause, network hang), B takes over and
  finishes; A wakes up and calls `complete_effect(owner=A)` → `_fence` sees owner is `NULL` (B
  released) → `LostClaim`, rolled back, propagates out of `run` (`StoreError` is re-raised, not
  retried). B's `DONE` record stands. `test_fenced_out_engine_cannot_finish_the_effect`.
- **Cancel during a tool**: flag is set (unfenced), tool finishes, its result is committed, the
  next loop iteration sees the flag → `CANCELLED`. No effect is ever abandoned mid-flight.

### 4.3 Why SQLite settings matter

- `BEGIN IMMEDIATE`: acquires the write lock at `BEGIN`, so the fence SELECT and the UPDATE are
  one atomic unit against any other writer (deferred `BEGIN` would let two engines both read
  `owner IS NULL`).
- `synchronous=FULL`: fsync at every commit; a power loss after `COMMIT` returned cannot lose it.
- `WAL`: readers (`status`, `history`) never block the writer and vice versa.
- `isolation_level=None`: Python's sqlite3 otherwise issues implicit `BEGIN` before DML and never
  before SELECT, which would silently make the fence read non-transactional.
- `foreign_keys=ON`: steps/events/effects cannot reference a missing workflow.
- `PRAGMA user_version = 1`: the schema version stamp, read before any DDL runs; a file from a newer
  kernel is refused (`StoreError`, exit 4) and left untouched rather than misread.

---

## 5. The SQL schema, column by column

```sql
workflows(id PK, spec_name, spec_version INT, input_json, state,
          cancel_requested INT DEFAULT 0, owner NULL, created_at, updated_at)
steps(workflow_id FK, name, idx INT, state, attempt INT DEFAULT 0, max_attempts INT,
      output_json NULL, error NULL, next_run_at NULL, updated_at, PK(workflow_id, name))
events(seq INTEGER PK AUTOINCREMENT, workflow_id FK, step_name NULL, type, attempt NULL,
       payload_json DEFAULT '{}', created_at)         INDEX (workflow_id, seq)
effects(key PK, workflow_id FK, step_name, effect_name, attempt INT, status, result_json NULL,
        created_at, updated_at)
```

- `workflows.owner`: the claim token; `NULL` = free. `cancel_requested`: cooperative flag.
  `spec_name + spec_version`: the pin (versioning).
- `steps.idx`: spec order for `status` output; `attempt`: attempts started; `max_attempts` copied
  from the spec at create time so `status` needs no spec; `output_json`: set on `SUCCEEDED`, read by
  later steps and by resume; `error`: last error, cleared on success; `next_run_at`: backoff
  deadline, `NULL` = runnable now.
- `events.seq`: global monotonic order; `attempt` nullable (workflow-level events have none);
  `payload_json` canonical JSON.
- `effects.key`: the sha256; `attempt`: the claiming attempt; `status`: `CLAIMED | DONE`;
  `result_json`: what the receiver returned.

Timestamps are ISO-8601 UTC strings with microseconds (sortable, human-readable, no timezone
bugs); times come from the injected clock.

---

## 6. The tests, one line each (`tests/`)

- `conftest.py`: `INPUT` fixture doc; `FakeClock` (callable `()` → t; `sleep(s)` records and
  advances); `make_engine(reg=None, crash_after=None, **kw)` opens a **fresh Store on the same file**
  = a new process on the same disk, with `CrashAfterCommit` when asked; `registry(sink,
  **overrides)`.
- `test_retry.py::test_retry_then_success`: delays `[0.05, 0.1]`, `slept` matches, `next_run_at`
  persisted then cleared, ingest attempt stays 1.
- `test_failure_policy.py`: `test_exhaustion_fails_workflow` (classify 2/2, reason `exhausted`,
  notify never started, claim released); `test_skip_policy_lets_workflow_succeed` (notify 5/5
  `SKIPPED`, policy `skip`).
- `test_idempotency.py`: `test_notify_effect_once_across_crash` (C4; `CLAIMED` + `RUNNING` + one
  line + stale owner after the crash; resume: one `ATTEMPT_INTERRUPTED`, one `EFFECT_AMBIGUOUS
  resend`, calls 1+1, lines 1, `DONE` result `duplicate`, attempt 2);
  `test_ambiguous_non_idempotent_fails_safely` (strict spec with `fail_workflow` on notify; resume
  → `FAILED ambiguous_effect`, legacy sink never called, row left `CLAIMED`);
  `test_done_effect_is_not_resent_on_resume` (C5 staged by hand under the dead owner; resume →
  sink calls 0, output is the stored result).
- `test_restart.py`: `test_restart_resumes_without_rerunning` (crash after classify; counts
  `{1,1,1,0}` then `{0,0,0,1}`; no `ATTEMPT_INTERRUPTED` because notify had not started);
  `test_claim_blocks_second_owner`; `test_restart_during_backoff_waits_the_remainder`;
  `test_backoff_wait_is_clamped_when_the_clock_steps_backwards` (clock back 1 h → sleeps 2.0 s);
  `test_fenced_out_engine_cannot_finish_the_effect` (the zombie race, in-process: B runs inside
  A's `send`).
- `test_replay.py`: `test_replay_rebuild_equals_tables` (consistent even mid-crash; CLI exit 0;
  narrative includes `DECISION` and `EFFECT_AMBIGUOUS`); `test_replay_detects_tampered_table`
  (`UPDATE steps SET attempt=7` → one diff line, exit 1); `test_replay_rejects_illegal_log`
  (a `STEP_SUCCEEDED` appended on a `PENDING` step → `IllegalTransition` in the reducer).
- `test_transitions.py`: illegal transition writes nothing; stale owner cannot transition;
  `CancelsThenFails` tool (cancels from inside the tool, then raises) → `CANCELLED`, classify
  `PENDING`, notify attempt 0, terminal refuses cancel, `run` on terminal is a no-op;
  cancel before first run; `FatalError` on blank text fails extract at 1/3 with later steps
  `PENDING`; stale owner cannot claim/complete an effect or append an event; `start` rejects a
  list input, a colliding key, a duplicate id; `run` on a terminal workflow adds no events;
  a cancel injected into the backoff sleep → `CANCELLED`, extract 1/3 `PENDING`, classify 0;
  a tool returning `{"when": object()}` → `FAILED` on attempt 1, reason `fatal`, error names JSON.
- `test_cli.py`: exit codes 4/5 for every operator error; a JSON spec round-trips through
  `start --no-run` and `run --spec`; `PRAGMA user_version = 99` on the file → `status` exits 4 and the file still has no tables of ours; `history missing` exits 4.

---

## 7. Extending it live (the likely tasks)

Approach for any live change: (1) say which of the three roles it touches (Engine / Store / Tool);
(2) say which state-machine edges and event types change (usually none); (3) say what the reducer
needs; (4) write the test first in one sentence; (5) code.

### 7.1 Add a step (e.g. `redact` between extract and classify)

Touch: `spec.py` (new **version**, never edit V1), `tools.py` (new tool), registry in `cli.py` and
`tests/conftest.py`. No store or engine change.

```python
# tools.py
class RedactTool:
    name, side_effect_free, idempotent = "redact", True, True
    def run(self, ctx, inputs):
        body = inputs["extract"]["body"]
        redacted = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[email]", body)
        ctx.record_decision("redaction", {"emails_removed": body.count("@") - redacted.count("@")})
        return {"body": redacted}

# spec.py
TRIAGE_V2 = WorkflowSpec("document_triage", 2, (
    StepSpec("ingest", "ingest"), StepSpec("extract", "extract"),
    StepSpec("redact", "redact", max_attempts=2),
    StepSpec("classify", "classify", max_attempts=2),
    StepSpec("notify", "notify", max_attempts=5, on_failure="skip")))
SPECS[TRIAGE_V2.key] = TRIAGE_V2
```

Then `ClassifyTool` in v2 should read `inputs["redact"]["body"]`: either a `ClassifyV2` or make
classify read `inputs.get("redact", inputs["extract"])["body"]` (additive, v1-safe). Say out loud:
"running v1 workflows are unaffected because they resolve `document_triage@1` from the registry".

### 7.2 Cancellation: today vs. preemptive

Today: cooperative between attempts (`_run_step` top-of-loop check), immediate for `PENDING`
unowned, flag otherwise, terminal refuses. To make a long tool stop early, add a third kernel
service and one exception type:

```python
# tools.py
class Cancelled(Exception): ...
# engine.py, StepContext
def check_cancel(self):
    if self._engine.store.cancel_requested(self.workflow_id):
        raise Cancelled("cancel requested")
# engine.py, _run_step, before `except FatalError`
except Cancelled:
    self.store.transition_step(wf_id, step.name, "RUNNING", "PENDING", owner,
        [("ATTEMPT_CANCELLED", {"attempt": attempt})], attempt_no=attempt)
    return "CANCELLED"
# reducer: elif t == "ATTEMPT_CANCELLED": _move(st, "RUNNING", "PENDING", STEP_ALLOWED, where)
```

No new state-machine edge (`RUNNING→PENDING` exists), one new event type, one reducer line, and a
tool opts in by calling `ctx.check_cancel()` inside its loop. Never cancel inside `run_effect`
between `CLAIMED` and `DONE`: that would manufacture an ambiguity on purpose.

### 7.3 Parallel steps / fan-out

Two different asks; name which one they mean.

**Parallel steps (DAG)**: add `depends_on: Tuple[str, ...] = ()` to `StepSpec`; `_run_owned`
replaces "for step in spec order" with a loop: `ready = [s for s in spec.steps if row(s).state ==
PENDING and all(row(d).state in (SUCCEEDED, SKIPPED) for d in s.depends_on)]`; run `ready` in a
`ThreadPoolExecutor`, each worker with **its own `Store`** (sqlite connections are per thread) and
the same `owner`; join; repeat until nothing is ready; a `FAILED` with `fail_workflow` finishes.
Transactions already serialize through `BEGIN IMMEDIATE`, so the fences hold unchanged. Resume
changes from "at most one `RUNNING` step" to "every `RUNNING` step gets `ATTEMPT_INTERRUPTED`"
(the code already loops over all steps, so nothing changes there). The spec validator must reject
cycles (DFS) and unknown dependencies.

**Fan-out inside a step (map over N items)**: keep the DAG out; the tool does
`ctx.run_effect(f"email:{i}", payload_i, send)` per item, so each item has its own key and ledger
row and a crash mid-loop resumes with the done ones short-circuited and the rest sent. If the
items must run concurrently, the tool uses threads around `run_effect`; the ledger writes are
fenced and serialized. Say: "the effect name is the fan-out index; the key already namespaces by
workflow and step".

### 7.4 Timeouts

Add `timeout_s: Optional[float] = None` to `StepSpec`. Python cannot kill a thread, so the engine
runs the tool in a worker thread and waits with a deadline; on timeout it **closes the context** so
any late write from the orphaned thread is refused, then treats it as retryable:

```python
# StepContext: self.closed = False; both services start with
#   if self.closed: raise StoreError("context closed (attempt timed out)")
# _run_step:
with ThreadPoolExecutor(1) as pool:
    fut = pool.submit(tool.run, ctx, self._inputs(wf_id))
    try:
        output = fut.result(timeout=step.timeout_s)
    except FuturesTimeout:
        ctx.closed = True
        raise RetryableError(f"timed out after {step.timeout_s}s")   # falls into the retry branch
```

Caveat to say unprompted: a timeout during `run_effect` after `CLAIMED` is just crash point C3;
the ledger handles it the same way on the next attempt. In the multi-service design timeouts are
enforced by the tool service (RPC deadline), which is cleaner.

### 7.5 A new tool with a side effect (e.g. `archive` to object storage)

```python
class ArchiveTool:
    name, side_effect_free, idempotent = "archive", False, True   # S3 PUT to a fixed key is idempotent
    def __init__(self, bucket): self.bucket = bucket
    def run(self, ctx, inputs):
        payload = {"bucket": self.bucket, "object": f"{inputs['ingest']['doc_id']}.txt",
                   "body": inputs["document"]["text"]}
        return {"put": ctx.run_effect("put", payload, self.bucket.put)}   # bucket.put(key, payload)
```

Checklist: declare `idempotent` from the receiver's real behaviour; use `run_effect` for every
external call; raise `FatalError` for input problems and `RetryableError` for transport problems;
register under a name; add the step to a new spec version.

### 7.6 Change the retry policy

All in `_run_step`'s retry branch plus `StepSpec` fields; the reducer reads `next_run_at` from the
event so it needs no change.

- Jitter: `delay = min(base * 2**(attempt-1), cap) * random.uniform(0.5, 1.5)`; persisted in
  `RETRY_SCHEDULED.delay_s`, so replay stays exact.
- Per-step cap: `backoff_cap_s` on `StepSpec`, replace `Engine.BACKOFF_CAP_S`.
- Retry only specific errors: `retry_on: Tuple[type, ...] = (RetryableError,)` and in the generic
  `except Exception` branch `retryable = attempt < max and isinstance(e, step.retry_on)`; unexpected
  exceptions then fail fast (D4 reversed; say it is a policy choice).
- Retry budget per workflow: a `retries_left` column on `workflows`, decremented in the same
  transaction as `RETRY_SCHEDULED`.

### 7.7 Operator verb for ambiguous effects (`effects resolve`)

```python
def resolve_effect(self, key, outcome, by):           # Store
    with self._tx():
        row = self.get_effect(key); self._fence(row.workflow_id, None)   # only when no engine owns it
        if outcome == "done": UPDATE status='DONE', result_json='{"status":"resolved"}'
        else:                 DELETE FROM effects WHERE key=?   # 'abandon': next attempt re-claims
        self._append(row.workflow_id, row.step_name, None, [("EFFECT_RESOLVED", {"key": key, "outcome": outcome, "by": by})])
```

Then `run <id>` continues: `DONE` → short-circuit; absent → fresh claim and send. Reducer: handle
`EFFECT_RESOLVED` (set status or delete the key).

### 7.8 `status --json`, `list`, `run --follow`

Trivial CLI additions: `json.dumps(wf.__dict__)` + steps + effects; `list_workflows` already
exists; `--follow` polls `load_workflow` every 0.5 s until terminal and prints new events since the
last seen `seq`.

### 7.9 Debugging a failing test (the method, in order)

1. `.venv/bin/python -m pytest -x -vv -k <name>`; read the assertion line, not the traceback top.
2. Print the narrative: `for e in store.events(wf_id): print(e.seq, e.step_name, e.type, e.payload)`
   (or `python -m wfk --db <tmp> replay <id>` if the db path is known).
3. Match the sequence against §3.2; the first event that differs from the expectation is the bug.
4. Check the three usual suspects: a transition pair missing from `STEP_ALLOWED`; a `transition_step`
   called with the wrong `frm` (the `WHERE state=?` fence makes it `LostClaim`, which looks like a
   claim bug but is a state bug); a reducer branch not updated for a new or changed event.
5. If the fake clock is involved: `clock.slept` tells you what the engine waited.

---

## 8. Fifteen likely questions, with model answers

1. **"Walk me through what happens when the process dies right after the email provider accepts
   the message."** → C4 story: `CLAIMED` on disk, step `RUNNING`, stale owner. Resume:
   `ATTEMPT_INTERRUPTED`, attempt 2, ledger says `CLAIMED`-not-`DONE` → `EFFECT_AMBIGUOUS`. Tool
   idempotent → resend with the same key, provider dedupes, `DONE`, `SUCCEEDED`. Not idempotent →
   `FAILED ambiguous_effect`, row left for an operator; with `skip` the workflow still completes.
   "The kernel never guesses."

2. **"So is it exactly-once or not?"** → "At-least-once invocation, exactly-once recording,
   exactly-once effect iff the receiver honours the key. No single-process system can do better,
   and the ones that claim to are hiding the window in a dispatcher. I made the window a recorded
   fact."

3. **"Why a claim without a TTL? That's a liveness hole."** → "Deliberately. A TTL lease without a
   fencing token at commit time lets a slow tool and the new owner both commit attempt N. In this
   single-process scope the fence is `owner = ?` inside every transaction, and recovery is an
   explicit `--take-over`. In the multi-worker design the claim becomes
   `(owner, lease_until, lease_token)` with heartbeats and `WHERE lease_token = ?` on every commit.
   Liveness is restored by the token, not by the TTL alone."

4. **"Why not event-source everything and drop the tables?"** → "`status` would be a fold on every
   read, and every bug in the fold is a production incident. I keep tables as the projection
   written in the same transaction, and I test that the fold reproduces them. The log is the
   truth; the tables are a cache that is proven consistent."

5. **"Why does replay not re-execute?"** → "Because re-execution with the same keys is just `run`,
   and re-execution with new keys is a new workflow (`start --derived-from`). A read-only replay is
   the only definition that cannot violate 'no duplicate side effects', and it doubles as the
   tamper check."

6. **"What if two CLI invocations run the same workflow at once?"** → "The claim is a conditional
   `UPDATE` under `BEGIN IMMEDIATE`: exactly one wins; the other gets `AlreadyOwned`, exit 3. If the
   winner stalls and an operator takes over, the stalled one's next write hits `_fence` and rolls
   back (`test_fenced_out_engine_cannot_finish_the_effect`)."

7. **"Why are unexpected exceptions retried instead of failing fast?"** → "Under 'correctness under
   failure' the default that loses less work is retry; a tool that knows better raises `FatalError`.
   Cost: a real bug burns N quick attempts with capped backoff. I'd reverse it with a `retry_on`
   list in production if the tool population was mature."

8. **"Where is the cancel checked, and why not mid-tool?"** → "Between attempts, at the top of the
   attempt loop. Preempting a tool mid-effect would manufacture an ambiguity on purpose. For long
   tools I'd add `ctx.check_cancel()` as a cooperative service (§7.2)."

9. **"How would you scale to 10k concurrent workflows?"** → "State ownership by shard
   (`hash(workflow_id)`), Postgres with partitioned events, stateless workers pulling 'run step X of
   W' tasks from a queue; the claim becomes a lease with a fencing token; the queue is an
   accelerator, tasks are re-derivable from `steps` (`PENDING`, `next_run_at <= now`, unowned).
   10k workflows is a few hundred transitions per second: one Postgres is comfortable."

10. **"How do you version without breaking in-flight workflows?"** → "Pin `spec_version` at start;
    the registry holds every published version; never edit a published version; tool output changes
    are additive, reshapes are a new tool version with a reader adapter; migration is an explicit
    logged event. The engine is a function of (database, registry), so deploying a registry that
    still contains the old versions changes nothing in flight."

11. **"What's in the effect key and why no attempt number?"** → "`sha256(workflow:step:effect_name)`.
    No attempt number so every attempt across retries and restarts presents the same key to the
    receiver. With a tenant it becomes `tenant:workflow:step:name` so client-supplied ids cannot
    collide across tenants."

12. **"How does a tool stay ignorant of the store?"** → "It gets a `StepContext` with identity and
    two services. Both services carry the engine's owner token, so a tool cannot write unfenced
    even by accident. `tools.py` imports `StepContext` only under `TYPE_CHECKING`."

13. **"What would an auditor get?"** → "An append-only log with a closed set of 16 event types;
    `history --kind DECISION` answers 'why did this document get this label'; `EFFECT_*` answers
    'when was the notification claimed and completed, and by which attempt'; `replay` proves the
    tables match the log. In Postgres a trigger forbids UPDATE/DELETE on events; hash chaining if a
    regulator needs tamper evidence."

14. **"What did you cut, and what would you build first in production?"** → Cut list (README).
    First: the lease + fencing token and a reconciler for stuck `CLAIMED` rows, because those are
    the two things that turn this from one process into a fleet. Second: `effects resolve` and
    metrics (attempts, ambiguities, time-in-state).

15. **"What's the weakest part of this design?"** → "Two things I'd say before you ask: the
    `idempotent` flag is declared by the tool and not verified against the receiver, so a wrong
    declaration turns at-most-once into at-least-once silently; and `side_effect_free` is
    documentation, not enforcement, a tool can call `requests.post` outside `run_effect`. Both are
    fixed in the service design by moving tool execution behind an RPC boundary that only exposes
    `Idempotency-Key`."

---

## 9. Things I simplified and would do in production

- **Lease TTL + fencing token** (`owner, lease_until, lease_token`, heartbeat, `WHERE lease_token=?`).
- **Reconciler** that lists `CLAIMED` rows older than N minutes and `RUNNING` steps with an expired
  lease; `effects resolve` verb.
- **Spec body persisted** with the workflow (or a `specs` table) instead of a code registry, so a
  future process can run a file spec without the same `--spec` flag.
- **DAG specs** with `depends_on`, a cycle check, and a ready-set scheduler.
- **Timeouts** per step; **cooperative cancel** inside tools; **preemptive** cancel only via the
  tool service.
- **Retry policy per error class**, jitter, backoff on an interrupted attempt (needed once resume is
  automatic), workflow-level retry budget.
- **Payload hash in the effects ledger** so a resend whose payload differs is refused (the receiver
  keeps the first body for a key).
- **Outbox-style delivery** only if a receiver has no idempotency key at all (then the outbox relay
  owns the key).
- **Tenant column** + predicate wrapper + per-tenant rate limits; key prefix by tenant.
- **PII**: redaction hook before `append_event`, payload tombstoning with retention, `prev_hash`
  chain.
- **Observability**: counters for attempts/retries/ambiguities, histogram of time in each state,
  `run --follow`.
- **Postgres**: same schema; `events` partitioned by month; `UPDATE/DELETE` trigger on events;
  `SELECT … FOR UPDATE SKIP LOCKED` as the queue.
- **Schema versioning** of tool outputs (`schema_version` in `output_json`) with reader adapters.
- **Secrets/encryption at rest** for payloads that carry document text.

---

## 10. Say-it-cold checklist (test yourself without looking)

- [ ] Draw the two state machines with all 12 edges.
- [ ] List the 16 event types and which transaction writes each.
- [ ] Narrate a clean run as 16 events.
- [ ] Narrate C4 and C5 and say why they differ on disk.
- [ ] Write `_run_effect` from memory (8 lines of logic).
- [ ] Write `_tx` and `_fence` from memory and explain `BEGIN IMMEDIATE`.
- [ ] Explain the two fences on `transition_step` (`owner`, `WHERE state = ?`).
- [ ] State the guarantee sentence verbatim.
- [ ] Name the three alternatives for idempotency (outbox+dispatcher, dedupe sink, kernel ledger)
      and why the ledger.
- [ ] Give the scaling story in 60 seconds: shard, Postgres, queue, lease+token, reconciler.
- [ ] Add a step, add a timeout, add cooperative cancel, each in under 5 minutes.

## 11. Oct 31 evening pass: five things that changed (know them cold)

Commits `9883d73..080c8f0`; tests 27 → 31; full reasoning in `TR_AUDIT.md`, talking points in
`CODE_WALKTHROUGH_FOR_GANESH.md` §10.

1. **`cancel` racing `start`** (`engine.py:145-149`): the re-read under the claim returns a terminal
   state instead of letting `_finish` raise `LostClaim`. Test injects the cancel inside `claim`.
2. **Interrupted last attempt** (`test_interrupted_last_attempt_goes_to_the_failure_policy`): crash
   inside classify's attempt 2 of 2 → resume → `PENDING → FAILED`, reason `exhausted`, error
   "attempt 2 of 2 was interrupted; no attempts left (error before it: ...)", no `ATTEMPT_FAILED`
   for attempt 2, replay consistent. Sister test: crash right after `STEP_FAILED` → next process
   commits the workflow `FAILED`, no new attempt.
3. **`history <unknown id>`** → exit 4 (`cli.py:167`).
4. **Schema check before DDL** (`store.py:201-209`): refuse, close, leave the newer file untouched.
5. **`FileNotifier(idempotent=False)` delivers every call** (`tools.py:153`); `side_effect_free` is
   labelled as a declaration the kernel does not enforce (`tools.py:34-39`).
