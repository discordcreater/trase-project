# Self-review pass (2026-10-30): findings and what I did with them

I reviewed the first commit the way an interviewer would: reproduce every claim in the README,
try the operator errors, race two engines. Twenty-two findings; all but three taken as written,
and those three are argued below. Tests went from 17 to 23; every fix has a test or a demo line.

## Defects

| # | Finding | Fix |
|---|---|---|
| 1 | **Effects ledger and loose events were not fenced by owner.** Engine A stalled inside the receiver call, B took over and finished, then A's `complete_effect` overwrote B's record and `replay` still said CONSISTENT (tables and log corrupted together). The README's "every transition carries `AND owner=?`" was true and beside the point: the ledger writes were not transitions. | One `Store._fence(wf_id, owner)` runs inside every workflow-scoped transaction (`BEGIN IMMEDIATE` holds the write lock, so the SELECT it does is what the COMMIT is based on). `claim_effect`, `complete_effect` and `append_event` take `owner` keyword-only; `StepContext` carries the engine's claim. `test_stale_owner_cannot_write_ledger_or_events` (unit) and `test_fenced_out_engine_cannot_finish_the_effect` (the exact race, in-process) pin it. A kernel error raised inside `ctx.*` now propagates instead of being retried as a tool failure. |
| 2 | `EFFECT_DONE` attributed to the claiming attempt. | `complete_effect(..., attempt=)` takes the completing attempt; the race test asserts `EFFECT_DONE.attempt == 2`. |
| 3 | `--spec file.json` created a workflow nobody could run. | `start` validates and registers the spec in the engine (`Engine.start` refuses a different body under an already-registered key); `run` accepts the same `--spec` so a later process can resolve it; unknown `name@version` is a message, exit 5. `test_spec_file_round_trips_through_start_and_run`. I kept the registry model rather than persisting the spec body: the README's versioning section says the registry holds every published version, and that is the deployment rule I would actually enforce. |
| 4 | `start --id X` on an existing id: raw `IntegrityError`. | `AlreadyExists(StoreError)`, printed as "already exists", exit 4. DESIGN §6/D8 now say `run` resumes; `start` only creates. |
| 5 | Tracebacks for every operator error. | `main()` maps `StoreError` → 4, `ValueError`/`OSError` (bad JSON, unknown spec, unknown tool, malformed spec file, bad `--fail`) → 5, `sqlite3.Error` (locked db) → 6. The spec and its tools are resolved **before** the claim, so `run` never leaves a half-started workflow. `test_operator_errors_exit_cleanly`, demo §7. |
| 6 | Non-dict input retried 3x as "transient". | `Engine.start` validates `isinstance(input, dict)` before persisting (`ValueError`). |
| 7 | `cancel` promised a follow-up nobody would make. | `Engine.cancel` returns the actual state; the CLI prints the owner and the `run <id> --take-over` route, or "no live engine, CANCELLED on next run". |
| 8 | Stale `error` after success; wrong cause on an interrupted last attempt. | `STEP_SUCCEEDED` clears `error` (store and reducer); the exhausted-after-interrupt path writes "attempt N of N was interrupted; no attempts left (error before it: ...)". |
| 9 | `run` on a terminal workflow claimed and released. | `run` loads first; terminal → report, no claim (`test_run_on_terminal_workflow_does_not_claim`). With `--take-over` a stale owner on a terminal row (crash point C7) is released. |
| 10 | Test hooks in the kernel (`crash_after`, `SimulatedCrash` import, `except SimulatedCrash` in `run`). | Gone. `engine.py` imports nothing from the fault code. `run` releases on return or on any `Exception`; a `BaseException` (the process dying) propagates without a release, which is exactly the stale owner a real crash leaves. **Partial pushback on the suggested shape:** a `Tool` wrapper cannot reach the between-steps crash point, because a tool runs *before* its SUCCEEDED commit. So crash injection now lives in `faults.py` at the two boundaries where crashes really happen: `CrashingSink` (inside the receiver call) and `CrashAfterCommit`, a `Store` subclass that dies right after a named step's SUCCEEDED commit. The CLI wraps the store for `--crash-after`. |
| 11 | Input/output key collision. | `start` rejects input keys that equal a step name (`ValueError`). Cheaper than namespacing and it keeps `inputs["extract"]["title"]` readable in the tools. |
| 12 | `COMMIT` outside the `try`. | Inside; a failed commit rolls back and the connection is reusable. |

## Documentation

| # | Finding | Fix |
|---|---|---|
| 13 | DESIGN.md read like the minutes of a committee. | Rewritten in the first person: the three shapes I sketched (outbox+dispatcher, dedupe-only sink, kernel ledger) and why I chose the ledger. |
| 14 | Signatures and names in DESIGN.md drifted from the code. | `cancel -> str`, `Engine.__init__(store, registry, specs=None, sleep=...)`, `CrashingSink`/`CrashAfterCommit`, `run` resumes, pseudo-code shows the release rule and the `StoreError` re-raise, §10 lists the seven files and 23 tests by name. |
| 15 | README D9 sentence. | Now: every write that belongs to a workflow re-checks `owner` inside its transaction, with the race test named. |

## Readability

| # | Finding | Fix |
|---|---|---|
| 16 | `narrate` rebuilt 16 lambdas per event. | Module-level `_NARRATORS` dict built once. |
| 17 | `StepContext` as a frozen dataclass with injected callables. | Plain class: `StepContext(engine, wf_id, owner, step, attempt, tool)` with two methods. |
| 18 | Types and imports. | `rebuild_state(events: Iterable[Event])`; `specs=None` → own copy of `SPECS`; imports wrapped. |
| 19 | `NotifyTool.idempotent = sink.idempotent` conflates tool and receiver. | **Kept the name, fixed the contract:** the `Tool` protocol now says `idempotent` means "the receivers of this tool's effects dedupe on the key the kernel passes them; declared by the tool, a statement about the receiver". Renaming to `receiver_idempotent` would push a receiver concept into every pure tool's declaration for no gain. |
| 20 | `FileNotifier` re-read the file per call; `list_workflows` N+1; truncation with no marker. | Keys loaded once per process and kept in memory (like a provider's dedupe index); one SELECT; `…` marker. `calls` stays: the tests compare invocations to lines written. |
| 21 | The FAILED-at-entry branch and the interrupted-last-attempt path were unexplained. | One paragraph in README §Execution model and comments at both sites. No `ATTEMPT_FAILED` is written for an interrupted last attempt on purpose: the attempt was interrupted (already logged), not failed. |
| 22 | `demo.sh` `grep -c` under `set -e`. | Key read from `history --json`; `grep -c ... \|\| true`. Added demo §7 (operator errors). |

## Verified after the fixes

- `pytest`: 23 passed on Python 3.14 (`.venv`) and on Python 3.9.6 (`/usr/bin/python3` venv).
- `scripts/demo.sh`: clean on both interpreters.
- The take-over race, reproduced with two threads and two connections: A ends with
  `LostClaim`, B's `{"status": "duplicate"}` record stands, the last event is B's
  `OWNER_RELEASED`, one line in the receiver.
