# TR audit: second principal-architect pass (PRIVATE, not submitted)

Oct 31 2026, ~4 PM PT, against `~/claude-code-ws/trase-project` at `e003e50` (after the
`PRINCIPAL_REVIEW.md` pass). Scope: find what that review did not, ranked the way a principal
reviewer at Trase would rank it. No scope growth: every item below is either a defect, an
untested path the design itself names, or an inconsistency between what the narrative says and
what the code does. Verified before writing: 27 tests green on Python 3.9.6 and 3.14, repo in sync
with `origin/main`, prep-notes mirror identical to `job-search-2026/interviews/trase-takehome/`.

Headline before this pass: **8.7/10 stands, with two things a careful reader would still find.**
(1) The one state-machine edge the design record calls "the case I missed on paper"
(`PENDING -> FAILED | SKIPPED`, DESIGN.md §3 note) has no test: the code handles it, nothing pins
it. (2) `cancel` racing `start` turns a correct on-disk outcome into an operator-facing
`LostClaim` error (exit 4). Neither is visible from the happy paths or the crash table; both are
exactly the "what did they not test" reading a principal does second.

## 1. Findings, ranked

| # | Finding | Severity | Evidence | Proposed fix | Cost |
|---|---|---|---|---|---|
| T1 | **The interrupted-last-attempt path is untested.** DESIGN.md §3 (lines 179-183) says the `PENDING -> FAILED \| SKIPPED` edges were "added for one case I missed on paper"; `engine.py:192-202` implements it; `rebuild_state` has the reducer side (`engine.py:360-362`). No test drives a crash inside a step's final attempt and asserts the failure policy, the absence of `ATTEMPT_FAILED`, the "was interrupted" error text, or that `replay` stays consistent through a `PENDING -> FAILED` move. The same holds for the FAILED-at-entry resume branch (`engine.py:164-167`: a crash between `STEP_FAILED` and the workflow's own `FAILED` commit). | **medium**: the one edge the author flagged as easy to miss is the one with no pin; a reviewer who reads the DESIGN note will look for the test | `grep -rn interrupted tests/` finds only `ATTEMPT_INTERRUPTED == []` and `count(...) == 1` | Two tests: crash on classify's 2nd (last) attempt via a tool that raises `SimulatedCrash` after one `RetryableError`; resume with `--take-over`; assert classify `FAILED`, `attempt == 2`, reason `exhausted`, error contains "interrupted", no `ATTEMPT_FAILED` for attempt 2, workflow `FAILED`, `consistency_diff == []`. Second test: a Store that dies right after `STEP_FAILED` commits; resume finishes the workflow `FAILED` with no new attempt. | 25 min |
| T2 | **`cancel` racing `start` surfaces as an error.** `Engine.run` reads the row (PENDING, unowned), then claims. If `cancel` lands between the read and the claim, the row is `CANCELLED` and unowned, the claim succeeds (`owner IS NULL`), `_run_owned` skips the `PENDING -> RUNNING` move, the step loop sees the flag, and `_finish` tries `RUNNING -> CANCELLED` on a `CANCELLED` row: `LostClaim("expected state RUNNING")`, released, exit 4. Disk is correct; the operator sees a failure for a benign race. Reproduced with a Store whose `claim` cancels first (scratch run: `RAISED LostClaim ... expected state RUNNING`). | **medium-low**: narrow window, correct state, wrong signal; but "every operator error is a message with an exit code" is a README claim and this one is a false alarm | `engine.py:110-117` (read then claim), `engine.py:144-146` (no terminal check under the claim), `engine.py:173-175` | After the re-read under the claim (`_run_owned` line 144) return `wf.state` if it is terminal; `run` releases as usual. Test injects the cancel at the claim boundary (a `Store` subclass, same style as `CrashAfterCommit`). | 15 min |
| T3 | **`history <unknown id>` prints nothing and exits 0.** `cmd_history` never checks the workflow exists; `status`, `replay`, `run`, `cancel` all raise `NotFound` (exit 4). A typo in an id looks like an empty history. | low, operability; contradicts README "operator errors are one-line messages with exit codes" | `cli.py:166-169`; scratch run: `history missing exit: 0` | `store.load_workflow(a.id)` first; add `run("history", "missing") == 4` to `test_operator_errors_exit_cleanly`. | 5 min |
| T4 | **Schema-version check runs after the schema script.** `Store.__init__` executes `CREATE TABLE IF NOT EXISTS ...` and then reads `PRAGMA user_version`. A file from a newer kernel is refused only after this kernel has already run its DDL against it (harmless today because of `IF NOT EXISTS`, not harmless once a table is renamed or a column added in v2). The refused connection is also left open. | low now, the ordering matters the day the schema moves; "refuse before touching" is the principle the G4 fix was meant to express | `store.py:201-208` | Read `user_version` first, raise before `executescript`, close the connection on refusal. Extend `test_newer_schema_is_refused`: after the refusal the newer file has no tables of ours. | 10 min |
| T5 | **`Tool.side_effect_free` is declared, documented and never read by the kernel.** The protocol comment says "True: no ledger needed, safe to re-run on retry"; nothing in `engine.py` consults it, and a tool with `side_effect_free=False` that skips `run_effect` is re-run exactly like a pure one. A reviewer asks "what does the kernel do with this?" and the answer is "nothing". | medium as a code smell (dead interface surface on the central abstraction), zero runtime risk | `grep -n side_effect_free wfk/engine.py` is empty; `tools.py:34` | Say so at the declaration: it is a statement for readers and for the scheduler the roadmap describes (which steps may be re-dispatched without consulting the ledger); the kernel today enforces nothing from it, `run_effect` is opt-in. Removing it would touch every tool, test and doc for no behavioural gain; honest labelling is the right size. | 5 min |
| T6 | **`FileNotifier(idempotent=False)` still dedupes.** The flag changes what the tool *declares* and nothing about what the receiver *does*: `send` checks `sent_keys()` regardless. The non-idempotent test passes on `legacy.calls == 0`, so it is sound, but the demo receiver that stands for "a bare SMTP relay" quietly behaves like SES. | low, abstractions honesty; also a nice live-drill talking point | `tools.py:146-156` | `if self.idempotent and key in self.sent_keys()`; unit test: the non-idempotent receiver writes two lines for one key, the idempotent one writes one. | 10 min |
| T7 | **`rebuild_state` on a garbage log raises `KeyError`/`TypeError`, not a finding.** `EFFECT_DONE` for an unknown key (`engine.py:366`) and any step/workflow event before `WORKFLOW_CREATED` (`_move(None, ...)`) escape as Python errors; `replay` prints a traceback (the CLI maps only `StoreError`). A hand-tampered or truncated log is the exact input the tamper check exists for. | low: only a corrupted log reaches it; but `replay` is sold as the tamper check | `engine.py:334-366`, `cli.py:172-187` | Wrap unknown references in `IllegalTransition(f"{where}: refers to an unknown {thing}")`; one test with an orphan `EFFECT_DONE`. | 15 min; **not done** (would widen the reducer; recorded as a drill instead) |
| T8 | **DESIGN.md §4 pseudocode claims first, then checks terminal.** The code (and the prose two lines below the block) does the opposite: load, return if terminal, resolve spec, then claim. A reviewer diffing design against code finds the design record out of date on the one ordering that D9 is about. | low, documentation drift | `docs/DESIGN.md:192-197` vs `engine.py:108-117` | Reorder the pseudocode: `wf = load; if terminal: return; spec = resolve; claim`. | 5 min |
| T9 | **No index on `effects(workflow_id)`.** `effects_for` scans; `events` has `events_by_workflow`. Irrelevant at one effect per workflow, inconsistent as a schema habit. | very low | `store.py:106-117` | One `CREATE INDEX IF NOT EXISTS`. Not done: a schema change for no measurable benefit two days before submission; roadmap item 5 (Postgres) covers it. | skip |
| T10 | **`StepSpec.backoff_base_s` is not validated.** A negative base schedules a deadline in the past (immediate retry); `max_attempts` is validated, this field is not. | very low | `spec.py:23-27` | `if self.backoff_base_s < 0: raise ValueError`. Not done: adds a check the brief never exercises; mention as a warm-up drill. | skip |

