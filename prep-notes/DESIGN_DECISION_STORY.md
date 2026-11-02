# Trase take-home: how the design was decided (and how to tell the story)

Private prep document for Ganesh. Not part of the submission. Written Oct 31 2026 from the verbatim design debate (`DESIGN_DEBATE.md`), the workflow journal (every agent's raw output), the final `docs/DESIGN.md` and `README.md` in `~/claude-code-ws/trase-project`, and `REVIEW_RESPONSE.md`.

**The problem in two lines.** Build a small, production-minded engine that runs a four-step document-triage workflow (ingest, extract, classify, notify) with an explicit state machine, retries, step-level failure handling, durable state that resumes safely after a crash, no duplicate notify, a queryable history of what was decided and why, a Tool interface and spec, a CLI (start/status/cancel/replay), tests, and a README with design sections A-D (scaling, failure semantics, versioning, multi-tenancy). "Clarity and correctness over features", 3-5 hours, AI tools allowed.

**Provenance note.** In `DESIGN_DEBATE.md` the appendix headings are mislabeled: the block titled "Proposal 2 (simplicity-first)" actually contains Architect #3's evolution-first text, and the block titled "Proposal 3 (evolution-first)" is a second copy of Proposal 1. The real simplicity-first proposal (Architect #2) exists only in the workflow journal; this document uses the journal text for it. The critic's report and verdict are identical in both sources.

---

## 1. The cast

| Role | Lens | Optimized for |
|---|---|---|
| **Architect 1** (correctness-first) | Enumerate every crash point and close it with a mechanism | Zero-ambiguity recovery: transactional outbox + dispatcher, TTL leases, six CLI verbs, fork-style replay |
| **Architect 2** (simplicity-first) | Smallest thing that is obviously right | Three tables, ~500 lines, "Engine decides, Store remembers, Tool does", read-only replay as a fold |
| **Architect 3** (evolution-first) | Pick the data model Temporal/Step Functions converge on, so scale-up is a storage swap | Event log as truth, effects ledger with CLAIMED/DONE, per-tool `idempotent` flag, `next_run_at` persisted |
| **Critic** | Adversarial grader: "what will the interviewer's first question expose?" | Finding vacuous proofs, half-built mechanisms, scope creep, and the one definition of replay that cannot violate the brief |
| **Chair** | Turn the verdict into a buildable decision record | `docs/DESIGN.md` D1-D15, crash-point table C0-C7, test plan, written before the code |
| **Reviewer** | Reproduce every README claim, race two engines, try every operator error | 22 findings (1 HIGH, 4 MEDIUM, rest LOW/readability); the HIGH one was a real correctness hole |
| **Fixer** | Apply the review with judgment, not obedience | 19 findings as written, 3 with a documented variation; 17 tests became 23 |

---

## 2. Each architect's position

| Dimension | A1 correctness-first | A2 simplicity-first | A3 evolution-first | **Built** |
|---|---|---|---|---|
| Tables | 4: workflows, steps, events, **outbox** | 3: workflows, steps, events (+ a `notifications` dedupe table as the sink) | 4: workflows, steps, events, **side_effects** (CLAIMED/DONE) | 4: workflows, steps, events, **effects** (CLAIMED/DONE) |
| Truth | Tables authoritative, events audit, same txn | Tables authoritative, events audit, same txn | **Log is truth, tables a cache**, reducer proves equality | Both: tables are what the engine reads, log is what replay/auditor read, one txn keeps them equal, reducer proves it |
| Step states | P→R→S\|F\|**SKIPPED**, R→P retry | P→R→S\|F, R→P retry | P→R→S\|F, R→P retry | P→R→S\|F\|SKIPPED, R→P; plus P→F\|SKIPPED (found in implementation) |
| Retry | `base*2^n` capped; Retryable vs non-retryable | Linear `0.1*attempt`, not persisted; unexpected = fatal | Exponential, **`next_run_at` persisted**; **unexpected = retryable**; `FatalError` | Exponential `min(0.05*2^(n-1), 2.0)` persisted; unexpected = retryable; `FatalError` |
| Step-level failure | `on_failure: fail_workflow \| skip` | none beyond retries | `on_failure: fail_workflow \| skip` | `on_failure: fail_workflow \| skip` |
| Notify idempotency | Tool **enqueues** outbox row (INSERT OR IGNORE); separate **Dispatcher** sends keyed; at-least-once at the relay | Key stored on the step; sink does INSERT OR IGNORE; "at-least-once + idempotent receiver = effectively-once" | **Kernel claims key (CLAIMED), calls effect, marks DONE**; CLAIMED-not-DONE on re-entry = `EFFECT_AMBIGUOUS`; resend only if tool `idempotent=True` | A3's ledger, with the critic's API fix: `ctx.run_effect(name, payload, fn)`; tools never see the DB |
| Key | `sha256(wf, step, spec_version)`, no attempt | `sha256(wf + step + spec_version)`, no attempt | `sha256(wf:step:effect_name)`, no attempt | `sha256(wf:step:effect_name)`, no attempt |
| Concurrency | **TTL lease** (30 s), expiry → `ATTEMPT_ABANDONED`, retry | `BEGIN IMMEDIATE` on begin_attempt "as the lease"; single process assumed | Lease columns designed, not exercised; fencing token only in section A | **Claim (owner column), no TTL, owner fence on every write**; `run --take-over` for a dead owner |
| Resume | RUNNING with stale lease → abandoned attempt | RUNNING → `ATTEMPT_INTERRUPTED` → PENDING, attempt counted | RUNNING with no terminal event → re-run as new attempt (`RESUMED_AFTER_CRASH`) | RUNNING → `ATTEMPT_INTERRUPTED` → PENDING, attempt counted; outputs persisted so done steps never re-run |
| Replay | **New workflow `ID~r1`, re-executes, re-notifies** (new key) | **Read-only fold** of events, CONSISTENT or diff | **Read-only rebuild**, exit 1 on mismatch; re-execution is a separate `start --from` | Read-only `rebuild_state()` + diff, exit 1; re-execution = future `start --derived-from` (prose) |
| CLI | start, status, history, cancel, replay, `resume --all` | start (also resume with `--id`), status, cancel, replay | start, status, history, cancel, replay, `run --follow` | start, run, status, cancel, history, replay |
| Spec | JSON file | Code dataclasses + `from_json` | Code dataclasses; YAML "a 20-line adapter" | Code dataclasses + JSON loader (≤ 20 lines) |
| Section A (scale) | Shard by wf id; workers from a queue, visibility timeout = lease TTL; outbox relay pool; `SKIP LOCKED` | Postgres + worker pool with leases; "outbox pattern already in the design" (it was not) | Shard by wf id; tasks with leases **+ fencing token checked at commit**; tools as RPC with the key; failure boundaries listed | A3's text |
| Section D | `tenant_id` + **hash-chained events** + encryption at rest (in schema) | `tenant_id` as prose | `tenant_id` in every table and key; append-only log is the audit; replay as tamper check | A3's text, prose only; nothing unexercised in the schema |

**A1 in four bullets.** Every state change is one transaction; crash points C1-C6 enumerated with recovery; the outbox decouples "step succeeded" from "provider is up"; replay is an operator action that forks a new execution and keeps history immutable.

**A2 in four bullets.** One sentence explains the whole system; `status` is one SELECT, never a fold; the kernel's whole job for notify is to hand a stable key to an idempotent receiver; replay as a read-only fold doubles as a self-audit, so one verb serves the history requirement and the CLI requirement.

**A3 in four bullets.** The data model is the one you would still have at 10k workflows (log + projection + keyed ledger); the kernel should know when it does not know (CLAIMED without DONE is a recorded fact, not a guess); at-least-once vs at-most-once is a per-tool declaration, auditable; replay means what it means in Temporal (re-derive state from history) and re-execution is a different verb.

---

## 3. The debate (paraphrased, faithful to the transcript)

**Round 1: the crash after the email leaves, before the commit.**
- Critic: "Who guarantees what?" A1: at-least-once at the edge, keyed; the outbox gives a clean step commit and an independent relay, "how every production system does it."
- Critic's attack: production systems have a relay because producer and sender are different services; here they are one loop in one process. The outbox added a table and a second retry policy, and the crash window (C5: `send()` done, SENT not committed) is byte-for-byte the same as A2's. A1's defense: decoupling, step success not hostage to the provider. A3's counter: the step *is* notify; if the provider is down the honest `status` is "notify, attempt 2 of 5, next at 10:04", not "notify SUCCEEDED, outbox PENDING"; the outbox makes `status` lie about the thing the brief cares most about.
- A2: cheapest version, the sink dedupes on the key, done. Critic: your sink is a table in the same SQLite file; if the test shares a connection there is no crash window and the test is theatre. A2 conceded: separate connection, own commit, stated explicitly; the principle stands.
- A3: agreed on the key, but the receiver cannot always dedupe, and the kernel should *know* when it does not know. Critic's attack on A3: when the tool is not idempotent the workflow is FAILED and the only exit is a fork that mints a new key and re-sends, which is the duplicate the ledger exists to prevent. A3 conceded: "the fork must not be the exit"; the honest rule is kernel stops, human resolves; "I will not build the resolver; I will write the sentence."

**Round 2: replay.**
- A1: replay as an action is what an operator expects; new id, provenance event, nothing overwritten; re-notifying on a new execution is correct, not a duplicate.
- A2: the brief says "no duplicate side effects, especially notify" and then lists `replay`; the only replay that cannot be read as violating the first line is one that never calls a tool. A3: same, and it is what the word means in Temporal.
- A1 conceded the default: fork goes behind a different verb or into the README.

**Round 3: leases.**
- Critic: A1 implemented expiry without a fence; A2 has `BEGIN IMMEDIATE` and calls it a lease; A3 designed columns and did not use them. A1: expiry-with-retry is SQS visibility timeout. A3: SQS *plus an idempotent consumer*; without a fencing token at commit, a slow tool plus an expired lease is two committed attempts; either fence or do not expire. A2: then assume one live process per DB file and say so.
- Critic's ruling: one UPDATE-WHERE claim so two CLI invocations cannot both run a workflow, and `WHERE owner = ?` on every transition so a lost claim cannot commit; fifteen lines; no TTL stealing. A1: acceptable.

**Round 4: unexpected exceptions.** A2: a `KeyError` is a bug, retrying it three times is noise. A3: a `ConnectionResetError` is not a bug and the tool author will not have typed it; under "correctness under failure" the default that loses less is retry-to-exhaustion, with `FatalError` for the tool that knows better. Critic: A3 wins, backoff is small so the cost is nothing.

**Round 5: step-level failure.** A2 took `on_failure: skip` ("ten lines, makes the requirement visibly distinct from retries").

**Round 6: scope.** A1 gave up the dispatcher, hash chains and encryption to the README. A3 gave up `tenant_id`, `schema_version`, `--follow`, but kept `next_run_at` ("one column, and it is what makes backoff survive a restart"). A2 upgraded its restart test to a real crash: uncaught `SimulatedCrash` after the post-classify commit, fresh `Engine` on the same file, call counters asserting zero re-runs and one notification.

**Round 7: log as truth or tables as truth.** A3: log is truth, tables a cache, replay proves they agree. A2: tables answer `status` in one SELECT, nobody folds on the read path. Critic: compatible; tables are what the engine reads, events are what the auditor and replay read, one transaction keeps them equal, the reducer proves it. "Keep both and say exactly that sentence."

**Where all three agreed from the start** (critic's cross-cutting notes): state tables + append-only events in the same transaction; an `ALLOWED` transition set with absorbing terminal states; key excludes the attempt number; cooperative cancel; linear steps; typed exceptions; and the honest admission that no single-process design closes the send-vs-commit window.

---

## 4. The decision path

Each entry: options → who argued what → why this won → tradeoff accepted → what would change it in production. References are to `docs/DESIGN.md` (D-numbers, sections) and the code.

**4.1 Effects ledger with a stable key, not an outbox, not a bare sink (D6, §5).**
Options: A1 outbox + dispatcher; A2 dedupe-only sink; A3 kernel ledger. The critic showed the outbox moves the window without closing it and adds a second retry policy; the sink hides the window inside the test. The ledger won because it records the ambiguity (CLAIMED without DONE) as a queryable fact and keeps `status` honest. Tradeoff: three commits per effectful step instead of one. Production: the key travels as `Idempotency-Key` to a real provider (SES/SendGrid/Stripe honour it); a reconciler lists stuck CLAIMED rows. Code: `engine.py` `_effect_key` = `sha256(wf:step:effect_name)`, `store.claim_effect` / `complete_effect`.

**4.2 `ctx.run_effect(name, payload, fn)`, tools never touch the DB (D6, §8).**
A3 originally exposed `ctx.effect_key(name)` and left "who claims?" open; the critic's attack #3 on A3 ("if the tool claims, the tool knows about the ledger") gave the final shape: the kernel claims, calls `fn(key, payload)`, marks DONE. Tradeoff: effectful tools must be written as a function the kernel can call; for the brief that is one call in `NotifyTool.run`.

**4.3 Ambiguity surfaced (`EFFECT_AMBIGUOUS`), never guessed, never forked (D7, §12).**
Options: always re-send; always fail; build an operator resolver (`effects resolve`); refuse and stop. A3 conceded the dead end the critic found and chose the sentence over the code. Per-tool `idempotent` flag decides: True → re-call with the same key; False → `FatalError("ambiguous effect; needs operator")`, row stays CLAIMED, and with `on_failure=skip` the triage still completes with notify SKIPPED. Tradeoff: a non-idempotent ambiguous effect needs a human. Production: the resolver verb and a reconciler. Test: `test_ambiguous_non_idempotent_fails_safely`.

**4.4 Claim with an owner fence, not a TTL lease (D9, §6).**
Options: A1 TTL lease with stealing; A2 `BEGIN IMMEDIATE` only; critic's claim + fence. Stealing without a fencing token at commit is a double-execution generator (31-second tool, 30-second TTL). The claim is one `UPDATE workflows SET owner=? WHERE id=? AND (owner IS NULL OR owner=?)` (`store.py` ~L279); every workflow-scoped write runs `_fence(wf_id, owner)` inside its `BEGIN IMMEDIATE` (`store.py` L213) and raises `LostClaim`. Tradeoff: a dead owner is not auto-recovered; `run --take-over` clears it, and the README states one live engine per DB file. Production (README §A): the claim becomes `owner, lease_until, lease_token` with heartbeats, and every commit carries `WHERE lease_token = ?`.

**4.5 Event log written in the same transaction as state; reducer proves they agree (D2, §2, §7).**
Options: pure event sourcing (fold on every read); tables only (no audit); both. The critic reconciled A2 and A3: tables for the engine, log for the auditor and replay, one transaction, one reducer. Tradeoff: one extra INSERT per transition. The critic's condition on A3's thesis was honoured: `effects` is also rebuilt from `EFFECT_CLAIMED`/`EFFECT_DONE`, so "log is truth" has no exception. Code: `store.py` has no UPDATE/DELETE statement for `events`; `rebuild_state()` in `engine.py` L284.

**4.6 Replay = read-only reducer + tamper check, not re-execution (D10, §7).**
Options: A1 fork-and-re-execute (re-notifies); A2/A3 read-only rebuild. The brief's "no duplicate side effects" settles it: the only replay that cannot violate the headline requirement never calls a tool. One reducer serves replay and the test (critic's attack #4 on A3: "two reducers is twice the bugs"). Tradeoff: re-running a failed workflow is not a CLI verb; it is a described `start --derived-from`. Tests: `test_replay_rebuild_equals_tables`, `test_replay_detects_tampered_table`, `test_replay_rejects_illegal_log`.

**4.7 Unexpected exceptions retry; `FatalError` short-circuits; backoff persisted (D4, §4).**
A3 over A2 on the default (loses less work); the critic's guard (`FatalError` so the FAILED path is reachable without exhausting attempts; `ExtractTool` raises it on a malformed document). `next_run_at` persisted so a restart during backoff waits the remainder (`test_restart_during_backoff_waits_the_remainder`: 0.03 s of 0.05 s). Constants: base 0.05 s, cap 2.0 s (`spec.py` L21, `engine.py` L60). Tradeoff: a genuine bug costs N quick attempts.

**4.8 Step-level `on_failure: fail_workflow | skip` and the `SKIPPED` state (D3, D5).**
A1 and A3 had it; A2 took it in the debate. `SKIPPED` is the one addition to the brief's four states, kept distinct from SUCCEEDED so the audit trail is honest. Implementation surprise (`DESIGN.md` §3 note): `PENDING → FAILED | SKIPPED` edges were added for a step interrupted on its last attempt, which resumes to PENDING with no budget; the policy must apply from there without starting an attempt it cannot afford.

**4.9 Resume by persisted outputs, not by re-executing code against history (D8).**
Options: re-execute from the beginning; Temporal-style deterministic replay of code; materialized `output_json`. Persisted outputs need no deterministic tools and make `Engine.run` a pure function of (database, registry). Tradeoff: a step interrupted mid-tool is charged one attempt. Test: `test_restart_resumes_without_rerunning` (counters 1/1/1/0 then 0/0/0/1).

**4.10 SQLite, one file, WAL, `synchronous=FULL`, `BEGIN IMMEDIATE` (§2, §6).**
All three agreed; the point of the brief is correctness under failure, not throughput. Tradeoff: one writer. Production: same schema on Postgres, or one SQLite per shard behind a partition map (README §A).

**4.11 Code-defined spec with a ≤ 20-line JSON loader (D12).**
A2/A3 over A1's JSON-first; typed, testable, no dependency. The reviewer later found `--spec file.json` created a workflow nobody could run; the fixer registered loaded specs in `Engine.start` and made `run` accept `--spec`, keeping the registry model rather than persisting the spec body because the versioning story (README §C) is "the registry holds every published version".

**4.12 What was cut, and named in the Time Log (D15).**
Outbox + dispatcher, TTL lease expiry, DAGs, YAML, hash-chained events, encryption at rest, `run --follow`, `tenant_id`, `schema_version`, preemptive cancel, metrics, `effects resolve`, `start --derived-from`. The critic's rule: "a defensible sentence in README and an indefensible 20 lines in a 4-hour codebase"; A1's rule: "if it is in the schema it must be tested; if not, it is prose."

**4.13 The review and what the fixer did (`REVIEW_RESPONSE.md`).**
- **HIGH, fixed:** the effects ledger and loose events (`DECISION`, `EFFECT_AMBIGUOUS`) were not owner-fenced. Reproduced race: engine A stalls inside the receiver call, B takes over and finishes, then A's `complete_effect` overwrites B's record and `replay` still says CONSISTENT because tables and log were corrupted together. Fix: one `_fence` inside every workflow-scoped transaction; `claim_effect`, `complete_effect`, `append_event` take keyword-only `owner`; a `StoreError` raised inside `ctx.*` propagates instead of being retried as a tool failure. Pinned by `test_fenced_out_engine_cannot_finish_the_effect` and `test_stale_owner_cannot_write_ledger_or_events`.
- **MEDIUM, fixed:** `EFFECT_DONE` attributed to the claiming attempt (now the completing attempt); `--spec` dead on arrival; `start --id X` raw `IntegrityError`; tracebacks for every operator error (now exit codes 4/5/6; spec and tools resolved before the claim so `run` never leaves a half-started workflow).
- **LOW, fixed:** non-dict input retried as "transient"; `cancel` printing a promise nobody keeps; stale `error` after success; `run` on a terminal workflow claiming and releasing; input/output key collision; `COMMIT` outside the `try`.
- **Three taken with a variation, and why:** (#10) the reviewer suggested moving crash injection into a `Tool` wrapper; a tool wrapper cannot reach the between-steps crash point because the tool runs *before* its SUCCEEDED commit, so injection lives in `faults.py` at the two real boundaries (`CrashingSink` inside the receiver call, `CrashAfterCommit` as a `Store` subclass) and the engine has no crash hook at all. (#3) kept the registry model over persisting spec bodies, for the reason above. (#19) kept the name `idempotent` on `Tool` but fixed the contract ("the receivers of this tool's effects dedupe on the key"); renaming to `receiver_idempotent` would push a receiver concept into every pure tool.
- **Docs (#13):** `DESIGN.md` read "like the minutes of a committee"; rewritten first-person as three shapes considered and one chosen.
- Result: 23 tests on Python 3.14 and 3.9.6, demo clean on both, the take-over race re-run against the fix (27 tests after the Oct 31 review pass, 31 after the evening pass; see PRINCIPAL_REVIEW.md and walkthrough §9).

---

## 5. Interview commentary (first person, for Ganesh)

### 5.1 The 90-second "how I approached the design"

"The brief says clarity and correctness over features, so I spent the first hour and a half on a design record before writing code. The hard problem is the window between an external side effect returning and the local commit that records it; no single-process design can close it, so the question is what you do about it honestly.

I worked through three shapes. A correctness-first one: a transactional outbox with a dispatcher, TTL leases, replay as a fork. A simplicity-first one: three tables, the sink dedupes on a key, replay is a read-only fold. And an evolution-first one: the event log is the truth, tables are a cache, and side effects go through a claim/done ledger. Then I stress-tested each one adversarially: what is the interviewer's first question, and does the design survive it?

The outbox failed that test: it moves the window into the relay without closing it, adds a second retry policy, and lets `status` say notify SUCCEEDED while nothing has left the building. The bare sink failed it too: if the dedupe table shares the step's connection, the crash window does not exist and the test proves nothing. The ledger won because it records the ambiguity, CLAIMED without DONE, as a fact the kernel can act on per tool.

So the kernel is four tables, one mental model, Engine decides, Store remembers, Tool does, and one stated guarantee: at-least-once invocation, exactly-once recording, exactly-once effect if the receiver honours the key, and when the kernel cannot know, it says so and stops. Then I reviewed it the way a grader would, raced two engines, and found that the ledger writes were not owner-fenced. That fix and its race test are in the repo."

### 5.2 Per-decision talking points

- **Effects ledger.** "The key is `sha256(workflow:step:effect)`, no attempt number, so every retry and every restart presents the same key. The kernel commits CLAIMED, calls the receiver, commits DONE with the result. Three commits instead of one, and in exchange a crash at any of the eight points C0-C7 leaves a state I can name and test."
- **Ambiguity over guessing.** "CLAIMED without DONE means the email may or may not have left; those two crash points are indistinguishable on disk. I refuse to guess: the tool declares whether its receiver dedupes on the key. If yes, I re-send under the same key. If no, the step fails with `ambiguous_effect`, the row stays CLAIMED for an operator, and because notify is `on_failure: skip` the triage still completes. I deliberately did not build a fork around it, because a fork mints a new key and sends the duplicate the ledger exists to prevent."
- **Claim, not lease.** "A TTL lease that steals without a fencing token is a double-execution generator: a 31-second tool under a 30-second TTL gives two committed attempts. So the single-process kernel has a claim with no expiry, and every write to a workflow, transitions, ledger and events, checks the owner inside its `BEGIN IMMEDIATE`. At scale that becomes a lease with a token checked at commit; the fence is already the shape of every write."
- **Log and tables.** "Every transition is one transaction: update the row and append the events that explain it. The engine reads tables, so `status` is one SELECT; the auditor reads events; and `replay` folds the log through the same reducer the tests use and diffs it against the tables. A hand-edited row makes replay exit 1."
- **Replay is read-only.** "The brief says no duplicate side effects and lists replay next to start. The only replay that cannot violate the first requirement never calls a tool. That is also what the word means in Temporal. Re-executing a failed workflow is a different verb, a new execution with its own keys; I described it and did not build it."
- **Retry default.** "Anything a tool did not type as fatal is retried to `max_attempts` with persisted exponential backoff, 0.05 s base, 2 s cap. A `ConnectionResetError` nobody typed should not kill a workflow permanently. `FatalError` exists for the tool that knows better: extract raises it on a malformed document."
- **Resume.** "Outputs are persisted per step, so a new process on the same file re-runs nothing that succeeded. A RUNNING step found at entry gets `ATTEMPT_INTERRUPTED` and goes back to PENDING with the attempt still counted. One case I only found in implementation: interrupted on the last attempt, the step comes back PENDING with no budget, so the failure policy applies from PENDING; two extra edges in the state set and a test."
- **What I cut.** "Outbox, TTL leases, DAGs, YAML, hash chains, encryption, tenant columns, metrics. Each is a sentence in the README with the path to the real thing. If it is in the schema it must be tested; if it is not tested, it is prose."

### 5.3 Likely questions

**"What alternatives did you consider?"** The three shapes above, by name, with the one sentence that killed each: outbox (same window, second retry policy, `status` lies), bare sink (vacuous proof unless the sink has its own connection), fork-replay (re-notifies on a succeeded workflow). Then the smaller forks: unexpected exceptions fatal vs retryable; TTL lease vs claim; log-as-truth vs tables-as-truth, resolved as "both, one transaction, one reducer".

**"What would you do differently with more time?"** In order of value: the operator verb `effects resolve <key> --done|--not-done` for the non-idempotent ambiguous case; a reconciler that lists stuck CLAIMED rows; `start --derived-from` as a real verb; the lease with heartbeat and token for multi-worker; then `tenant_id` as the first component of the key once there is a second tenant to test with. Not DAGs: the brief is linear and a DAG engine is a different project.

**"What breaks at 10k concurrent workflows?"** Nothing in the data model; the single writer and the in-process loop do. 10k workflows is a few hundred transitions per second and ~50 events each of history; Postgres with `events` partitioned by month is comfortable. The loop becomes stateless workers pulling tasks from a queue or a `SKIP LOCKED` table; the claim becomes a lease with a token on every commit; `next_run_at` becomes a delayed message; tools go behind RPC with the key as `Idempotency-Key`. The queue is an accelerator, not the source of truth: tasks can be re-derived from `steps` where PENDING and no owner. The hot spot is the effects ledger scan for stuck rows, which is a periodic reconciler, and the events append, which is partitioned.

**"How did you use AI?"** Honest version: the brief allowed and expected it. I used it as a design sparring panel and an adversarial reviewer: I had three proposals written from different lenses, correctness, simplicity, evolution, then had them attacked and debated, and I made the calls. The ledger, the claim-with-fence, the read-only replay and the retry default are my decisions and I can defend each from the code. After the build I ran a review pass that reproduced every README claim and raced two engines; it found that the ledger writes were not fenced, and I fixed it with a race test. I verified every guarantee with tests, 23 of them across seven files, on Python 3.9 and 3.14. I did not accept the review blindly: three findings I took with a variation, for example crash injection lives at the real boundaries in `faults.py` because a tool wrapper cannot reach the between-steps crash point.

### 5.4 Phrases to avoid

- Never "the agents decided", "the critic said", "the architect proposed". It is "I considered", "I rejected", "I chose".
- No disclaimers ("this is a toy", "I didn't have time to"): say what was cut and why in one line, then stop.
- No reading from notes; know the numbers: 4 tables, 6 modules, 6 verbs, 31 tests (23 before the Oct 31 review passes, 27 after the first), 7 test files, C0-C7, 0.05 s / 2 s, 22 findings, 1 HIGH.
- Do not claim exactly-once. The guarantee has three clauses; say all three.
- Do not call the claim a lease. It is a claim without expiry, and the lease is the production evolution.

---

## 6. Cheat sheet

| Decision | Rejected alternative | Reason | Tradeoff |
|---|---|---|---|
| Effects ledger, key `sha256(wf:step:effect)`, CLAIMED → DONE | Transactional outbox + dispatcher; dedupe-only sink | Same window, but recorded and resolvable per tool; no second retry policy; `status` stays honest | 3 commits per effectful step |
| `ctx.run_effect(name, payload, fn)` | `ctx.effect_key()` with the tool claiming | Tools never touch the DB | Effect must be a callable the kernel invokes |
| `EFFECT_AMBIGUOUS` + per-tool `idempotent`; non-idempotent → fail, stop | Always re-send; always fail; fork a new execution; build a resolver | Never guess, never mint a new key; the resolver is prose | Non-idempotent ambiguity needs a human |
| Claim (`owner`), fence on every write, `--take-over` | TTL lease with stealing; `BEGIN IMMEDIATE` only | Stealing without a commit-time token = double execution | Dead owner cleared by operator; one live engine per file |
| Tables + append-only events in one txn; reducer proves equality | Pure event sourcing; tables only | One SELECT for `status`; audit complete by construction; "log is truth" is tested | One extra INSERT per transition |
| `replay` read-only: `rebuild_state()` + diff, exit 1 | Fork + re-execute (re-notifies) | Only definition that cannot violate "no duplicate notify"; doubles as tamper check | Re-run of a failed workflow is a described verb, not built |
| Unexpected exception = retryable; `FatalError` short-circuits | Unexpected = fatal | Loses less work under failure | A bug costs N quick attempts |
| Backoff `min(0.05·2^(n-1), 2 s)`, `next_run_at` persisted | Cosmetic in-memory backoff | A restart waits the remainder, not zero and not the full delay | One column |
| `on_failure: fail_workflow \| skip`, `SKIPPED` state | Retries only | Step-level failure handling visibly distinct from retries | One state beyond the brief's four |
| Resume via persisted `output_json`; `ATTEMPT_INTERRUPTED` → PENDING, attempt counted | Re-execute from the start; deterministic code replay | No deterministic-tool requirement; engine is a function of (db, registry) | Interrupted attempt charged to the budget |
| `PENDING → FAILED \| SKIPPED` edges | Start an attempt with no budget | Interrupted last attempt must hit the policy from PENDING | Found in implementation, documented |
| SQLite, WAL, `synchronous=FULL`, `BEGIN IMMEDIATE` | Postgres now | Correctness, not throughput, is the brief | One writer |
| Code-defined spec + ≤ 20-line JSON loader, registry holds versions | YAML; persisting spec bodies | Typed, testable, no dependency; versioning = registry rule | `run --spec` needed for file specs |
| Crash injection in `faults.py` (`CrashingSink`, `CrashAfterCommit`) | Engine `crash_after` hook; tool wrapper | Kernel knows no test exception; wrapper cannot reach the post-commit point | Store subclass for one injection |
| Cut: outbox, TTL, DAGs, YAML, hash chain, encryption, tenant_id, schema_version, `--follow`, metrics | Build them | Not tested = prose; none earns 20 lines in 4 hours | Named gaps with a one-line path each |
