# Interview prep: the Trase take-home discussion and the live round (NOT SUBMITTED)

Rounds this covers: (1) the follow-up discussion of the take-home (decisions, how AI was used, a
new requirement); (2) the live technical rounds: code, debug and explain **without AI**. Role:
Senior/Staff Software Engineer, Platform and Execution Model. Read `STUDY_GUIDE_FOR_GANESH.md`
first (every function, every number); `PRESENTATION.md` is the talk; this file is delivery,
pushback and drills. Never mention another process, company or comp in any of these rounds.

---

## 1. What "principal" sounds like in this room

1. **Decisions with alternatives and tradeoffs, never features.** Shape of every answer: *"I chose X
   over Y and Z because …; the cost is …; the failure it prevents is …"*.
2. **Failure semantics stated precisely.** Where the window is, what is on disk at each crash
   point, what the next process does, which test pins it. Vocabulary: fence, claim, ledger,
   ambiguity, at-least-once invocation, exactly-once recording.
3. **Evolution for every mechanism.** claim → lease + fencing token; SQLite → sharded Postgres;
   in-process loop → queue + stateless workers; registry → service catalogue; `--take-over` →
   reconciler; `idempotent` declared → enforced at the RPC boundary.
4. **Operability.** Exit codes, `status` as one SELECT, `history --kind DECISION`, `replay` as tamper
   check, the ambiguity rate as the SLO.

Own history, once, when sizing comes up: the Cisco data platform (50+ PB per year of telemetry
from 15M+ devices for 100+ teams) and the backend runtime for AI agent workflows at Salesforce.
Numbers, not adjectives; petabytes per year, never a larger unit.

## 2. How to run the conversation

- Open with the 2-minute statement (PRESENTATION §0), then: *"Where would you like to go deeper:
  the failure semantics, the scaling story, or the code?"*
- Three beats per answer: decision → why → tradeoff. Then stop.
- Whiteboard the step state machine and the C4 timeline before any code. When asked for code, go
  to the function that embodies the decision: `Store._tx` + `_fence`, `Engine._run_effect`,
  `Engine._run_step`'s exception ladder, `rebuild_state`.
- Name the test right after the claim.
- Volunteer the weakest points (PRESENTATION §11) and where the fleet design fixes them.
- When they propose an alternative: engage on the merits for two sentences, then name the
  constraint that decided it. Changing your mind on a genuine point reads as senior.
- **On "how did you use AI"** (they will ask; it was allowed): the design record and the crash-point
  table were the thinking; AI tooling drafted and reviewed code against that record; the
  self-review pass found the unfenced ledger write; every decision in the README is one you can
  defend line by line. Keep it to three sentences and move to the decisions. Never over-explain,
  never disclaim.

## 3. Pitfalls

- Monologues past 60 s. Disclaimers ("hacky", "ran out of time"). Over-claiming "exactly-once"
  without the qualifier. Code tours. Guessing numbers (you know them: see PRESENTATION header).
- "The brief said four states, you have five/six": *"SKIPPED is the honest state for a step that
  never ran under a skip policy; CANCELLED because cancel was asked for. Happy to rename, not to
  merge."*
- Rushing the demo: point at `CLAIMED` before resume and `EFFECT_AMBIGUOUS -> resend` after.

---

## 4. Likely pushback, with principal-grade answers

