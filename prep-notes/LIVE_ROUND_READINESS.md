# Trase live round: readiness pack (written Nov 1 2026, 11:50 PM PT; NOT SUBMITTED as of writing)

Confidential (Trase asked that the brief and the solution never be posted). Private repo only. Builds on
the existing prep docs; it does not repeat them. Everything marked **verified** was run on a scratch
copy of the extracted zip, never on the submission.

**Bottom line first:** the zip is complete, correct and matches the canonical code byte for byte.
**No code, README or zip change is needed before you submit.** The one urgent item is the clock: the
48-hour window ended Sun Nov 1 3:18 PM PT. The 24-hour extension request to Adam (sent Nov 1, thread
`1a18ee4c619c2aad`) has **no reply** as of 11:50 PM. **Submit the zip as-is first thing Monday Nov 2
morning, before 3 PM PT at the latest.**

---

## 0. READ THESE IN THIS ORDER (about 3 h in total; the first three are the minimum, about 70 min)

| # | Doc | Why | Time |
|---|---|---|---|
| 1 | **This file**, §7 RUNNING NOTES, then §4 and §5 | what they will ask and change live | 30 min |
| 2 | [STUDY_GUIDE_FOR_GANESH.md](https://github.com/discordcreater/trase-project/blob/main/prep-notes/STUDY_GUIDE_FOR_GANESH.md) §0, §3, §4 | numbers, control flow, every crash point | 30 min |
| 3 | The code itself: `wfk/engine.py` `_run_owned`, `_run_step`, `_run_effect`; `wfk/store.py` `_tx`, `_fence`, `claim`, `transition_step` (local `~/claude-code-ws/trase-project`) | they will point at lines | 15 min |
| 4 | [INTERVIEW_PREP.md](https://github.com/discordcreater/trase-project/blob/main/prep-notes/INTERVIEW_PREP.md) §4 (pushback) and §5 (drills) | answers to pushback | 20 min |
| 5 | [DESIGN_DECISION_STORY.md](https://github.com/discordcreater/trase-project/blob/main/prep-notes/DESIGN_DECISION_STORY.md) | "why this and not X" for each decision | 25 min |
| 6 | [CODE_WALKTHROUGH_FOR_GANESH.md](https://github.com/discordcreater/trase-project/blob/main/prep-notes/CODE_WALKTHROUGH_FOR_GANESH.md) §9-§10 (late changes) | the newest parts of the code | 20 min |
| 7 | [TR_AUDIT.md](https://github.com/discordcreater/trase-project/blob/main/prep-notes/TR_AUDIT.md) and [PRINCIPAL_REVIEW.md](https://github.com/discordcreater/trase-project/blob/main/prep-notes/PRINCIPAL_REVIEW.md) | what reviewers found and fixed | 20 min |
| 8 | Optional: [PRESENTATION.md](https://github.com/discordcreater/trase-project/blob/main/prep-notes/PRESENTATION.md) (only if they ask you to walk through it), [REQUIREMENTS_ONE_PAGER.md](https://github.com/discordcreater/trase-project/blob/main/prep-notes/REQUIREMENTS_ONE_PAGER.md), [COMPLETENESS_AUDIT.md](https://github.com/discordcreater/trase-project/blob/main/prep-notes/COMPLETENESS_AUDIT.md) | background | 20 min |

Local copy of this file: `~/claude-code-ws/trase-project/prep-notes/LIVE_ROUND_READINESS.md` (excluded from the zip
by `.gitattributes export-ignore`).

**Practice setup for the drills (no AI):** `cp -R ~/claude-code-ws/trase-project /tmp/wfk-drill && cd /tmp/wfk-drill && git checkout -b drill`,
then `~/claude-code-ws/trase-takehome/.venv/bin/python -m pytest -q` after each change, and `git checkout -- .` to reset.
Never practise inside `trase-project` itself; the zip is built from it.

---

## 1. Verification of the submitted artifact (re-run tonight)

| Check | Result |
|---|---|
| Zip `~/claude-code-ws/trase-takehome/dist/ganesh_muthuluru_trase.zip` | 66,346 bytes, 29 entries (25 files + 4 dirs), built Oct 31 4:20 PM; zip comment `2eed227` = `trase-project` HEAD ("docs: evening audit and prep notes follow the code") |
| Zip vs working tree | extracted into a fresh empty dir and `cmp`'d file by file: **identical** to `~/claude-code-ws/trase-project` at `2eed227` (clean tree, pushed) and to the 25 shipped files in `~/claude-code-ws/trase-takehome` |
| Files that must not be there | none: no `.venv`, `__pycache__`, `.db`, `notifications.jsonl`, `.pytest_cache`, `_private/brief`, `prep-notes/`, private docs (`STUDY_GUIDE`, `INTERVIEW_PREP` etc.), no `.git`. `requirements.txt` present (pytest only; runtime is stdlib) |
| Identity/secret leaks | none: no personal name, no paths, no "Claude/AI" text, no Cisco/Salesforce mention in the shipped files |
| Tests from the extracted zip, Python 3.14.0 (fresh venv) | **31 passed**, about 0.6 s wall |
| Tests from the extracted zip, system Python 3.9.6 (fresh venv) | **31 passed** |
| `scripts/demo.sh` from the extracted zip | exit 0; all 7 sections as described in the README: retry delays 0.05 s and 0.1 s; crash after classify, plain `run` refused (exit 3), `--take-over` finishes with 1 notification; crash inside notify, `EFFECT_AMBIGUOUS ... -> resend`, receiver returns `duplicate`, still 1 notification; `replay` "derived from 20 events ... CONSISTENT"; cancel; errors exit 4 |
| Exec bit on `scripts/demo.sh` inside the zip | preserved (`-rwxr-xr-x`) |
| Repo privacy | `discordcreater/trase-project` and `discordcreater/job-search-2026` are both **PRIVATE** (checked with `gh`) |

Counts to quote (all re-measured): 4 tables, 6 modules, 6 CLI verbs, 7 exit codes (0-6), 31 tests in 7 files,
1,444 lines in `wfk/` and 748 in `tests/`, clean run = **16 events**, crash + resume run = **20 events**.

---

## 2. Completeness matrix (brief → evidence in the zip)

| Brief requirement | Where | Evidence | Status |
|---|---|---|---|
| Four steps (Ingest, Extract, Classify, Notify), each a Tool with a clear interface | `wfk/tools.py` `Tool` Protocol; `IngestTool`, `ExtractTool`, `ClassifyTool`, `NotifyTool` | demo §1 | ✅ |
| Declarative spec (JSON/YAML or code) | `wfk/spec.py` `StepSpec`, `WorkflowSpec`, `TRIAGE_V1`, `load_spec_json` | `test_spec_file_round_trips_through_start_and_run` | ✅ (code + JSON) |
| State machine PENDING → RUNNING → SUCCEEDED/FAILED | `store.py` `WF_ALLOWED`, `STEP_ALLOWED`; enforced in `transition_*` | `test_illegal_transition_raises_and_writes_nothing`, `test_replay_rejects_illegal_log` | ✅ (+CANCELLED, +SKIPPED, both justified in README) |
| Retries with max attempts | `engine._run_step`, persisted `next_run_at`, capped exponential backoff | `test_retry_then_success`, demo §2 | ✅ |
| Step-level failure handling | `StepSpec.on_failure` (`fail_workflow`/`skip`), `engine._give_up` | `test_exhaustion_fails_workflow`, `test_skip_policy_lets_workflow_succeed` | ✅ |
| Cancellation (optional) | `Engine.cancel`, cooperative flag | 4 cancel tests, demo §6 | ✅ (bonus) |
| Persist workflow + step state | SQLite WAL, `synchronous=FULL`, 4 tables | all tests use a real file | ✅ |
| Safe resume after restart | `_run_owned` resume loop, owner claim + `--take-over`, `CrashAfterCommit`, `os._exit` | `test_restart_resumes_without_rerunning` + 6 more in `test_restart.py`, demo §3 | ✅ automated, not just described |
| No duplicate side effects, especially notify | effects ledger `CLAIMED`/`DONE`, stable key `sha256(wf:step:effect)`, `EFFECT_AMBIGUOUS` | `test_notify_effect_once_across_crash`, `test_ambiguous_non_idempotent_fails_safely`, `test_done_effect_is_not_resent_on_resume`, demo §4 | ✅ |
| History: state transitions + decisions queryable | `events` table, `DECISION` events, `history --kind` | demo §5 | ✅ |
| Inspect history via CLI | `history`, `status`, `replay` | demo | ✅ |
| Tool interfaces + workflow structure; orchestration separate from execution | `engine.py` imports no concrete tool; tools never see the store (`StepContext`) | module boundaries; README "Engine decides, Store remembers, Tool does" | ✅ |
| CLI: start, status, cancel, replay | `cli.py` (+ `run`, `history`) | `test_cli.py`, demo | ✅ |
| Test: retry OR idempotency | both | 31 tests | ✅ (exceeds) |
| Restart safety demonstrated | automated tests + demo | as above | ✅ |
| Design A: scaling 10k+, multi-service (state ownership, execution model, failure boundaries) | README "A." | all three sub-headings present | ✅ |
| Design B: one concrete failure scenario (what goes wrong, handling, tradeoffs) | README "B." crash during notify + retry after partial success | tested | ✅ (two scenarios) |
| Design C: versioning, not breaking running workflows | README "C." pinning, rules, draining | `spec_version` pinned at start, `_spec_for` | ✅ |
| Design D (optional): multi-tenancy or compliance | README "D." both | described, not built (stated) | ✅ (bonus) |
| README: architecture, execution model, idempotency, how to run, design | `README.md` | all present | ✅ |
| Demo (optional) | `scripts/demo.sh` | runs clean | ✅ |
| Time log (optional) | README "Time log" + "Intentionally simplified" | present | ✅ (see §3 note) |
| Submission format | `ganesh_muthuluru_trase.zip`, no venv/node_modules, requirements file | checked | ✅ |

**No gaps against the brief.**

---

## 3. Findings: precision nits, stale numbers, test gaps (none blocks submission)

Ranked by how likely an interviewer is to notice. None is a correctness bug in the shipped behaviour.

1. **README blockquote: "at-least-once invocation of an effect"** is true only for idempotent receivers. For a
   non-idempotent receiver, a crash after `CLAIMED` commits and before `fn()` is called (C3 at its very start)
   ends in zero invocations: the kernel refuses to resend. The README's own section B says it correctly
   ("the at-least-once vs at-most-once choice is made per tool"). **Say it before they do:** "precisely:
   at-least-once with an idempotent receiver, at-most-once without one; the blockquote compresses that."
2. **A provably unsent effect is treated as ambiguous.** If a non-idempotent receiver raises a transient error
   (connection refused, 503 before it accepted), the claim stays `CLAIMED`, the next attempt records
   `EFFECT_AMBIGUOUS action=fail`, and with notify's `skip` policy the workflow ends **SUCCEEDED, notify SKIPPED,
   no email** after using only 2 of 5 attempts. **Verified** with a probe. It is the safe direction (never a
   duplicate) and the README's operator path covers it, but README A's line "Receiver dies → RetryableError
   before the claim" describes a path the shipped notify tool cannot take (it always claims before calling).
   This is the most likely live change request: §5 drill 1 fixes it in about 20 lines.
3. **With notify on `skip`, an ambiguous non-idempotent effect yields a SUCCEEDED workflow.** Documented (README B
   point 4), visible in `status` (effect `CLAIMED`) and in `history --kind EFFECT_AMBIGUOUS`, but nothing alerts.
   Answer: the reconciler in roadmap item 2.
4. **Test gaps found by mutation (verified):** (a) removing the cancel check at the top of the `_run_step` loop
   (cancel *between steps*, no retry pending) breaks no test; (b) dropping `AND owner = ?` from `Store.release`
   (a stalled old process could clear the new owner's claim) breaks no test. The code is right; the tests do not
   pin it. Both tests are in §5 drill 6, 15 lines total.
5. **Time log totals about 7 h** (1.5 + 1.5 + 0.75 + 0.75 + 0.5 + 2) against a 3-5 h brief, with no total line.
   Honest, and the 2 h row is clearly labelled as self-review and hardening. Expect "you spent seven hours?"
   Answer in §7.
6. **`docs/DESIGN.md` D15** lists `schema_version` among the cuts and says "4-hour codebase", while the code stamps
   `PRAGMA user_version` (the README cut list says "`schema_version` in outputs", which is the intended meaning).
   Cosmetic.
7. Private-only, not in the zip: `PRIORITY_ACTIONS.md` item 1 and `COMPLETENESS_AUDIT.md` still say "23 tests";
   the real number is 31. `~/claude-code-ws/trase-takehome` has uncommitted edits and a different git history
   from `trase-project`; irrelevant to the zip (built from `trase-project`), but use `trase-project` as the
   only source of truth.

**Pre-submit action warranted: none.** Items 1, 2 and 6 could be one-line README/DESIGN rewordings, but changing
the artifact after the deadline costs more (rebuild, re-verify, risk) than it gains; it is stronger to raise
items 1 and 2 yourself in the live round as "what I would tighten next". If you insist on touching it: edit only
the README blockquote in `~/claude-code-ws/trase-project`, commit, then rebuild with
`git -C ~/claude-code-ws/trase-project archive --format=zip -o ~/claude-code-ws/trase-takehome/dist/ganesh_muthuluru_trase.zip HEAD`
and re-run the tests from a fresh extract. Recommended: do not.

---

## 4. The 10 most likely follow-up questions (with the 20-second answer)

1. **"Walk me through what happens if the process dies while sending the email."** C3/C4: `CLAIMED` is on disk,
   notify is `RUNNING`. Resume: `ATTEMPT_INTERRUPTED`, back to `PENDING`, attempt 2 finds `CLAIMED`, writes
   `EFFECT_AMBIGUOUS`; idempotent receiver → resend with the same key, receiver dedupes; non-idempotent → fail the
   step with `ambiguous_effect`, leave the row for an operator. Point at `engine._run_effect` lines 288-313.
2. **"Why not exactly-once?"** Nothing local can close the window between "receiver accepted" and "my commit".
   Exactly-once effect = my stable key + a receiver that dedupes on it. I record the ambiguity instead of hiding it.
3. **"Why does the key not include the attempt number?"** So every attempt and every restart presents the same key
   and the receiver can dedupe across them. With the attempt in it, the resend after a crash is a new key, which
   is a duplicate email (that is debug drill B in §6).
4. **"How do two engines not both run the same workflow?"** `claim` is a conditional UPDATE (`owner IS NULL`); every
   later write re-checks `owner` inside `BEGIN IMMEDIATE` (`_fence`). A stalled process that lost its claim cannot
   commit anything (`test_fenced_out_engine_cannot_finish_the_effect`). The one thing it can still do is finish an
   external call already in flight, which is exactly what the ledger absorbs.
5. **"Why `BEGIN IMMEDIATE` and not a plain transaction?"** A deferred transaction reads under a shared lock and
   upgrades on first write; under WAL the upgrade fails with SQLITE_BUSY if someone else committed, and the fence was
   evaluated on a stale snapshot. IMMEDIATE takes the write lock first, so what I read is what I commit on.
6. **"Why no lease with expiry?"** Without a fencing token checked at commit, expiry lets a slow worker and the new
   owner both commit attempt N. Here the fence is the owner column and recovery is an explicit `--take-over`;
   in the fleet version it is `lease_until` + `lease_token` in every WHERE (roadmap item 1).
7. **"What does replay do, and why does it never call tools?"** Folds `events` through the same ALLOWED sets
   (`rebuild_state`), diffs the projection against the tables, exits 1 on any difference. Re-executing tools would be
   the one definition of replay that can send a duplicate email; re-execution is a new workflow with new keys.
8. **"How does this scale to 10k concurrent workflows / many services?"** Shard by `hash(workflow_id)`, Postgres,
   stateless workers pulling "run step X of W" from a queue (or `SKIP LOCKED`), lease + fencing token, tools behind
   RPC with the effect key as `Idempotency-Key`; the queue is an accelerator, `steps` is the truth. 10k workflows is
   a few hundred transitions per second: one Postgres.
9. **"How do you change a workflow without breaking running ones?"** `spec_version` pinned at `start`; old versions
   stay registered until drained; tool contract changes get `tool@version`; migrating in-flight work is an explicit
   `MIGRATED` event. The effect key is version-free on purpose, so a migrated workflow still dedupes.
10. **"Unexpected exceptions are retried, not failed fast. Why?"** Under "correctness under failure" the default that
    loses less work wins; a tool that knows better raises `FatalError`. Cost: a real bug burns N quick attempts. A
    non-JSON output is the exception: it is a tool bug, so it is fatal at once.

Bonus questions to have one line for: **how was AI used** (as allowed: design debate and review; every decision
is one you can defend line by line, which is what today is for); **what would you cut** (`faults.py` stays, it is
the proof); **the hardest bug you found** (the effects ledger was not owner-fenced, so a fenced-out engine could still
write `DONE`; found by racing two engines, fixed with one `_fence` per workflow-scoped transaction and a test).

---

## 5. Live change requests: 8 drills with exact files, diffs and tests

Format: **ask → plan you say aloud (30 s) → files/functions → minimal diff → test → time**. Drills 1-6 are
**verified**: implemented on a scratch copy of the zip, all of them applied together plus drill 5, full suite green (31 → 37 tests).

### Drill 1 (most likely): "A receiver says connection refused. Don't treat that as ambiguous." **verified**
Plan: let the effect fn say "provably not sent"; the kernel releases the claim, so the normal retry sends cleanly.
New event `EFFECT_RELEASED`; reducer pops the effect. Files: `tools.py`, `store.py`, `engine.py` (`_run_effect`, `rebuild_state`).
```python
# tools.py
class EffectNotPerformed(RetryableError):
    """The receiver provably did NOT accept; the kernel releases the claim."""

# store.py, next to complete_effect
def release_effect(self, key, *, owner, attempt, reason):
    with self._tx():
        row = self.get_effect(key)
        if row is None or row.status != "CLAIMED":
            raise LostClaim(f"effect {key[:12]}: expected CLAIMED")
        self._fence(row.workflow_id, owner)
        self.conn.execute("DELETE FROM effects WHERE key = ? AND status = 'CLAIMED'", (key,))
        self._append(row.workflow_id, row.step_name, attempt,
                     [("EFFECT_RELEASED", {"key": key, "reason": reason})])

# engine.py, _run_effect: wrap the external call
try:
    result = fn(key, payload)
except EffectNotPerformed as e:
    self.store.release_effect(key, owner=owner, attempt=attempt, reason=str(e))
    raise                                  # it is a RetryableError: the normal retry path runs
# rebuild_state
elif t == "EFFECT_RELEASED":
    p.effects.pop(pl["key"])
```
Test: a non-idempotent sink that raises `EffectNotPerformed` on call 1 and succeeds on call 2; spec with notify
`fail_workflow`; assert `SUCCEEDED`, 2 calls, 1 delivery, one `EFFECT_RELEASED`, no `EFFECT_AMBIGUOUS`, effect `DONE`,
`consistency_diff(store, wf) == []`. Say the caveat: only the receiver can know "provably"; a timeout is never this
exception. 12-15 min.

### Drill 2: "Give operators a way to settle an ambiguous effect." **verified**
Plan: `Store.resolve_effect(key, "done"|"not_sent", by)` plus CLI verb `resolve KEY --as done|not-sent`. Operator
assertion, like `--take-over`, so not owner-fenced; must run **before** resuming, because a workflow that already
reached FAILED is terminal (absorbing) and will not continue. (INTERVIEW_PREP §5.6 says "then `run` continues";
true only if you resolve before the resume.)
```python
def resolve_effect(self, key, outcome, by):
    with self._tx():
        row = self.get_effect(key)
        if row is None or row.status != "CLAIMED":
            raise IllegalTransition(f"effect {key[:12]} is not CLAIMED")
        ev = [("EFFECT_RESOLVED", {"key": key, "outcome": outcome, "by": by})]
        if outcome == "done":
            result = {"status": "resolved_done", "by": by}
            self.conn.execute("UPDATE effects SET status='DONE', result_json=?, updated_at=? WHERE key=?",
                              (dumps(result), self.now(), key))
            ev.append(("EFFECT_DONE", {"key": key, "result": result}))     # reducer already handles it
        else:
            self.conn.execute("DELETE FROM effects WHERE key = ?", (key,))
            ev.append(("EFFECT_RELEASED", {"key": key, "reason": f"operator {by}"}))  # needs drill 1's reducer line
        self._append(row.workflow_id, row.step_name, None, ev)
```
CLI: `rs = sub.add_parser("resolve"); rs.add_argument("key"); rs.add_argument("--as", dest="outcome", choices=["done","not-sent"], required=True)`.
Test: non-idempotent `CrashingSink` crash, `resolve_effect(key, "done", "ops")`, `run(take_over=True) == "SUCCEEDED"`,
new sink called 0 times, no `EFFECT_AMBIGUOUS`, replay consistent. 12 min.

### Drill 3: "A resend could carry a different body. Detect it." **verified** (includes a schema migration)
Plan: store `payload_hash` on claim; on a `CLAIMED` resend compare; mismatch → `FatalError`. Schema v1 → v2.
```python
# store.py
SCHEMA_VERSION = 2
# SCHEMA: effects gets "payload_hash TEXT" as the last column
if found == 1:
    self.conn.execute("ALTER TABLE effects ADD COLUMN payload_hash TEXT")
# EffectRow gets payload_hash: Optional[str] = None; _effect passes r["payload_hash"]
# claim_effect(..., payload_hash=None) inserts it
# engine._run_effect
phash = hashlib.sha256(dumps(payload).encode()).hexdigest()
...  # inside the CLAIMED branch, before the resend:
if row.payload_hash and row.payload_hash != phash:
    raise FatalError(f"effect '{name}': payload changed since attempt {row.attempt} claimed it")
```
Test: crash inside notify, resume with a NotifyTool subclass that changes `notify_to`; notify `SKIPPED` with
"payload changed", sink called 0 times, row still `CLAIMED`. Also say: a v1 file opened by this kernel migrates
(verified: `user_version` 2, column present), and `test_newer_schema_is_refused` still passes. 15 min.

### Drill 4: "Add a `redact` step between extract and classify." **verified**
Plan: new side-effect-free tool, **new spec version** `TRIAGE_V2`, v1 stays registered, in-flight v1 workflows unaffected.
```python
# tools.py
class RedactTool:
    name, side_effect_free, idempotent = "redact", True, True
    def run(self, ctx, inputs):
        body, n = re.subn(r"[\w.+-]+@[\w-]+\.[\w.]+", "[email]", inputs["extract"]["body"])
        ctx.record_decision("redactions", {"count": n})
        return {"title": inputs["extract"]["title"], "body": body}
# spec.py
TRIAGE_V2 = WorkflowSpec("document_triage", 2, (TRIAGE_V1.steps[0], TRIAGE_V1.steps[1],
    StepSpec("redact", "redact", max_attempts=2), TRIAGE_V1.steps[2], TRIAGE_V1.steps[3]))
SPECS = {TRIAGE_V1.key: TRIAGE_V1, TRIAGE_V2.key: TRIAGE_V2}
# cli.py build_registry: "redact": RedactTool()
```
Test: start v1, crash after extract, resume on the new registry → still 4 steps, SUCCEEDED; start v2 → redact output
masks the email. **Point to raise yourself:** classify and notify still read `inputs["extract"]`; for redaction to
matter they must read `inputs["redact"]`, which is a tool contract change → `classify@2`/`notify@2` registry keys,
exactly README C's rule. 10-12 min.

### Drill 5: "Add jitter to the backoff." **verified**
Plan: inject randomness like the clock. `Engine.__init__(..., rand=random.random)`;
`cap = min(base * 2**(attempt-1), BACKOFF_CAP_S); delay = cap * (0.5 + 0.5 * self.rand())`.
**Trap:** `test_retry_then_success` and `test_restart_during_backoff_waits_the_remainder` fail (they assert exact
delays). Fix in `tests/conftest.py` `_make`: `kw.setdefault("rand", lambda: 1.0)`. Replay stays deterministic
because the chosen `delay_s` and `next_run_at` are persisted in `RETRY_SCHEDULED`. 6 min.

### Drill 6: "Write the tests your suite is missing." **verified** (both fail on the mutated code, pass on yours)
```python
class CancelsThenSucceeds(ClassifyTool):
    def __init__(self, engine): self.engine = engine
    def run(self, ctx, inputs):
        self.engine.cancel(ctx.workflow_id, by="test")
        return super().run(ctx, inputs)

def test_cancel_between_steps_stops_before_notify(make_engine, notifications):
    engine = make_engine()
    engine.registry = registry(FileNotifier(notifications), classify=CancelsThenSucceeds(engine))
    wf = engine.start(TRIAGE_V1, INPUT)
    assert engine.run(wf) == "CANCELLED"
    assert engine.store.load_step(wf, "classify").state == "SUCCEEDED"
    assert engine.store.load_step(wf, "notify").attempt == 0

def test_stale_engine_release_does_not_free_new_owner(make_engine):
    e = make_engine(); wf = e.start(TRIAGE_V1, INPUT)
    e.store.claim(wf, "old"); e.store.claim(wf, "new", take_over=True)
    e.store.release(wf, "old")
    assert e.store.load_workflow(wf).owner == "new"
```
5 min.

### Drill 7 (sketch): "`status --json` and `status --state RUNNING`."
`cli.py` `cmd_status`: `st.add_argument("--json", action="store_true")`, `st.add_argument("--state")`; filter
`store.list_workflows()` by `wf.state`; JSON = `dataclasses.asdict(wf)` plus `steps: [asdict(s) for s in store.load_steps(wf.id)]`
plus `effects`. Test via the `run` fixture in `test_cli.py`, `json.loads` the output. 8 min. Warm-up; do it fast.

### Drill 8 (sketch): "A step must time out after N seconds."
`StepSpec.timeout_s: Optional[float] = None`. In `_run_step`, run `tool.run` in a `concurrent.futures.ThreadPoolExecutor(1)`
and `future.result(timeout=step.timeout_s)`; on `TimeoutError` raise `RetryableError("timeout")`. Say the two caveats
before coding: (1) Python cannot kill the thread, so the late tool may still call `ctx.*`; add a `closed` flag on
`StepContext` checked in `record_decision`/`run_effect` (raise `StoreError`); (2) SQLite connections have thread affinity,
so the tool thread must not use the engine's `Store` (it doesn't: `ctx.*` would, hence the flag). A timeout inside an
effect becomes `EFFECT_AMBIGUOUS` next attempt, by design. 15 min; it is fine to stop at the plan and the caveats.

Other asks to have a plan for (one sentence each): **cancel inside a long tool** (`ctx.check_cancel()`, INTERVIEW_PREP §5.2);
**a second effectful tool** (`archive`: its own `effect_name`, declare `idempotent` from the receiver's truth);
**parallel steps** (ask: DAG or fan-out inside a step; `depends_on` + one Store per thread; the fence is unchanged);
**retry the whole failed workflow** (never re-run in place: `start --derived-from <id>` creates a new execution with
new effect keys, which is why `replay` is read-only).

---

## 6. Debug drills: three injected bugs (verified symptoms)

Method to say aloud every time: *reproduce with `pytest -x -vv -k <name>` → read the first assertion, not the last →
print the event sequence (`for e in store.events(wf): print(e.seq, e.step_name, e.type, e.payload)`) and compare it
with the clean 16-event shape → suspects in order: an `ALLOWED` pair, a wrong `frm` in a `transition_step`, a missing
reducer branch, the effect key.*

**Bug A: backoff off by one.** Injected: `engine.py` `_run_step`, `2 ** (attempt - 1)` → `2 ** attempt`.
Symptom: `test_retry_then_success`: `assert [0.1, 0.2] == [0.05, 0.1]`; also `test_restart_during_backoff_waits_the_remainder`.
Find: the first delay is double the base → exponent wrong. Fix the exponent. 2 min.

**Bug B: duplicate email after a crash.** Injected: `engine.py` `_run_effect`, key built as
`effect_key(wf_id, step_name, f"{name}#{attempt}")`. Symptom: 5 tests fail (`test_notify_effect_once_across_crash`:
`get_effect(key)` is `None`; the demo §4 would print **2** notifications). Find: after resume, `history --kind EFFECT_CLAIMED`
shows two different keys for the same workflow; there is no `EFFECT_AMBIGUOUS` because attempt 2 found no row under
its new key. Root cause: the key must not contain the attempt. 5 min.

**Bug C: resume dies with LostClaim.** Injected: `engine.py` `_run_owned`, the loop that moves `RUNNING` steps back to
`PENDING` disabled. Symptom: `wfk.store.LostClaim: step <id>/notify: expected state PENDING` on every crash-inside-a-step
test. Find: the error says the store expected `PENDING`; `status` shows notify `RUNNING`; nothing wrote
`ATTEMPT_INTERRUPTED` → the resume loop did not run. The fence did its job: it refused a transition from a state the
row was not in. 5 min.

**Silent bugs (no test catches them today; drill 6 adds the tests):** removing the cancel check at the top of
`_run_step`'s loop; dropping `AND owner = ?` from `Store.release`. Good answer to "what would your tests miss?".

---

## 7. RUNNING NOTES (keep this open during the call)

**2-minute summary.** "wfk is a small durable workflow kernel: Engine decides, Store remembers, Tool does. Four
triage tools behind a `Tool` protocol; the engine never imports a concrete tool and tools never see the database,
only a `StepContext` with two services: record a decision, run an effect. State lives in one SQLite file in WAL mode:
four tables, and every state change is one `BEGIN IMMEDIATE` transaction that updates the row and appends the event
that explains it, fenced on the workflow's owner, so a process that lost its claim cannot write anything. Resume is
trivial because the engine holds no state: a new process reads the rows, moves an interrupted step back to PENDING
with its attempt charged, and continues; finished steps are never re-run. The hard part is notify: an effects ledger
with a key that is stable across attempts. CLAIMED before the call, DONE after. If a crash leaves CLAIMED without DONE,
the kernel records EFFECT_AMBIGUOUS and resends with the same key only if the receiver dedupes; otherwise it stops and
leaves it for an operator. Replay is read-only: it rebuilds state from the log and proves the tables match. 31 tests,
every crash injected at a real boundary, plus a demo of each failure path."

**Decisions, one line of why each.**
- SQLite WAL + `synchronous=FULL`: durable before the next action, zero dependencies; Postgres behind the same Store later.
- State row + append-only log in one transaction: tables are fast to read, the log explains them, they cannot disagree.
- Closed state machines as two set literals: an illegal move raises and writes nothing; replay uses the same sets.
- Owner claim + fence at commit, manual `--take-over`, no TTL: expiry without a fencing token lets two workers commit.
- Effects ledger with an attempt-free key: dedupe across retries and restarts; ambiguity is recorded, not hidden.
- Idempotency declared per receiver: the kernel resends only to receivers that honour the key.
- Unknown exceptions retry, `FatalError` fails now, non-JSON output is fatal: lose the least work by default.
- Notify `on_failure=skip`: a dead mail provider should not fail a triage that already classified.
- Replay never runs tools: re-execution is a new workflow with new keys.
- Crash injection outside the kernel (`faults.py`, `os._exit`): the kernel has no test hooks.

**Five facts to know cold.**
1. Key = `sha256(workflow_id:step:effect_name)`, no attempt, no spec version.
2. Backoff = `min(0.05 · 2^(n−1), 2.0)` s → 0.05, 0.1; max attempts ingest 3, extract 3, classify 2, notify 5 (skip).
3. Clean run = 16 events; crash in notify + resume = 20; 31 tests in 7 files; 4 tables, 6 modules, 6 verbs.
4. Exit codes: 0 ok, 1 replay inconsistent, 2 FAILED/CANCELLED, 3 owned, 4 store refused, 5 bad input, 6 DB error.
5. Crash points C0-C7 on notify; C3/C4 is the only ambiguous window; C5 (DONE, step still RUNNING) returns the stored result.

**Honest limitations and what I would do next.**
- One live engine per file; recovery is a human `--take-over` → lease + fencing token (roadmap 1).
- An ambiguous non-idempotent effect needs an operator and there is no verb yet → `resolve` + reconciler (roadmap 2; drill 2).
- A provably unsent effect to a non-idempotent receiver is still treated as ambiguous → `EffectNotPerformed` (drill 1).
- Payload is rebuilt on resend, not stored → payload hash (roadmap 3; drill 3).
- No jitter, interrupted attempts retry immediately → fine for an operator-driven resume, wrong for a fleet (roadmap 4).
- Cancel is cooperative, between attempts; a long tool runs to the end.
- "At-least-once" in the README blockquote means with an idempotent receiver; otherwise at-most-once.
- Time: about 7 h, above the 3-5 h guide: "the core was about 5 hours; the last two were a self-review where I raced
  two engines and found the ledger was not owner-fenced, and I would rather ship that fix than the time box."

**When you don't know.**
- "I don't know offhand. Here is how I'd find out: I'd write a test that puts the row in exactly that state and run resume."
- "Let me check the code rather than guess." (then open the function; reading your own code aloud is a strength)
- "Two options: A is simpler, B survives X. Given the brief, I'd pick A and note B. Do you want me to code A?"
- "Before I type: which role does this touch (engine, store, tool, spec), which events change, what the reducer needs,
  and the one test that proves it."
- For a requirement change: "Let me restate it to be sure… Does it need to hold across a crash, or only in a live process?"

---

## 8. Verdict

The submission meets every requirement in the brief, exceeds it on restart safety (automated, not described),
idempotency (ledger + explicit ambiguity) and auditability (queryable decisions + a replay that proves the tables),
and the zip is clean and byte-identical to the canonical code; 31/31 tests pass on Python 3.9.6 and 3.14 from a fresh
extract and the demo runs clean. No defect warrants touching the code, README or zip. The only risk is the clock: the
window closed Sun 3:18 PM PT and Adam has not confirmed the extension, so submit first thing Monday. For the live round,
the likeliest pressure points are the precision of "at-least-once", the transient-error-after-claim path, the
7-hour time log and live extensions of `_run_effect`; drills 1-3 and 6 above cover them and were proven to work.
