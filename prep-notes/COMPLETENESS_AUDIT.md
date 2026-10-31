# Completeness audit against the brief (PRIVATE, not submitted)

> Oct 31 update: a second review pass (`PRINCIPAL_REVIEW.md`) added four fixes with tests (27 tests
> now), inline notes and a README roadmap; commits `38e305e..b51a529`, zip rebuilt. The mapping
> below (written Oct 30 at 23 tests) still holds; counts in it are historical.

Audited 2026-10-30 evening on a fresh `git clone` of the local repo. Every requirement in
`_private/brief.txt` (3,170 bytes, read line by line) is mapped to where it is satisfied and the
evidence that it works. Verification runs:

- `pytest -q`: **23 passed** on Python 3.14 (`.venv`) and on the system Python 3.9.6 (fresh venv
  `/tmp/v39`, pytest only).
- `scripts/demo.sh` with `PYTHON=/usr/bin/python3` (3.9) on the fresh clone: all seven sections
  complete, exit 0, two crash scenarios resume with exactly one notification each, replay
  `CONSISTENT`.
- `grep -rniE "claude|anthropic|chatgpt|openai|copilot|\bAI\b|LLM"` over every file that goes in
  the zip: no hits. A grep for the forbidden storage-unit wording (anything other than "50+ PB per year") over README, docs and code: no hits.