| Pushback | Answer |
|---|---|
| **"Is it exactly-once or not?"** | "At-least-once invocation, exactly-once recording, exactly-once effect iff the receiver honours the key. No single-process design does better; the ones that claim to have hidden the window in a dispatcher. I made the window a recorded fact, `EFFECT_AMBIGUOUS`, with the decision policy on it." |
| **"A claim without TTL is a liveness hole."** | "Deliberately. A TTL you can steal without a fencing token at commit lets a slow tool and the new owner both commit attempt N; that is a correctness hole, which is worse. Here the fence is `owner = ?` inside every transaction and recovery is an explicit `--take-over`. The fleet version restores liveness with `(owner, lease_until, lease_token)` and `WHERE lease_token = ?` on every commit; the token is what makes the TTL safe." |
| **"Your non-idempotent path fails on a plain connection-refused. That's too conservative."** | "Yes, on purpose: from disk the kernel cannot tell 'never sent' from 'sent, reply lost', so it takes the at-most-once stance and leaves the row for an operator. The first follow-up is a typed `NotSent` signal from the receiver, raised before any bytes leave, which the kernel would treat as retryable without ambiguity. I did not build it because it needs the receiver's cooperation and I wanted the default to be the safe one." |
| **"Three commits per effectful step is expensive."** | "Three fsyncs per notify, ~1-3 ms each on local SSD; notify is one step in four and the external call is tens of milliseconds. The alternative is an outbox with a relay, which is the same number of writes in a different process plus a liveness dependency. If the effect rate ever mattered, I would batch the CLAIMED writes per worker, not remove them." |
| **"Why not event-source everything?"** | "`status` would be a fold on every read and every bug in the fold is an incident. Tables are the projection, written in the same transaction, and the fold is tested against them. I get the audit property of event sourcing and the read property of tables, for one extra INSERT." |
| **"What does `cancel` do if it races `start`?"** | "The row is PENDING and unowned, so `cancel` moves it to CANCELLED and `run`'s claim still succeeds on it. I re-read under the claim and return a terminal state as-is; `run` releases and nothing is written. Before that check, `_finish` tried RUNNING→CANCELLED and the operator saw a `LostClaim` for a workflow that had ended correctly: right on disk, wrong signal. The test injects the cancel inside `claim`, the way the crash tests inject crashes." |
| **"What did your own review find?"** | "Three kinds of thing. Paths outside the crash table: a cancel during a backoff wait ran one more attempt; the cancel/start race above. Honesty of the abstractions: `side_effect_free` is read by nobody, so it says so; the demo receiver with `idempotent=False` was still deduping, so the flag was a label, not behaviour. And order of operations: the schema-version check ran after my DDL, which is harmless with `IF NOT EXISTS` and wrong the day v2 renames a table. Each came with a test." |
| **"Why SQLite?"** | "The brief is single-process; SQLite with WAL and `synchronous=FULL` gives real durability and real transactions with no daemon, and the schema is plain SQL that moves to Postgres unchanged. `BEGIN IMMEDIATE` is the only SQLite-specific choice and Postgres `SELECT … FOR UPDATE` replaces it." |
| **"What if two workers pick the same workflow in your fleet design?"** | "One runnable task per workflow at a time, and the lease token on every commit. Even if the queue delivers twice, the second worker's claim fails or its commits are fenced. The queue is an accelerator; the `steps` table is the truth it is re-derived from." |
| **"Your replay doesn't re-execute. Isn't that just a consistency check?"** | "It is, and that is the only definition of replay that cannot violate 'no duplicate side effects'. Re-executing a failed run is a new workflow with its own keys; I named it `start --derived-from` and did not build it, so nobody confuses the two." |
| **"How do you migrate an in-flight workflow to spec v2?"** | "Explicitly: a `MIGRATED{from, to}` event, a mapping from v1 step outputs to v2 inputs, and a check that every SUCCEEDED step in v1 has a counterpart in v2 at the same position or earlier. Anything else refuses. Implicit migration is how you get a step re-run with the wrong inputs." |
| **"Why does an unexpected exception retry instead of failing fast?"** | "Under 'correctness under failure' the default that loses less work is retry: a transient loses nothing; a genuine bug costs N capped attempts of milliseconds. A tool that knows better raises `FatalError`. I would flip the default only with per-tool error classification at the RPC boundary." |
| **"What breaks at 10k concurrent?"** | "Nothing in the model; the single file and the single process. Shard by workflow id, Postgres with `events` partitioned by month, workers behind a queue. A few hundred transitions per second and ~50 events per workflow is comfortable for one Postgres; the first real cost is the events table's growth, which is retention, not capacity." |
| **"How would an auditor use this?"** | "`history <id>` is the complete record: every transition with from/to/reason, every retry with delay and cause, every effect claim/done/ambiguity, every `DECISION` with the rule that fired. `replay` proves the record matches the state. Add a `prev_hash` and it is tamper-evident; add redaction before `append_event` and it is PII-safe." |
| **"What's the weakest part?"** | "`idempotent` is declared by the tool, not verified: a wrong declaration turns at-most-once into at-least-once silently. In the fleet design the RPC boundary carries the key as `Idempotency-Key` and the receiver's behaviour is a contract test, not a flag." |
| **"How did you use AI?"** | See §2. Three sentences, then back to decisions. |
| **"What did you change after your first complete version?"** | "Two passes. The first found the ledger writes unfenced (a stalled engine could overwrite the record of the one that took over) and fixed it with one `_fence` in every workflow-scoped transaction plus a race test. The second read the kernel as an operator would run it for a year: a cancel during a backoff sleep ran one more attempt, a non-JSON tool output was retried as transient, a clock stepping backwards could stall a resume for hours, and the file had no schema version. Four fixes, four tests, and comments at the decisions those exposed: fence versus lease, key stability across versions, payload drift, the poison-pill bound." (Walkthrough §9 has each one.) |

