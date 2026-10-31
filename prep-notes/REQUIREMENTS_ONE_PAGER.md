# Trase take-home: requirements on one page (PRIVATE, not submitted)

Source: `_private/brief.txt` ("Technical Take-Home (3-5 hours): Durable Workflow Kernel + Architecture Deep Dive"), read line by line on Oct 31 2026. The brief's own priority order: "clarity of thinking and correctness over feature completeness".

## A. Explicit requirements (must be visibly satisfied)

| # | Brief says | What a reviewer will look for |
|---|---|---|
| R1 | Four-step document triage: Ingest, Extract, Classify, Notify | a spec naming the four steps in order; one Tool per step |
| R2 | Each step modeled as a Tool with a clear interface | one interface (`run(ctx, inputs) -> dict`), typed errors, no step reaching into the engine or the DB |
| R3 | Declarative workflow spec (JSON/YAML or code-defined) | data, not code paths; versioned; loadable from a file is a plus |
| R4 | State machine `PENDING -> RUNNING -> SUCCEEDED \| FAILED` | the machine written down in one place and enforced on every write; extra states only if justified |
| R5 | Retries with max attempts | per-step budget; attempt counter persisted; backoff; the budget survives a restart |
| R6 | Step-level failure handling | something distinct from retries: a per-step policy (fail the workflow vs continue), fatal vs transient |
| R7 | Cancellation (optional) | a `cancel` verb that does something defensible; semantics stated (cooperative vs preemptive) |
| R8 | Persist workflow + step state | a real store (SQLite is fine); every transition durable before the next action |
| R9 | Safe resume after restart | a new process on the same DB finishes the job; completed steps not re-run; interrupted step handled explicitly |
| R10 | No duplicate side effects, especially notify | an idempotency mechanism with a stable key; the crash window named and handled, not hand-waved |
| R11 | Execution history: state transitions + decisions made | an append-only log written in the same unit of work as the state change; decisions queryable by type |
| R12 | A way to inspect history (CLI is enough) | `history`/`replay` output that a human can read |
| R13 | Tool interfaces and workflow structure defined; orchestration separated from execution | engine imports no concrete tool; tools never touch persistence |
| R14 | CLI: start, status, cancel, replay | the four verbs exist; `replay` has a defined meaning that cannot re-fire side effects |
| R15 | One test covering retry OR idempotency | at least one; both is better; tests that inject failure at a realistic boundary |
| R16 | Restart safety demonstrated (or described) | an automated crash+resume test or a scripted demo; a description at minimum |
| R17 | README: architecture overview, execution model, idempotency approach, how to run, design sections | each heading present and accurate to the code |
| R18 | Design A: 10k+ concurrent workflows, multiple services; state ownership, execution model, failure boundaries | sharding/ownership, queue + workers, leases/fencing, what fails independently |
| R19 | Design B: one concrete failure scenario; what goes wrong, how handled, tradeoffs | crash during the notify side effect or retry after partial success, tied to the code |
| R20 | Design C: evolving workflows/tools without breaking running workflows | version pinning at start, immutable published versions, additive outputs, draining |
| R21 | Design D (optional): multi-tenancy or compliance | isolation strategy or audit/compliance considerations |
| R22 | Deliverables: repo (implementation, minimal tests, CLI), README; optional demo and time log | runs from a fresh checkout with documented commands |

## B. Implicit expectations a Trase reviewer will hold (Senior/Staff, platform and execution model)

- **Correctness reasoning is written down.** Every "it cannot happen twice" claim is backed by a test or a named crash point. Guarantees are stated precisely (at-least-once vs exactly-once) and the limits are admitted.
- **Failure semantics are the product.** The interesting path is the unhappy one: crash between steps, crash inside a side effect, retry after partial success, a stale process writing after a take-over, a restart during a backoff.
- **Abstractions are few and load-bearing.** One Tool interface, one Store boundary, one Engine loop; no framework, no plugin system, no speculative generality.
- **Operability.** Clear CLI messages and exit codes, no tracebacks for operator errors, a status view that shows the next action (`--take-over`), an audit log that answers "why".
- **Scope judgment for 3-5 hours.** Standard library, one file database, a handful of modules; the things not built are named with a reason and a path.
- **Honest narrative.** README and design doc match the code; the time log is believable; simplifications are called simplifications.
- **Code that can be changed live.** The follow-up asks for a new requirement coded without AI: the code must be small enough to hold in one head, with obvious seams (add a step, add a policy, add a tool).

## C. Evaluation criteria (how to read the grade)

1. Correctness under failure (R5, R8-R10, R16): does the kernel survive the crash points, and does it know when it cannot tell? Weight: highest.
2. Durable execution semantics (R8, R9, R11): is the store the only state, is every change durable and explained, can state be rebuilt from the log?
3. Clean abstractions (R2, R3, R13): are the interfaces minimal, typed, and separated as the brief asks?
4. System design reasoning (R18-R21): do the design sections reflect what the code actually does and where it stops?
5. Communication (R12, R17, demo, time log): can a reviewer run it, read it and believe it in 20 minutes?