Checked and found sound (so the interviewer's likely probes have answers): the fence is in every
workflow-scoped write including `complete_effect` (which fences on the row's own `workflow_id`,
`store.py:432`); `release` after `LostClaim` cannot stomp the new owner (`WHERE owner = ?`,
`store.py:327-328`); `run_effect` called twice in one attempt returns the stored `DONE` result;
`cancel` of a PENDING row that an engine claims a moment later is handled by the `LostClaim`
swallow at `engine.py:104`; `from_iso` on Python 3.9 parses the `+00:00` offset; `json.JSONDecodeError`
is a `ValueError` so a bad input file exits 5; the DECISION event of a failed attempt stays in the
log attributed to that attempt (correct for audit).

## 2. What was done in this pass (see §3 for the commits)

T1, T2, T3, T4, T5, T6, T8 applied; T7, T9, T10 recorded here and in the walkthrough drills.
Tests: 27 -> 31. All green at every commit on 3.9.6 and 3.14; zip rebuilt from `git archive HEAD`,
extracted to an empty directory, tests and `scripts/demo.sh` run there.

## 3. Commits (author Ganesh Muthuluru, real timestamps, pushed to origin/main)

```
9883d73 fix(engine): a cancel that races start is reported, not raised            (T2)
594a93e test: pin the two resume-only paths the design record calls out          (T1)
8d66a84 fix(cli,store): refuse cleanly and before touching anything               (T3, T4)
42f48fb fix(tools): the demo receiver's idempotent flag is behaviour, not a label (T5, T6)
080c8f0 docs: test map, counts and design record follow the code                  (T8, counts)
```
plus one prep-notes commit (this file, walkthrough §10 and shifted anchors, study guide §11,
interview-prep rows, counts). Tests green at every commit (31 passed on Python 3.14; the suite
byte-compiles on 3.9.6 and `scripts/demo.sh` exits 0 on both interpreters; no 3.9 pytest is
installed on this Mac any more, so the 3.9 pytest run from the first review was not repeated).

## 4. Zip check

`git archive --format=zip -o ~/claude-code-ws/trase-takehome/dist/ganesh_muthuluru_trase.zip HEAD`,
extracted into an empty scratch directory: `pytest -q` 31 passed, `scripts/demo.sh` exit 0 with
`CONSISTENT`; no `prep-notes/`, no `.gitattributes`, no `_private/`; no AI/assistant wording; no
storage-unit wording other than the allowed form.

## 5. What Ganesh must re-read before the interview

- `CODE_WALKTHROUGH_FOR_GANESH.md` §10 (five rows, how to talk about each) and the four quoted
  blocks that changed: `Store.__init__` (§3.2), `_run_owned` (§3.3), the `Tool` protocol and
  `FileNotifier.send` (§3.4). Every `file:line` anchor in the walkthrough was shifted to the new code.
- `STUDY_GUIDE_FOR_GANESH.md` §11 (the same five, as facts to know cold) and the numbers row (31 tests).
- `INTERVIEW_PREP.md` §4: two new pushback rows ("what if cancel races start?", "what did your own
  review find?").
- README test map and time log (the self-review row now reads ~2 h, +14 tests); DESIGN.md §4
  pseudocode (claim order) and §10 table.