---

## 5. Live-round extension drills (no AI; 10-minute timer each; say the 4-line plan first)

Plan shape, out loud, before typing: *which role it touches (engine / store / tool / spec) → which
events or edges change → what the reducer needs → the test in one sentence.* Keep the state
machine closed: new behaviour is new events, rarely new edges. Run `pytest -q` after each; revert
with `git checkout -- .` (local only).

1. **Add a step** (`redact` between extract and classify): new `Tool` class with
   `side_effect_free=True`; `TRIAGE_V2` with the step inserted; register under version 2; v1 stays.
   Test: a v1 workflow started before the deploy still runs v1 (`_spec_for` pins).
2. **Cooperative cancel inside a tool**: `ctx.check_cancel()` reads `store.cancel_requested`;
   raises `Cancelled(Exception)`; `_run_step` catches it before the generic `except`, writes
   `ATTEMPT_CANCELLED` and `RUNNING → PENDING`, returns `"CANCELLED"`; reducer line for the new
   event. Never call it inside `run_effect`. Test: tool calls `check_cancel` after `cancel()` was
   requested; step PENDING, workflow CANCELLED, no notification.
3. **Per-step timeout** (`timeout_s` on `StepSpec`): run the tool in a worker thread with a
   deadline; on expiry mark the `StepContext` closed so late `record_decision`/`run_effect` raise
   `StoreError`, and raise `RetryableError("timeout")`. Say the C3 caveat unprompted: a timeout
   inside an effect is an ambiguity on the next attempt, by design.
4. **Jittered backoff / per-step cap**: `backoff_cap_s` on `StepSpec`, `random.uniform(0.5, 1.0) *
   delay`; the persisted `delay_s` in `RETRY_SCHEDULED` keeps replay deterministic. Test: inject
   `random` via the engine like the clock.
5. **A second effectful tool** (`archive` to object storage): declare `idempotent` from the
   receiver's truth; every external call through `run_effect` with its own `effect_name`; typed
   errors; new spec version. Test: crash after the receiver accepted, resume, one object.
6. **Operator verb `effects resolve <key> --as done|failed`**: fenced store method that moves
   `CLAIMED → DONE{operator}` or `→ FAILED`, appends `EFFECT_RESOLVED{by}`; reducer line; then
   `run` continues. Test: non-idempotent ambiguity, resolve as done, workflow SUCCEEDED, receiver
   never called.
7. **Parallel steps**: ask first: a DAG of steps or fan-out inside a step? DAG: `depends_on` on
   `StepSpec`, one thread per ready step, one `Store` per thread; the owner fence is unchanged;
   resume already loops over every RUNNING step. Say why fan-out inside a step is simpler for the
   brief's workflow.
8. **`status --json` / `list`**: serialize `WorkflowRow`/`StepRow`/`EffectRow` with `dataclasses.asdict`;
   add `--state` filter to `list_workflows`. Five minutes; a warm-up.
9. **Debug a failing test**: `pytest -x -vv -k name`, print the event sequence, compare with the
   16-event clean-run shape, check the three suspects: an `ALLOWED` pair, a wrong `frm`, a missing
   reducer branch.

Order for the evening before: 1, 2, 5, 6 (the ones most likely asked), then 3, 4, 7.

---

## 6. Day-of checklist

- Repo open at `wfk/engine.py`; `scripts/demo.sh` output captured in a terminal; `pytest` green;
  no AI tools or assistant tabs open anywhere on screen.
- Whiteboard or paper for the state machine and the C4 timeline.
- Opening statement read aloud once, 10 minutes before. First sentence: "I read the brief as a
  correctness problem …". Last sentence: "Where would you like to go deeper?"