**Verdict: COMPLETE.** Every required item and every optional item (cancel, demo, time log,
section D) is present and exercised. One documentation drift was found and fixed
(DESIGN.md §0/D1 still carried the pre-self-review numbers: "five modules, five CLI verbs, six
tests, ~500-650 lines"; now "six modules, six verbs, 23 tests, ~1,300 lines"). No code defect
found.

## Requirement -> where -> evidence

### Problem statement

| Brief | Where | Evidence |
|---|---|---|
| Four-step document triage: Ingest, Extract, Classify, Notify | `wfk/spec.py` `TRIAGE_V1` (4 `StepSpec`s, in order); tools in `wfk/tools.py` | demo §1 `status` shows the four steps SUCCEEDED; `examples/doc.json` is the input |
| Each step modeled as a Tool with a clear interface | `wfk/tools.py` `Tool(Protocol)`: `name`, `side_effect_free`, `idempotent`, `run(ctx, inputs) -> dict`; typed errors `RetryableError` / `FatalError`; `Sink(Protocol)` for the receiver | `IngestTool`, `ExtractTool`, `ClassifyTool`, `NotifyTool` implement it; `faults.py` wrappers implement the same protocol, so the engine cannot tell them apart |

### 1. Execution model and state

| Brief | Where | Evidence |
|---|---|---|
| Declarative workflow spec (JSON/YAML or code-defined) | code-defined frozen dataclasses `WorkflowSpec` / `StepSpec` (`spec.py`); JSON loader `load_spec_json`; CLI `--spec name@version` or `--spec file.json` | `test_spec_file_round_trips_through_start_and_run`; `test_operator_errors_exit_cleanly` (malformed spec -> exit 5) |
| State machine PENDING -> RUNNING -> SUCCEEDED \| FAILED | `store.py` `WF_ALLOWED` / `STEP_ALLOWED` set literals; `transition_workflow` / `transition_step` refuse any other pair before writing; the replay reducer applies the same sets | `test_illegal_transition_raises_and_writes_nothing`, `test_replay_rejects_illegal_log`. Additions beyond the brief: `CANCELLED` (cancel is optional in the brief, implemented) and `SKIPPED` (step-level failure policy), both explained in README §Execution model |
| Retries with max attempts | `StepSpec.max_attempts` (>= 1 enforced in `__post_init__`); `Engine._run_step` retry ladder; exponential backoff `min(base * 2^(attempt-1), 2 s)` persisted as `next_run_at` | `test_retry_then_success` (attempt 3, delays `[0.05, 0.1]`, earlier step untouched); `test_exhaustion_fails_workflow` (classify `max_attempts=2` -> FAILED `exhausted`); demo §2 |
| Step-level failure handling | `StepSpec.on_failure = "fail_workflow" \| "skip"`; `Engine._give_up`; `FatalError` vs retryable; interrupted-last-attempt path | `test_skip_policy_lets_workflow_succeed` (notify SKIPPED after 5, workflow SUCCEEDED); `test_fatal_error_fails_without_retry`; `test_exhaustion_fails_workflow` (later steps never start) |
| Cancellation (optional) | `Engine.cancel` (cooperative flag, honoured between attempts; PENDING+unowned cancels now; terminal refuses); CLI `cancel` | `test_cancel_between_attempts`, `test_cancel_before_first_run`; demo §6 |

### 2. Durability and idempotency

| Brief | Where | Evidence |
|---|---|---|
| Persist workflow + step state | SQLite (`store.py`): `workflows`, `steps`, `effects`, `events`; WAL + `synchronous=FULL`; every change one `BEGIN IMMEDIATE` transaction that updates a row and appends its events | `status` is one SELECT per table; `test_replay_rebuild_equals_tables` proves tables == log after retry + crash + resume |
| Safe resume after restart | `Engine.run` is a function of (database, registry); claim/owner fence; `ATTEMPT_INTERRUPTED` path for a step found RUNNING; SUCCEEDED steps never re-run (`output_json` on disk); `next_run_at` honoured across restart; FAILED-at-entry and interrupted-last-attempt paths | `test_restart_resumes_without_rerunning` (counters 1/1/1/0 then 0/0/0/1), `test_restart_during_backoff_waits_the_remainder`, `test_claim_blocks_second_owner`, `test_fenced_out_engine_cannot_finish_the_effect`, `test_stale_owner_cannot_commit`; demo §3 (`--crash-after classify`, `os._exit`, `run --take-over`) |
| No duplicate side effects (especially notify) | Effects ledger: `StepContext.run_effect` -> `Engine._run_effect`; key `sha256(workflow:step:effect)` with no attempt number; CLAIMED -> call -> DONE; DONE row returns stored result; CLAIMED-not-DONE -> `EFFECT_AMBIGUOUS` + tool's declared idempotency decides (resend same key / fail `ambiguous_effect`); ledger writes owner-fenced | `test_notify_effect_once_across_crash` (one line after crash in notify + resume), `test_ambiguous_non_idempotent_fails_safely` (receiver called once ever), `test_done_effect_is_not_resent_on_resume` (C5), `test_stale_owner_cannot_write_ledger_or_events`; demo §4 |

### 3. Auditability

| Brief | Where | Evidence |
|---|---|---|
| History sufficient to track state transitions | `events` table, append-only (no UPDATE/DELETE statement in `store.py`); 16 event types; every transition writes `WORKFLOW_STATE` / `ATTEMPT_STARTED` / `STEP_*` with `from`/`to`/`reason` | `history <id>` narrates every event; `replay` folds them back into state |
| Query decisions made during execution | `DECISION` events via `ctx.record_decision` (classify writes label/confidence/rule); `RETRY_SCHEDULED` / `STEP_SKIPPED` / `EFFECT_AMBIGUOUS` carry their reasons; `history <id> --kind DECISION [--json]` | demo §5; `test_replay_rebuild_equals_tables` asserts `DECISION` and `EFFECT_AMBIGUOUS` in the replay output |
| Way to inspect history (CLI sufficient) | CLI `history` (narrated or JSON, `--kind` filter) and `replay` (narrative + derived state + consistency diff, exit 1 on mismatch) | `test_replay_detects_tampered_table` (hand-edited row -> exit 1) |

### 4. Interfaces and abstractions

| Brief | Where | Evidence |
|---|---|---|
| Tool interfaces | `tools.py` `Tool` and `Sink` protocols; `engine.py` `StepContext` (identity + `record_decision` + `run_effect`; never the store) | `TYPE_CHECKING`-only import of `StepContext` in `tools.py`: tools depend on the interface, not the engine |
| Workflow structure | `WorkflowSpec(name, version, steps)`; registry `{tool_name: Tool}` assembled by the caller; `SPECS[(name, version)]` | `Engine._check_tools` refuses a spec whose tools are missing, before claiming |
| Separation orchestration vs step/tool execution | `engine.py` imports no concrete tool and nothing from `faults.py`; `tools.py` never imports the store; `cli.py` wires both | `grep -n "import" wfk/engine.py` shows only `spec`, `store`, `tools` (protocol + errors); README §Architecture |

### 5. CLI

| Brief | Where | Evidence |
|---|---|---|
| start | `cmd_start` (create + run; `--no-run`, `--id`, `--spec`, fault flags) | `test_operator_errors_exit_cleanly`, demo §1 |
| status | `cmd_status` (one workflow or all; steps, attempts, `next_run_at`, effects) | demo §1, §3, §4 |
| cancel | `cmd_cancel` (prints what will happen: CANCELLED now, or flag honoured by the owner / next run) | demo §6 |
| replay | `cmd_replay` (read-only; exit 1 when log and tables disagree) | `test_replay_*`, demo §5 |
| extras | `run [--take-over]` (resume), `history [--kind] [--json]`; exit codes 0-6 documented in `cli.py` and README | `test_operator_errors_exit_cleanly`, demo §7 |

### 6. Testing

| Brief | Where | Evidence |
|---|---|---|
| One test covering retry OR idempotency | both: `tests/test_retry.py`, `tests/test_idempotency.py` (3 tests) | 23 tests, 7 files, ~0.1 s, fake clock, temp SQLite per test |
| Demonstrate restart safety | automated: `tests/test_restart.py` (4 tests) and `test_idempotency.py`; demonstrated: demo §3 and §4 with a real `os._exit(1)` and a second process; described: README §Execution model, DESIGN §6 and §5.2 (C0-C7) | crash injection only at real boundaries (`CrashingSink`, `CrashAfterCommit`), no hooks in the kernel |

### Required: Architecture and design

| Brief | Where | Evidence |
|---|---|---|
| A. Scaling: 10k+ concurrent, multiple services; state ownership, execution model, failure boundaries | README §Design A (sharding by workflow id, Postgres with partitioned `events`, queue + stateless workers, lease with fencing token `WHERE lease_token = ?`, tools behind RPC with `Idempotency-Key`, per-boundary failure handling, queue as accelerator not truth); DESIGN §11 (+ hot spots) | sized: a few hundred transitions/s, ~50 events per workflow |
| B. Failure semantics: one concrete scenario, what goes wrong, how handled, tradeoffs | README §Design B: crash during the notify side effect (C4), plus retry after partial success; DESIGN §12 | matches `test_notify_effect_once_across_crash` and demo §4 line for line |
| C. Abstractions and versioning: evolution, not breaking running workflows | README §Design C (pin `spec_version` at start, immutable published versions, `tool@version`, additive outputs, drain rule, explicit `MIGRATED` event, engine = f(database, registry)); DESIGN §13 | `Engine.start` refuses to re-register a key with a different body; `_spec_for` resolves the pinned version |
| D. (Optional) multi-tenancy or compliance | README §Design D: both (tenant column + key prefix + per-tenant Store/limits; append-only audit, replay as tamper check, PII redaction/retention/hash chain); DESIGN §14 | described, honestly marked not built |

### Deliverables

| Brief | Where | Evidence |
|---|---|---|
| Code repository: implementation, minimal tests, CLI | `wfk/`, `tests/`, `python -m wfk`; `requirements.txt`, `pyproject.toml`, `.gitignore`, `examples/doc.json` | fresh clone runs with stdlib only; pytest is the only dependency |
| README: architecture overview, execution model, idempotency approach, how to run, design section | README.md sections in that order: How to run, Demo, Architecture overview, Execution model, Idempotency approach, What the tests prove, Design A-D, Time log | 23 KB, every claim cross-referenced to a test or demo step |
| Demo (optional): a run; retry OR restart safety | `scripts/demo.sh`: clean run, retry, crash between steps + resume, crash inside notify + resume, decisions + replay, cancel, operator errors | runs in ~2 s in a temp dir on 3.9 and 3.14 |
| Time log (optional): where time went, what was simplified | README §Time log (~6 h by phase) + "Intentionally simplified / cut" list; DESIGN §15 | |

## Known limitations (deliberate, documented; good pushback material)

1. **A transient failure inside the receiver call for a non-idempotent tool is treated as
   ambiguity on the next attempt**, so retries of a non-idempotent effect fail the step with
   `ambiguous_effect` even when the failure was clearly "connection refused". This is the honest
   at-most-once stance (the kernel cannot distinguish "never sent" from "sent, reply lost"); a
   typed `NotSent` signal from the receiver would let it retry safely. Documented in DESIGN
   C3 and README §Design A ("RetryableError before the claim, or ambiguity after it").
2. **Claim without TTL**: a dead owner needs `run --take-over`. Chosen over TTL stealing because
   stealing without a fencing token at commit lets two owners commit the same attempt; the fleet
   design adds the token. README §Execution model step 1.
3. **`idempotent` is declared, not verified**; the RPC boundary with `Idempotency-Key` in the fleet
   design is where it becomes enforceable. README §Design A, presentation tradeoffs table.
4. **Cooperative cancel** waits for a running tool. README cut list.
5. **JSON spec files are per-process**: `run` needs the same `--spec` flag (the registry model;
   persisting the spec body was rejected in the self-review). `test_spec_file_round_trips...`.

## What Ganesh must decide or provide

Nothing beyond review and submission:

1. Read README.md and docs/DESIGN.md once; skim `wfk/engine.py` `_run_effect` and `_run_step`.
2. Run `.venv/bin/python -m pytest -q` and `scripts/demo.sh` once on his machine.
3. Submit `dist/ganesh_muthuluru_trase.zip` via the Greenhouse link before Sun Nov 1 ~3:18 PM PT.

Nothing in the zip needs his words, credentials or an attestation. The GitHub repo
(`discordcreater/trase-project`, private) is a convenience for the live round and is not part of
the submission; the zip is.
