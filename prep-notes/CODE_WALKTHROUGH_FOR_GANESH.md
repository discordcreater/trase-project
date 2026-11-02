# `wfk` annotated code walkthrough (private; NOT in the submission)

For: Ganesh, before the Trase follow-up. Written Oct 30, 2026 against the canonical code in
`~/claude-code-ws/trase-project` (every quote below is verbatim from that repo, with the file and
line numbers as of the Oct 31 evening pass, commit `080c8f0`; the drill code in §6 was built and tested in a scratch copy, never
in the canonical repo).

**Nov 2 update:** code comments and docstrings were shortened (no behaviour change). Every quoted
code block, `file:line` anchor and "(lines N–M)" range in §3 and the §8-§10 tables was remapped to the new
line numbers; the prose in §9/§10 describing what a comment says still describes its original, longer
wording. See `HUMANIZE_PASS.md`.

The follow-up (Adam Elconin, Oct 28 call and his email) is: *what were your decisions, how did you
leverage AI, then "here's a new requirement, build it", live, no AI, no search.* He said he has seen
good projects whose authors "don't own the code as much or the decisions". This document exists so
that you own every line and every decision.

---

## 1. Read this first

1. **Mental model, four words: Engine decides, Store remembers, Tool does.** `engine.py` chooses
   what runs next and never does work; `store.py` is the only file that touches SQLite; `tools.py`
   does the work and never sees the database. `spec.py` says which tools run in which order;
   `cli.py` wires it all; `faults.py` injects crashes for tests and the demo (the kernel never
   imports it).
2. **The guarantee, one sentence (memorize it, it is in the README verbatim):** at-least-once
   invocation of an effect, exactly-once recording of its outcome, exactly-once *effect* iff the
   receiver honours the idempotency key; when a crash makes a non-idempotent effect's outcome
   unknowable, the kernel records `EFFECT_AMBIGUOUS`, fails the step, and stops. Never guesses.
3. **The three decisions to defend:** (D2) every state change is one SQLite transaction that
   updates a row *and* appends the events explaining it, so tables and log cannot disagree;
   (D6/D7) side effects go through a ledger keyed by `sha256(workflow:step:effect)` with no attempt
   number, and an ambiguous claim is recorded, then resolved by the tool's declared idempotency;
   (D9) a claim with an owner fence re-checked inside every write, instead of a TTL lease.
4. **Numbers:** 7 modules, ~1,300 lines; 4 tables; 16 event types; 5 workflow edges, 7 step
   edges; 6 CLI verbs; 7 exit codes; 31 tests in 7 files (<1 s); a clean run writes exactly 16 events.
5. **Read the repo in 90 minutes, in this order** (the order §3 follows): `spec.py` (10 min) →
   `store.py` top half, schema and ALLOWED sets (15) → `store.py` bottom half, transitions and
   ledger (15) → `engine.py` `Engine.run`/`_run_owned`/`_run_step` (20) → `engine.py` `_run_effect`
   and `rebuild_state` (10) → `tools.py` (10) → `faults.py` + `cli.py` (10). Then run
   `scripts/demo.sh` and read `history` output with §4 open.
6. Keep a terminal open on the repo while reading: `python3 -m wfk --db /tmp/w.db start examples/doc.json --id a`
   then `python3 -m wfk --db /tmp/w.db history a`. Seeing the 16 events once beats reading twice.
7. You are a Java engineer: §2 maps every Python construct this code uses to its Java equivalent.
   There is nothing exotic here; the hardest Python in the repo is a `@contextmanager` generator.
8. Everything in §6 (drills) was built and run against the real test suite in a scratch copy; the
   diffs are exact. Practice them by typing, not reading.
9. If you only have 30 minutes: §1, §4 (the traces), §8 (the cheat card), then type drill 6.1.
10. **Oct 31 review pass:** four fixes and fifteen comments went in after this document was first
    written; §9 lists each one with the sentence to say about it. Line numbers above are updated.

---

## 2. Python for a Java engineer: only what this code uses

| Python in `wfk` | Java equivalent | Notes |
|---|---|---|
| `@dataclass(frozen=True) class StepSpec: name: str; max_attempts: int = 3` | `record StepSpec(String name, int maxAttempts)` (immutable; auto `equals`/`hashCode`/`toString`) | `frozen=True` = no setters; assignment raises. `__post_init__` is the compact constructor body for validation. Field defaults replace builder/overloads. `StepSpec(**s)` = construct from a `Map<String,Object>` by keyword (a `TypeError` if a key is unknown, which `load_spec_json` turns into `ValueError`). |
| `@dataclass class Projection: workflows: Dict[...] = field(default_factory=dict)` | mutable POJO with `new HashMap<>()` initializers | `field(default_factory=...)` is needed because a plain `= {}` default would be shared across instances (Python evaluates defaults once). |
| `class Tool(Protocol): name: str; def run(self, ctx, inputs) -> Dict: ...` | `interface Tool { String name(); Map run(StepContext ctx, Map inputs); }` | **Structural**, not nominal: `IngestTool` never writes `implements Tool`; having the attributes and `run` is enough (duck typing, checked by mypy only). Same for `Sink`. |
| `class RetryableError(Exception): pass` / `class SimulatedCrash(BaseException)` | `class RetryableError extends RuntimeException {}` / `extends Throwable` but **not** `Exception` (think `Error`) | `except Exception` does NOT catch a `BaseException` subclass; that is why `SimulatedCrash` behaves like `kill -9` through the engine's `except Exception` handlers. `KeyboardInterrupt`/`SystemExit` are also `BaseException`. |
| `try: ... except FatalError as e: ... except StoreError: raise ... except Exception as e:` | `try/catch` chain, most specific first; `raise` alone = `throw e;` rethrow | Order matters exactly as in Java. `raise X from None` suppresses the chained cause (like `new X()` without `initCause`). |
| `@contextmanager def _tx(self): BEGIN; try: yield; COMMIT; except BaseException: ROLLBACK; raise` then `with self._tx(): ...` | `try (Tx tx = store.begin()) { ... }` try-with-resources, where the close commits or rolls back | The code before `yield` runs on entry, the code after on exit; an exception inside the `with` body resumes at `yield` as a raise. `with open(path) as f:` is the same idea for files (auto-close). |
| `sqlite3.connect(path, isolation_level=None)`; `conn.execute("BEGIN IMMEDIATE")` / `"COMMIT"` / `"ROLLBACK"` | JDBC `setAutoCommit(false)` is the opposite default; here `isolation_level=None` = autocommit unless you write `BEGIN` yourself | `BEGIN IMMEDIATE` takes the write lock at BEGIN (Java: `SELECT ... FOR UPDATE` on the whole DB). `cur.rowcount` = `executeUpdate()` return value. `?` placeholders = `PreparedStatement` params. `conn.row_factory = sqlite3.Row` makes rows indexable by column name like `ResultSet.getString("owner")`. `executemany` = `addBatch`/`executeBatch`. `executescript` runs several statements (schema). |
| `Optional[str]`, `Dict[str, Any]`, `Tuple[str, int]`, `Callable[[float], None]`, `Sequence[...]` | `@Nullable String`, `Map<String,Object>`, a 2-tuple record, `Consumer<Double>`, `List<...>` | Type hints only; nothing is enforced at runtime. `from __future__ import annotations` makes hints lazy strings (so `"StepContext"` forward refs and 3.9 compatibility work). `TYPE_CHECKING` guards an import used only by hints (breaks an import cycle). |
| `f"workflow {wf_id} is owned by {owner}"`; `f"{s.name:<10}"`; `{e.key[:12]}` | `String.format("workflow %s ...")`; `%-10s`; `substring(0,12)` | `[:12]` slicing never throws on short strings. `{x!r}` = `repr(x)` (quoted). |
| `if __name__ == "__main__"` is not used; instead `wfk/__main__.py` does `sys.exit(main())` | `public static void main` + `System.exit(code)` | `python -m wfk` runs `wfk/__main__.py`. `os._exit(1)` = `Runtime.halt()`: no finally blocks, no cleanup (used to fake kill -9). |
| `(frm, to) not in WF_ALLOWED` where `WF_ALLOWED = {("PENDING","RUNNING"), ...}` | `Set<Pair<State,State>>` of allowed edges, `contains` | A set literal of tuples; the whole state machine is data. |
| `*fields.values()` / `**fields: Any` / `[f"{k} = ?" for k in fields]` | varargs / a `Map<String,Object>` of named args / a stream-map-collect | `**fields` collects extra keyword args (`attempt=1, error=None`) into a dict; `*xs` spreads a sequence into positional args. List comprehensions are `stream().map().collect()`. |
| `for i, (name, max_attempts) in enumerate(steps)` | indexed for-each with destructuring | tuple unpacking everywhere: `name, _, ver = s.partition("@")`. |
| `json.dumps(obj, sort_keys=True, separators=(",", ":"))` / `json.loads` | Jackson `writeValueAsString` with sorted keys / `readValue` | `sort_keys=True` makes stored JSON canonical (comparable byte-for-byte). |
| `hashlib.sha256(s.encode()).hexdigest()`; `uuid.uuid4().hex[:8]` | `MessageDigest.getInstance("SHA-256")` + hex; `UUID.randomUUID()` | |
| `datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="microseconds")` | `Instant.ofEpochSecond(...).toString()` (ISO-8601 UTC) | Stored as TEXT; sortable because fixed width. |
| `self.name, self.side_effect_free, self.idempotent = ...` (class-level tuple assignment) | `static final` fields on the class | `name, side_effect_free, idempotent = "ingest", True, True` at class level defines three class attributes at once. |
| `lambda p: f"..."` in `_NARRATORS` dict | `Map<String, Function<Map, String>>` | `_NARRATORS.get(e.type, _j)` = `getOrDefault`. |
| `argparse` subparsers with `set_defaults(fn=cmd_start)` | picocli subcommands; the handler stored on the parsed args | `a.fn(a, store)` dispatches without an if-chain. |
| `with pytest.raises(SimulatedCrash): engine1.run(wf_id)` | `assertThrows(SimulatedCrash.class, () -> ...)` | pytest fixtures (`make_engine`, `clock`) are injected by parameter name, like JUnit 5 parameter resolvers. `tmp_path` is a per-test temp dir. |

Idioms to recognise: `x or default` (null-coalescing, also treats `""`/`0`/`[]` as missing);
`row.result or {}`; `if not isinstance(input, dict)`; `getattr(a, "crash_after", None)`
(reflection with default); `dict(SPECS)` (shallow copy); `{**SPECS, spec.key: spec}` (copy plus
one entry); `sorted(set(a) - set(b))` (set difference); `t.split("_")[1]` on `"STEP_FAILED"` gives
`"FAILED"`.

---

## 3. File-by-file walkthrough (reading order)

Convention for annotations: **what** it does / **why** it is here / **if removed**, what breaks.

### 3.1 `wfk/spec.py` (63 lines): the declarative workflow definition

Responsibility: say which steps run, with which tool, how many attempts, what happens on failure.
Immutable; a workflow pins the version it started with.

```python
ON_FAILURE = ("fail_workflow", "skip")


@dataclass(frozen=True)
class StepSpec:
    name: str
    tool: str                          # registry key
    max_attempts: int = 3
    on_failure: str = "fail_workflow"  # or "skip"
    backoff_base_s: float = 0.05       # delay = min(base * 2**(attempt-1), Engine.BACKOFF_CAP_S)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError(f"step {self.name}: max_attempts must be >= 1")
        if self.on_failure not in ON_FAILURE:
            raise ValueError(f"step {self.name}: on_failure must be one of {ON_FAILURE}")
```
(lines 11–26)

- `name` is the step's identity in the `steps` table and in `inputs[step_name]`; `tool` is a key
  into the registry dict (so one tool can serve two steps, and a step can be re-bound without
  touching the tool). **Why separate:** orchestration references tools by name, never by class.
- `max_attempts` counts attempts *started* (an interrupted attempt is charged). `on_failure` is the
  step-level failure policy (D5). `backoff_base_s` is the only retry tuning knob.
- `__post_init__` validates at construction: a bad spec fails at import time, not at run time.
  **If removed:** `max_attempts=0` would make `_run_step` give up immediately with an "interrupted"
  message that lies.

```python
@dataclass(frozen=True)
class WorkflowSpec:
    name: str
    version: int
    steps: Tuple[StepSpec, ...]

    def __post_init__(self) -> None:
        names = [s.name for s in self.steps]
        if len(names) != len(set(names)):
            raise ValueError(f"spec {self.name}: duplicate step names")

    @property
    def key(self) -> Tuple[str, int]:
        return (self.name, self.version)
```
(lines 29–42)

- `steps` is a tuple (immutable, hashable) so `WorkflowSpec` is comparable by value: `Engine.start`
  uses `registered != spec` to refuse re-registering a version with a different body.
- Duplicate step names would collide on the `steps` primary key `(workflow_id, name)` and on
  `inputs[name]`. `key` is the registry key `("document_triage", 1)`.

```python
TRIAGE_V1 = WorkflowSpec("document_triage", 1, (
    StepSpec("ingest", "ingest", max_attempts=3),
    StepSpec("extract", "extract", max_attempts=3),
    StepSpec("classify", "classify", max_attempts=2),
    StepSpec("notify", "notify", max_attempts=5, on_failure="skip"),
))

SPECS: Dict[Tuple[str, int], WorkflowSpec] = {TRIAGE_V1.key: TRIAGE_V1}
```
(lines 45–52)

- The brief's workflow. Notify gets 5 attempts and `skip`: a dead mail provider must not fail a
  triage that already classified. Classify gets 2 because it is pure and cheap; if it fails twice
  something is wrong with the input, not the network.
- `SPECS` is the published registry; `Engine` copies it (`dict(SPECS)`) so `start()` can register a
  file spec for one process without mutating the module.

```python
def load_spec_json(path: str) -> WorkflowSpec:
    """{"name": ..., "version": 1, "steps": [{"name", "tool", "max_attempts"?, "on_failure"?}]}"""
    with open(path) as f:
        raw = json.load(f)
    try:
        steps = tuple(StepSpec(**s) for s in raw["steps"])
        return WorkflowSpec(raw["name"], int(raw["version"]), steps)
    except (KeyError, TypeError) as e:
        raise ValueError(f"{path}: malformed spec: {e}") from None
```
(lines 55–63)

- `StepSpec(**s)` constructs from the JSON object by keyword; an unknown key (`"nme"`) is a
  `TypeError`, a missing `"steps"` a `KeyError`; both become one `ValueError` the CLI maps to exit 5
  (`test_operator_errors_exit_cleanly`). `from None` hides the noisy chained traceback.

### 3.2 `wfk/store.py` (431 lines): persistence, the only SQLite code

Responsibility: the schema, the two state machines as data, and every write as one fenced
transaction that also appends the events explaining it.

#### The state machines (lines 19–33)

```python
WF_ALLOWED = {
    ("PENDING", "RUNNING"), ("PENDING", "CANCELLED"),
    ("RUNNING", "SUCCEEDED"), ("RUNNING", "FAILED"), ("RUNNING", "CANCELLED"),
}
STEP_ALLOWED = {
    ("PENDING", "RUNNING"),
    ("RUNNING", "SUCCEEDED"), ("RUNNING", "PENDING"),      # PENDING = retry or interrupted
    ("RUNNING", "FAILED"), ("RUNNING", "SKIPPED"),
    ("PENDING", "FAILED"), ("PENDING", "SKIPPED"),         # interrupted on its last attempt
}
WF_TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}
STEP_TERMINAL = {"SUCCEEDED", "FAILED", "SKIPPED"}

# Columns a step transition may set, besides ``state``.
STEP_FIELDS = {"attempt", "output_json", "error", "next_run_at"}
```

Every allowed transition, with who triggers it and the event written in the same transaction:

| Edge | Triggered by | Event(s) |
|---|---|---|
| workflow `PENDING → RUNNING` | `_run_owned` on first run | `WORKFLOW_STATE{started}` |
| workflow `PENDING → CANCELLED` | `Engine.cancel` when unowned and never run | `WORKFLOW_STATE{cancelled before first run}` |
| workflow `RUNNING → SUCCEEDED` | `_finish` when all steps SUCCEEDED/SKIPPED | `WORKFLOW_STATE` |
| workflow `RUNNING → FAILED` | `_finish` after a `fail_workflow` step failed (or found FAILED on resume) | `WORKFLOW_STATE` |
| workflow `RUNNING → CANCELLED` | `_finish` when `_run_step` saw the cancel flag between attempts | `WORKFLOW_STATE` |
| step `PENDING → RUNNING` | `_run_step`, attempt += 1 | `ATTEMPT_STARTED` |
| step `RUNNING → SUCCEEDED` | tool returned | `STEP_SUCCEEDED{output}` |
| step `RUNNING → PENDING` | retryable error with budget left, **or** found RUNNING on resume | `ATTEMPT_FAILED` + `RETRY_SCHEDULED`, or `ATTEMPT_INTERRUPTED` |
| step `RUNNING → FAILED` | `FatalError`, exhausted, or `AmbiguousEffect`, with `on_failure=fail_workflow` | `ATTEMPT_FAILED` + `STEP_FAILED{reason}` |
| step `RUNNING → SKIPPED` | same causes, `on_failure=skip` | `ATTEMPT_FAILED` + `STEP_SKIPPED{policy}` |
| step `PENDING → FAILED` / `PENDING → SKIPPED` | resume found the step PENDING with `attempt >= max_attempts` (interrupted on its last attempt) | `STEP_FAILED`/`STEP_SKIPPED` only, no `ATTEMPT_FAILED` |

**Why a set of tuples:** `transition_*` refuses anything not listed (`IllegalTransition`, nothing
written), and `rebuild_state` applies the *same* sets on read, so one bug cannot corrupt a row
silently and the replay reducer is not a second copy of the machine. **If removed:** any string
could be written to `state`; the "a bug cannot silently corrupt a row" claim dies.
`STEP_FIELDS` whitelists the columns `transition_step(**fields)` may set, because the column names
are interpolated into SQL (`f"{k} = ?"`): the whitelist is what keeps that from being injection.

#### Errors (lines 36–61)

```python
class StoreError(Exception):
    """Base class for every error the store raises."""


class NotFound(StoreError):
    pass


class AlreadyExists(StoreError):
    pass


class IllegalTransition(StoreError):
    """(from, to) is not in the state machine. Nothing was written."""


class LostClaim(StoreError):
    """The workflow's owner or the row's state changed underneath us. Rolled back."""


class AlreadyOwned(StoreError):
    """Another engine holds the claim on this workflow."""

    def __init__(self, workflow_id: str, owner: Optional[str]) -> None:
        super().__init__(f"workflow {workflow_id} is owned by {owner}")
        self.owner = owner
```

- One base class so the CLI maps "store refused" to exit 4 in one `except StoreError`. The engine's
  `_run_step` has `except StoreError: raise` so a kernel failure inside `ctx.*` is never mistaken for
  a tool failure and retried. `AlreadyOwned` carries `owner` so the CLI can print who holds it.

#### Schema (lines 64–110)

```sql
CREATE TABLE IF NOT EXISTS workflows (
  id               TEXT PRIMARY KEY,
  spec_name        TEXT NOT NULL,
  spec_version     INTEGER NOT NULL,
  input_json       TEXT NOT NULL,
  state            TEXT NOT NULL,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  owner            TEXT,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);
```
`workflows`: one row per execution. `spec_name`+`spec_version` pin the spec (D13 versioning: the
engine resolves the spec the workflow started with, forever). `input_json` is the original input,
re-read on every attempt (never from memory). `cancel_requested` is a flag, not a state: anyone may
set it; only the owning engine acts on it. `owner` is the claim token (`NULL` = free).

```sql
CREATE TABLE IF NOT EXISTS steps (
  workflow_id  TEXT NOT NULL REFERENCES workflows(id),
  name         TEXT NOT NULL,
  idx          INTEGER NOT NULL,
  state        TEXT NOT NULL,
  attempt      INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL,
  output_json  TEXT,
  error        TEXT,
  next_run_at  TEXT,
  updated_at   TEXT NOT NULL,
  PRIMARY KEY (workflow_id, name)
);
```
`steps`: one row per step per workflow, created all-PENDING at `start`. `attempt` counts attempts
started; `max_attempts` is copied from the spec at creation (so `status` needs no spec).
`output_json` is set on SUCCEEDED and is what later steps read as `inputs[name]`; this is why
succeeded steps are never re-run. `error` is the last error. `next_run_at` is the persisted backoff
deadline (NULL = runnable now); it is what lets a restart during a backoff wait only the remainder.

```sql
CREATE TABLE IF NOT EXISTS events (
  seq          INTEGER PRIMARY KEY AUTOINCREMENT,
  workflow_id  TEXT NOT NULL REFERENCES workflows(id),
  step_name    TEXT,
  type         TEXT NOT NULL,
  attempt      INTEGER,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_workflow ON events(workflow_id, seq);
```
`events`: append-only (no UPDATE/DELETE statement for it exists anywhere in `store.py`; say that
sentence in the interview). `seq` is the global order; `step_name` NULL = workflow-level event;
`attempt` = which attempt wrote it (after a resume, `EFFECT_DONE` carries attempt 2 while
`EFFECT_CLAIMED` carries 1, and the log says so). The index makes `history`/`replay` one range scan.

```sql
CREATE TABLE IF NOT EXISTS effects (
  key          TEXT PRIMARY KEY,
  workflow_id  TEXT NOT NULL REFERENCES workflows(id),
  step_name    TEXT NOT NULL,
  effect_name  TEXT NOT NULL,
  attempt      INTEGER NOT NULL,
  status       TEXT NOT NULL,
  result_json  TEXT,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);
```
`effects`: the side-effect ledger. `key` = `sha256(workflow_id:step_name:effect_name)` (the primary
key makes a second CLAIM for the same key impossible). `status` is `CLAIMED` or `DONE`; `result_json`
is set on DONE and returned on re-entry without calling the receiver. `attempt` is the attempt that
claimed (informational).

#### Helpers and row types (lines 113–175)

```python
def to_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="microseconds")


def from_iso(s: str) -> float:
    return datetime.fromisoformat(s).timestamp()


def dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))
```
- All timestamps flow through `store.now()` → `to_iso(self.clock())`, so tests inject a `FakeClock`
  and backoff takes no real time. `dumps` is canonical JSON (sorted keys, no spaces) so stored values
  are byte-comparable.

`WorkflowRow`, `StepRow`, `EffectRow`, `Event` (lines 125–171) are frozen dataclasses: typed,
read-only views of a row with JSON columns already parsed (`input`, `output`, `result`, `payload`
are dicts). `EventSpec = Tuple[str, Dict[str, Any]]` (line 175) is "an event to write":
`("ATTEMPT_STARTED", {"attempt": 1})`.

#### `Store.__init__` and the transaction helper (lines 178–217)

```python
SCHEMA_VERSION = 1  # PRAGMA user_version


class Store:
    def __init__(self, path: str, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        # One connection per Store, used from one thread. Two Stores on the same file are
        # treated like two processes; the owner fence is what keeps them apart.
        # synchronous=FULL means an fsync per commit, which is the price of durable-before-next-step.
        self.conn = sqlite3.connect(path, isolation_level=None)  # explicit BEGIN/COMMIT only
        self.conn.row_factory = sqlite3.Row
        # Check the version before running any DDL, so a file from a newer kernel is left untouched.
        found = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if found > SCHEMA_VERSION:
            self.conn.close()
            raise StoreError(f"{path}: schema version {found} is newer than this kernel's {SCHEMA_VERSION}")
        self.conn.executescript(
            "PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA foreign_keys=ON;" + SCHEMA
        )
        if found < SCHEMA_VERSION:  # fresh file; migrations would go here
            self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
```
- `isolation_level=None` turns off Python's implicit transaction management so `BEGIN IMMEDIATE`
  is ours. WAL = readers don't block the writer; `synchronous=FULL` = fsync on every commit (the
  durability claim depends on it); `foreign_keys=ON` because SQLite ignores `REFERENCES` otherwise.
  `PRAGMA user_version` is read *first*: a file written by a newer kernel is refused with `StoreError`
  (exit 4) before any DDL has touched it and the connection is closed (`test_newer_schema_is_refused`
  checks the file still has none of our tables). Then `CREATE TABLE IF NOT EXISTS` makes opening
  idempotent and a fresh file (0) is stamped with the schema version (1). **Note for drills:** the connection is
  thread-bound by default (`check_same_thread=True`); see §6.2.

```python
    @contextmanager
    def _tx(self) -> Iterator[None]:
        """IMMEDIATE, not DEFERRED: take the write lock before reading, so the owner check
        and the write see the same snapshot. Under contention sqlite3 waits its busy
        timeout (5 s) and then raises OperationalError (CLI exit 6)."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
```
- **What:** `with self._tx():` wraps a block in one transaction. **Why `IMMEDIATE`:** a deferred
  BEGIN would let two processes both read `owner`, both think they hold it, and then serialize at
  the UPDATE; IMMEDIATE takes the write lock before the fence's SELECT, so the fence reads the truth
  the commit will be based on. **Why `BaseException`:** a `SimulatedCrash`/`KeyboardInterrupt` inside
  a transaction must roll back too. **If removed:** state UPDATE and event INSERT could commit
  separately; the "tables and log cannot disagree" claim dies.

```python
    def _fence(self, wf_id: str, owner: Optional[str]) -> None:
        """Inside a transaction: the workflow must be owned by ``owner`` (None = unowned).

        Checked on every write rather than once at claim time, because the process to
        worry about is the slow one that still thinks it owns the workflow."""
        r = self.conn.execute("SELECT owner FROM workflows WHERE id = ?", (wf_id,)).fetchone()
        if r is None:
            raise NotFound(f"workflow {wf_id} not found")
        if r["owner"] != owner:
            raise LostClaim(f"workflow {wf_id} is owned by {r['owner']}, not {owner}")
```
(lines 219–228)
- The owner fence (D9). Called inside every write transaction: transitions, ledger, loose events.
  A process that lost its claim (another engine took over) gets `LostClaim` and the `_tx` rollback.
  `owner=None` means "I expect it unowned" (used by `Engine.cancel` for the PENDING→CANCELLED edge).
  **If removed:** `test_fenced_out_engine_cannot_finish_the_effect` and
  `test_stale_owner_cannot_write_ledger_or_events` fail: a stalled engine A could overwrite B's record.

```python
    def _append(self, wf_id: str, step: Optional[str], attempt: Optional[int],
                events: Sequence[EventSpec]) -> None:
        """Only called inside a transaction. Every state change goes through here, so this
        is where metrics/tracing would hook in."""
        now = self.now()
        self.conn.executemany(
            "INSERT INTO events (workflow_id, step_name, type, attempt, payload_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [(wf_id, step, t, attempt, dumps(p), now) for t, p in events],
        )
```
(lines 230–239)
- The only INSERT into `events`. Private, so nobody appends outside a transaction.

#### Workflows (lines 243–331)

```python
    def create_workflow(self, wf_id: str, spec_name: str, spec_version: int,
                        input: Dict[str, Any], steps: Sequence[Tuple[str, int]]) -> None:
        """Insert the workflow and its steps (all PENDING) in one transaction."""
        now = self.now()
        with self._tx():
            try:
                self.conn.execute(
                    "INSERT INTO workflows (id, spec_name, spec_version, input_json, state,"
                    " created_at, updated_at) VALUES (?, ?, ?, ?, 'PENDING', ?, ?)",
                    (wf_id, spec_name, spec_version, dumps(input), now, now),
                )
            except sqlite3.IntegrityError:
                raise AlreadyExists(f"workflow {wf_id} already exists") from None
            self.conn.executemany(
                "INSERT INTO steps (workflow_id, name, idx, state, max_attempts, updated_at)"
                " VALUES (?, ?, ?, 'PENDING', ?, ?)",
                [(wf_id, name, i, max_attempts, now) for i, (name, max_attempts) in enumerate(steps)],
            )
            self._append(wf_id, None, None, [("WORKFLOW_CREATED", {
                "spec_name": spec_name, "spec_version": spec_version, "input": input,
                "steps": [{"name": n, "max_attempts": m} for n, m in steps],
            })])
```
- Workflow row + N step rows + `WORKFLOW_CREATED` in one commit (crash point C0 is clean: either
  all exist or none). The primary-key violation becomes `AlreadyExists` (exit 4 in the CLI).
  The event payload carries the step list so `rebuild_state` can create the step projections
  from the log alone.

```python
    def claim(self, wf_id: str, owner: str, take_over: bool = False) -> None:
        """Become the one engine running this workflow.

        ``take_over`` is unconditional: the operator is saying the old owner is dead. If
        they're wrong, the fence still stops the old process from committing anything
        (an effect already in flight still lands; that's what the ledger is for). With
        more than one machine this becomes a lease with a token checked on every write."""
        with self._tx():
            if take_over:
                cur = self.conn.execute("UPDATE workflows SET owner = ? WHERE id = ?", (owner, wf_id))
            else:
                cur = self.conn.execute(
                    "UPDATE workflows SET owner = ? WHERE id = ? AND (owner IS NULL OR owner = ?)",
                    (owner, wf_id, owner))
            if cur.rowcount != 1:
                current = self.load_workflow(wf_id).owner  # raises NotFound if absent
                raise AlreadyOwned(wf_id, current)
            self._append(wf_id, None, None, [("OWNER_CLAIMED", {"owner": owner, "take_over": take_over})])

    def release(self, wf_id: str, owner: str) -> None:
        with self._tx():
            cur = self.conn.execute(
                "UPDATE workflows SET owner = NULL WHERE id = ? AND owner = ?", (wf_id, owner))
            if cur.rowcount == 1:
                self._append(wf_id, None, None, [("OWNER_RELEASED", {"owner": owner})])
```
(lines 281–305)
- **Claim = a conditional UPDATE** (compare-and-set): rowcount 0 means someone else owns it →
  `AlreadyOwned` → CLI exit 3 with "use --take-over if that process is dead". `take_over` is the
  unconditional form an operator uses after a crash. **Release** is also conditional on `owner`: a
  stale engine cannot release someone else's claim (rowcount 0 → silently nothing, no event).
  **Why not a TTL lease:** stealing on expiry without a fencing token at commit lets a slow tool and
  a new owner both commit the same attempt; the fence is the token, and a dead owner is cleared by
  a human, which is stated as the assumption (one live engine per DB file).

```python
    def transition_workflow(self, wf_id: str, frm: str, to: str, owner: Optional[str],
                            reason: str) -> None:
        if (frm, to) not in WF_ALLOWED:
            raise IllegalTransition(f"workflow {frm} -> {to}")
        with self._tx():
            self._fence(wf_id, owner)
            cur = self.conn.execute(
                "UPDATE workflows SET state = ?, updated_at = ? WHERE id = ? AND state = ?",
                (to, self.now(), wf_id, frm))
            if cur.rowcount != 1:
                raise LostClaim(f"workflow {wf_id}: expected state {frm}")
            self._append(wf_id, None, None, [("WORKFLOW_STATE", {"from": frm, "to": to, "reason": reason})])
```
(lines 307–318)
- The pattern every write follows: (1) check the edge against ALLOWED **before** opening a
  transaction (nothing written); (2) `BEGIN IMMEDIATE`; (3) owner fence; (4) UPDATE with
  `WHERE state = frm` as the *second* fence (a stale view of the row cannot commit); (5) append the
  explaining event; (6) COMMIT. `reason` goes into the payload so `history` reads like prose.

```python
    def request_cancel(self, wf_id: str, by: str) -> None:
        """Not fenced: anyone may ask, the owning engine acts on it."""
        with self._tx():
            cur = self.conn.execute(
                "UPDATE workflows SET cancel_requested = 1, updated_at = ? WHERE id = ?",
                (self.now(), wf_id))
            if cur.rowcount != 1:
                raise NotFound(f"workflow {wf_id} not found")
            self._append(wf_id, None, None, [("CANCEL_REQUESTED", {"by": by})])

    def cancel_requested(self, wf_id: str) -> bool:
        return self.load_workflow(wf_id).cancel_requested
```
(lines 320–331)
- Cancel is a *request* anyone may make (a second CLI process while an engine runs); the engine
  reads the flag between attempts. The one write that is deliberately unfenced.

#### Steps (lines 335–370)

```python
    def transition_step(self, wf_id: str, name: str, frm: str, to: str, owner: Optional[str],
                        events: Sequence[EventSpec], attempt_no: Optional[int] = None,
                        **fields: Any) -> None:
        """Move a step and append its events in one transaction. Checks both the owner
        and the step's current state; either mismatch raises LostClaim and rolls back."""
        if (frm, to) not in STEP_ALLOWED:
            raise IllegalTransition(f"step {name}: {frm} -> {to}")
        unknown = set(fields) - STEP_FIELDS
        if unknown:
            raise ValueError(f"unknown step fields: {sorted(unknown)}")
        sets = ", ".join(["state = ?", "updated_at = ?"] + [f"{k} = ?" for k in fields])
        with self._tx():
            self._fence(wf_id, owner)
            cur = self.conn.execute(
                f"UPDATE steps SET {sets} WHERE workflow_id = ? AND name = ? AND state = ?",
                (to, self.now(), *fields.values(), wf_id, name, frm))
            if cur.rowcount != 1:
                raise LostClaim(f"step {wf_id}/{name}: expected state {frm}")
            self._append(wf_id, name, attempt_no, events)
```
- The workhorse: every step edge goes through here. `events` is a list because one transition
  can explain itself with two events (`ATTEMPT_FAILED` + `RETRY_SCHEDULED`). `attempt_no` is
  stamped on the events; `**fields` are the columns to set (`attempt=2, next_run_at=None`), checked
  against `STEP_FIELDS` because they are interpolated into the SQL. The `AND state = ?` is the fence
  that catches "my view of this row is stale". **If `AND state = ?` were removed:** no existing test
  fails (see §6.10 bug 2), which is exactly the kind of gap you should be able to name and close
  with a test on the spot.

#### Effects ledger (lines 374–412)

```python
    def claim_effect(self, key: str, wf_id: str, step: str, effect_name: str, attempt: int,
                     *, owner: Optional[str]) -> None:
        now = self.now()
        with self._tx():
            self._fence(wf_id, owner)
            self.conn.execute(
                "INSERT INTO effects (key, workflow_id, step_name, effect_name, attempt, status,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'CLAIMED', ?, ?)",
                (key, wf_id, step, effect_name, attempt, now, now))
            self._append(wf_id, step, attempt, [("EFFECT_CLAIMED", {
                "key": key, "effect_name": effect_name, "attempt": attempt})])

    def complete_effect(self, key: str, result: Dict[str, Any], *, owner: Optional[str],
                        attempt: int) -> None:
        """``attempt`` is the one that completed it, which after a resume isn't the claimer."""
        with self._tx():
            row = self.get_effect(key)
            if row is None:
                raise NotFound(f"effect {key} not found")
            self._fence(row.workflow_id, owner)
            self.conn.execute(
                "UPDATE effects SET status = 'DONE', result_json = ?, updated_at = ? WHERE key = ?",
                (dumps(result), self.now(), key))
            self._append(row.workflow_id, row.step_name, attempt,
                         [("EFFECT_DONE", {"key": key, "result": result})])
```
- Two commits around the external call: CLAIMED before, DONE after. Both fenced (the self-review
  found the ledger unfenced originally; `test_stale_owner_cannot_write_ledger_or_events` pins it
  now). `*, owner` makes `owner` keyword-only so a caller cannot pass it positionally by accident.
  The `INSERT` with `key` as PRIMARY KEY is what makes a double claim impossible even in principle.

```python
    def append_event(self, wf_id: str, step: Optional[str], type: str, attempt: Optional[int],
                     payload: Dict[str, Any], *, owner: Optional[str]) -> None:
        """Event outside a transition (DECISION, EFFECT_AMBIGUOUS). Fenced like the rest."""
        with self._tx():
            self._fence(wf_id, owner)
            self._append(wf_id, step, attempt, [(type, payload)])

    def events(self, wf_id: str, kind: Optional[str] = None) -> List[Event]:
        sql = "SELECT * FROM events WHERE workflow_id = ?"
        args: Tuple[Any, ...] = (wf_id,)
        if kind:
            sql += " AND type = ?"
            args += (kind,)
        rows = self.conn.execute(sql + " ORDER BY seq", args).fetchall()
        return [Event(r["seq"], r["workflow_id"], r["step_name"], r["type"], r["attempt"],
                      json.loads(r["payload_json"]), r["created_at"]) for r in rows]
```
(lines 416–431)
- `append_event` is for the two "loose" events that are not transitions: `DECISION` (tool-authored)
  and `EFFECT_AMBIGUOUS` (kernel). Still fenced. `events(kind=...)` is what `history --kind` and
  every test assertion use.

### 3.3 `wfk/engine.py` (374 lines): orchestration

Responsibility: decide what runs next from what is on disk. Holds nothing between transitions;
imports no concrete tool and nothing from `faults`.

```python
class AmbiguousEffect(FatalError):
    """A previous attempt claimed this effect and never recorded the outcome,
    and the tool is not idempotent. Only an operator can resolve it."""


def effect_key(workflow_id: str, step_name: str, effect_name: str) -> str:
    # No attempt number and no spec version: retries, restarts and a spec migration
    # all have to present the same key or the receiver can't dedupe. (So renaming a
    # step changes the key; a migration that renames one needs an alias.)
    return hashlib.sha256(f"{workflow_id}:{step_name}:{effect_name}".encode()).hexdigest()
```
(lines 26–35)
- `AmbiguousEffect` is a `FatalError` so `_run_step`'s `except FatalError` catches it and fails the
  step now (no retry can resolve an ambiguity); `isinstance` picks the reason `ambiguous_effect`.
- **The key excludes the attempt**: this is the single most important line for the "no duplicate
  notify" requirement. Adding the attempt (§6.10 bug 1) makes five tests fail.

```python
class StepContext:
    """What a tool gets to see. No store access; both services write under the engine's claim."""

    def __init__(self, engine: "Engine", workflow_id: str, owner: str,
                 step: StepSpec, attempt: int, tool: Tool) -> None:
        self.workflow_id, self.step_name, self.attempt = workflow_id, step.name, attempt
        self._engine, self._owner, self._tool = engine, owner, tool

    def record_decision(self, key: str, data: Dict[str, Any]) -> None:
        """Write a DECISION event: what the tool decided and why."""
        self._engine.store.append_event(self.workflow_id, self.step_name, "DECISION", self.attempt,
                                        {"key": key, "data": data}, owner=self._owner)

    def run_effect(self, name: str, payload: Dict[str, Any], fn: EffectFn) -> Dict[str, Any]:
        """Call ``fn(key, payload)`` through the effects ledger (see Engine._run_effect)."""
        return self._engine._run_effect(self.workflow_id, self._owner, self.step_name,
                                        self.attempt, self._tool, name, payload, fn)
```
(lines 38–54)
- The tool's whole world: three identity fields and two services. Underscored fields are private
  by convention. The context carries the engine's `owner` so every write a tool causes is fenced
  under the engine's claim. **If a tool got the store instead:** it could write unfenced, and a
  stalled tool after a take-over could corrupt B's work.

```python
class Engine:
    BACKOFF_CAP_S = 2.0

    def __init__(self, store: Store, registry: Dict[str, Tool],
                 specs: Optional[Dict[Tuple[str, int], WorkflowSpec]] = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.store, self.registry, self.sleep = store, registry, sleep
        self.specs = dict(SPECS if specs is None else specs)  # own copy: start() may register
```
(lines 57–64)
- Dependency injection by constructor: store, registry (`{tool_name: Tool}`), specs, and `sleep`
  (tests pass `clock.sleep` so backoff advances a fake clock instead of waiting).

```python
    def start(self, spec: WorkflowSpec, input: Dict[str, Any],
              workflow_id: Optional[str] = None) -> str:
        """Create a workflow (PENDING) pinned to ``spec``. Does not run it."""
        if not isinstance(input, dict):
            raise ValueError(f"workflow input must be a JSON object, not {type(input).__name__}")
        reserved = sorted(set(input) & {s.name for s in spec.steps})
        if reserved:  # step outputs are merged into the inputs by step name
            raise ValueError(f"input keys collide with step names: {reserved}")
        self._check_tools(spec)
        registered = self.specs.setdefault(spec.key, spec)
        if registered != spec:
            raise ValueError(f"spec {spec.name}@{spec.version} is already registered with a different body")
        wf_id = workflow_id or uuid.uuid4().hex[:12]
        self.store.create_workflow(wf_id, spec.name, spec.version, input,
                                   [(s.name, s.max_attempts) for s in spec.steps])
        return wf_id
```
(lines 68–83)
- Validate everything *before* persisting: input must be an object; input keys must not collide
  with step names (because `_inputs()` merges `{step_name: output}` into the input dict and a
  collision would shadow); every tool the spec names must exist; a spec version cannot be
  re-registered with a different body (immutability of published versions). Only then one
  transaction creates the rows. `test_start_rejects_bad_input_and_duplicate_id` pins this.

```python
    def cancel(self, workflow_id: str, by: str = "cli") -> str:
        """Cooperative: an unowned PENDING workflow is cancelled right away, anything else
        gets a flag the owning engine checks between attempts. Returns the state after."""
        wf = self.store.load_workflow(workflow_id)
        if wf.state in WF_TERMINAL:
            raise IllegalTransition(f"workflow {workflow_id} is {wf.state}; cannot cancel")
        self.store.request_cancel(workflow_id, by)
        if wf.state == "PENDING" and wf.owner is None:
            try:
                self.store.transition_workflow(workflow_id, "PENDING", "CANCELLED", None,
                                               "cancelled before first run")
            except LostClaim:  # an engine claimed it meanwhile; the flag will be honoured
                pass
        return self.store.load_workflow(workflow_id).state
```
(lines 85–98)
- Three cases: terminal → refuse (exit 4 "cannot cancel"); never run and unowned → CANCELLED now
  (the `owner=None` fence proves nobody claimed it in between; if someone did, `LostClaim` is
  swallowed because the flag is already set and that engine will honour it); otherwise → flag only.
  D13: cooperative, never mid-tool, because preempting an effect is unsafe.

```python
    def run(self, workflow_id: str, take_over: bool = False) -> str:
        """Run or resume a workflow to a terminal state. Returns the final state."""
        wf = self.store.load_workflow(workflow_id)
        if wf.state in WF_TERMINAL:
            if take_over and wf.owner:  # finished, but the process that finished it died before releasing
                self.store.release(workflow_id, wf.owner)
            return wf.state
        spec = self._spec_for(wf)  # fail before claiming, not after
        owner = uuid.uuid4().hex[:8]
        self.store.claim(workflow_id, owner, take_over)
        try:
            state = self._run_owned(workflow_id, owner, spec)
        except Exception:
            self.store.release(workflow_id, owner)  # an error is not a crash: free the claim
            raise
        # BaseException (KeyboardInterrupt, SimulatedCrash) skips the release on purpose:
        # that's the process dying, and a dead process leaves its claim behind.
        self.store.release(workflow_id, owner)
        return state
```
(lines 100–118)
- **Line by line:** terminal workflows are never claimed (no OWNER_* noise;
  `test_run_on_terminal_workflow_does_not_claim`); crash point C7 (finished but owner left) is
  cleaned by `--take-over`. Resolve the spec and tools before claiming so an operator error leaves
  no half-claimed row. Mint an 8-hex owner token. Claim. Run. On an *error* (an `Exception`) release
  and rethrow; on a *crash* (`BaseException`) do nothing, which is exactly what a real dead process
  does: the stale owner stays and a plain `run` is refused with exit 3 until `--take-over`.
  **The `except Exception` vs `BaseException` distinction is the whole crash model.**

```python
    def _spec_for(self, wf: WorkflowRow) -> WorkflowSpec:
        spec = self.specs.get((wf.spec_name, wf.spec_version))
        if spec is None:
            raise ValueError(f"workflow {wf.id} is pinned to spec {wf.spec_name}@{wf.spec_version},"
                             f" which is not registered")
        self._check_tools(spec)
        return spec

    def _check_tools(self, spec: WorkflowSpec) -> None:
        missing = sorted({s.tool for s in spec.steps} - set(self.registry))
        if missing:
            raise ValueError(f"spec {spec.name}@{spec.version} needs tools not in the registry: {missing}")
```
(lines 122–133)
- Versioning in practice: the workflow row says which spec it runs; the engine refuses to run it
  with anything else (`test_spec_file_round_trips_through_start_and_run`: `run` without `--spec
  file.json` is exit 5 "not registered").

```python
    def _run_owned(self, wf_id: str, owner: str, spec: WorkflowSpec) -> str:
        wf = self.store.load_workflow(wf_id)  # re-read under our claim
        if wf.state in WF_TERMINAL:
            # a cancel slipped in between run()'s first read and the claim
            return wf.state
        if wf.state == "PENDING":
            self.store.transition_workflow(wf_id, "PENDING", "RUNNING", owner, "started")

        # A step left RUNNING was interrupted mid-attempt. The attempt stays counted and
        # it reruns immediately. Fine while resume is a manual --take-over; with automatic
        # lease expiry this should back off like a failed attempt.
        for row in self.store.load_steps(wf_id):
            if row.state == "RUNNING":
                self.store.transition_step(wf_id, row.name, "RUNNING", "PENDING", owner,
                                           [("ATTEMPT_INTERRUPTED", {"attempt": row.attempt})],
                                           attempt_no=row.attempt)

        for step in spec.steps:
            row = self.store.load_step(wf_id, step.name)
            if row.state in ("SUCCEEDED", "SKIPPED"):
                continue  # output_json is persisted; never re-run
            if row.state == "FAILED":
                # crashed between STEP_FAILED and the workflow's FAILED commit
                return self._finish(wf_id, owner, "FAILED", f"step {step.name} failed")
            outcome = self._run_step(wf_id, owner, step)
            if outcome in ("FAILED", "CANCELLED"):
                return self._finish(wf_id, owner, outcome, f"step {step.name}: {outcome.lower()}")
        return self._finish(wf_id, owner, "SUCCEEDED", "all steps succeeded or skipped")

    def _finish(self, wf_id: str, owner: str, state: str, reason: str) -> str:
        self.store.transition_workflow(wf_id, "RUNNING", state, owner, reason)
        return state
```
(lines 135–166)
- The resume algorithm (D8), and it is the same code as the first run; that is the point. Re-read
  under the claim; if that re-read is already terminal, a `cancel` landed between `run`'s first read
  and the claim (the row was PENDING and unowned, so the claim went through): return the state, `run`
  releases, nothing is written (`test_cancel_racing_start_is_reported_not_raised`; before this check
  `_finish` tried RUNNING→CANCELLED on a CANCELLED row and the operator saw `LostClaim`, exit 4).
  First run: PENDING→RUNNING. Resume: any RUNNING step was interrupted; mark it,
  move it back to PENDING with its attempt count intact (`attempt` is not in the `**fields`, so it
  stays). Then walk the spec in order: skip terminal-good steps (their `output_json` is on disk);
  a FAILED step found at entry means we died between `STEP_FAILED` and the workflow's FAILED commit,
  so just finish; otherwise run the step and finish on FAILED/CANCELLED. `_finish` is the only
  writer of workflow terminal states.

```python
    def _inputs(self, wf_id: str) -> Dict[str, Any]:
        """Workflow input + {step_name: output} for every SUCCEEDED step, read from the store."""
        inputs = dict(self.store.load_workflow(wf_id).input)
        for row in self.store.load_steps(wf_id):
            if row.state == "SUCCEEDED":
                inputs[row.name] = row.output
        return inputs
```
(lines 168–174)
- Tools see `{"document": ..., "notify_to": ..., "ingest": {...}, "extract": {...}, ...}`, read
  from disk on every attempt. A resumed process therefore sees exactly what the first run produced.
  (SKIPPED steps contribute nothing, which is why the `start` collision check matters.)

```python
    def _run_step(self, wf_id: str, owner: str, step: StepSpec) -> str:
        """Drive one step to SUCCEEDED / SKIPPED / FAILED, or return CANCELLED."""
        tool = self.registry[step.tool]
        while True:
            row = self.store.load_step(wf_id, step.name)
            if self.store.cancel_requested(wf_id):
                return "CANCELLED"
            if row.attempt >= row.max_attempts:
                # Only an interrupted last attempt lands here, so no ATTEMPT_FAILED.
                # It's also what stops an input that crashes the process every time
                # from being resumed forever: each crash costs one attempt.
                error = f"attempt {row.attempt} of {row.max_attempts} was interrupted; no attempts left"
                if row.error:
                    error += f" (error before it: {row.error})"
                return self._give_up(wf_id, owner, step, row.state, row.attempt, "exhausted", error, [])
            if self._wait_until(row.next_run_at) and self.store.cancel_requested(wf_id):
                return "CANCELLED"  # cancelled while we slept

            attempt = row.attempt + 1
            self.store.transition_step(wf_id, step.name, "PENDING", "RUNNING", owner,
                                       [("ATTEMPT_STARTED", {"attempt": attempt})],
                                       attempt_no=attempt, attempt=attempt, next_run_at=None)
            ctx = StepContext(self, wf_id, owner, step, attempt, tool)
            try:
                output = tool.run(ctx, self._inputs(wf_id))  # no transaction open here
                output_json = self._encode_output(output)
            except FatalError as e:
                failed: EventSpec = ("ATTEMPT_FAILED", {"attempt": attempt, "error": str(e), "retryable": False})
                reason = "ambiguous_effect" if isinstance(e, AmbiguousEffect) else "fatal"
                return self._give_up(wf_id, owner, step, "RUNNING", attempt, reason, str(e), [failed])
            except StoreError:
                raise  # e.g. LostClaim from inside ctx.*: our problem, not the tool's
            except Exception as e:  # RetryableError and anything unexpected
                retryable = attempt < row.max_attempts
                failed = ("ATTEMPT_FAILED", {"attempt": attempt, "error": str(e), "retryable": retryable})
                if not retryable:
                    return self._give_up(wf_id, owner, step, "RUNNING", attempt, "exhausted", str(e), [failed])
                # No jitter: one process doesn't contend with itself, and the test checks
                # exact delays. A fleet would need it.
                delay = min(step.backoff_base_s * 2 ** (attempt - 1), self.BACKOFF_CAP_S)
                next_run_at = to_iso(self.store.clock() + delay)
                self.store.transition_step(
                    wf_id, step.name, "RUNNING", "PENDING", owner,
                    [failed, ("RETRY_SCHEDULED", {"attempt": attempt, "next_run_at": next_run_at,
                                                  "delay_s": delay, "error": str(e),
                                                  "reason": f"attempt {attempt} of {row.max_attempts} failed"})],
                    attempt_no=attempt, error=str(e), next_run_at=next_run_at)
                continue

            self.store.transition_step(wf_id, step.name, "RUNNING", "SUCCEEDED", owner,
                                       [("STEP_SUCCEEDED", {"attempt": attempt, "output": output})],
                                       attempt_no=attempt, output_json=output_json, error=None)
            return "SUCCEEDED"

    @staticmethod
    def _encode_output(output: Any) -> str:
        """Outputs get persisted and fed to later steps, so they must be JSON objects.
        Retrying won't fix a bad one, hence FatalError."""
        if not isinstance(output, dict):
            raise FatalError(f"tool returned {type(output).__name__}, not a JSON object")
        try:
            return dumps(output)
        except (TypeError, ValueError) as e:
            raise FatalError(f"tool output is not JSON-serializable: {e}") from e
```
(lines 176–239)
- **The retry loop, top to bottom.** Each iteration re-reads the row (no in-memory state). Cancel
  check first (between attempts, never mid-tool). Budget check: `attempt >= max_attempts` with the
  row PENDING can only mean "interrupted on the last attempt" (a retry is never scheduled without
  budget), so apply the failure policy from PENDING (`PENDING → FAILED | SKIPPED`, the two edges
  added during implementation) with no `ATTEMPT_FAILED`. Wait out a persisted `next_run_at` (clamped
  to the 2 s cap) and, if it waited at all, re-read the cancel flag: a cancel that landed during the
  sleep stops the attempt that was about to start instead of running one more.
  `PENDING → RUNNING` with `attempt += 1` and `next_run_at = None` in one commit. Build the context.
  **Call the tool with no transaction open** (an external call inside a transaction would hold the
  write lock across a network call). Then the three outcomes: `FatalError` → give up with reason
  `fatal`/`ambiguous_effect`; `StoreError` → rethrow (kernel failure, not the tool's); anything else
  → retryable iff budget left: compute the backoff (`0.05, 0.1, 0.2, ...` capped at 2 s), persist
  `next_run_at` with two events, loop. Success → `_encode_output` first (not a dict, or not
  JSON-serializable → `FatalError`: a tool bug fails once with reason `fatal`, never N times as a
  "transient"), then `RUNNING → SUCCEEDED` with the output persisted.
- D4: unexpected exceptions are retryable because "losing less work" is the safer default; a tool
  that knows better raises `FatalError`.

```python
    def _give_up(self, wf_id: str, owner: str, step: StepSpec, frm: str, attempt: int,
                 reason: str, error: str, events: List[EventSpec]) -> str:
        """on_failure decides: FAILED (stops the workflow) or SKIPPED."""
        if step.on_failure == "skip":
            to, ev = "SKIPPED", ("STEP_SKIPPED", {"attempt": attempt, "error": error,
                                                 "reason": reason, "policy": "skip"})
        else:
            to, ev = "FAILED", ("STEP_FAILED", {"attempt": attempt, "error": error, "reason": reason})
        self.store.transition_step(wf_id, step.name, frm, to, owner, events + [ev],
                                   attempt_no=attempt, error=error)
        return to

    def _wait_until(self, next_run_at: Optional[str]) -> bool:
        """Sleep until the persisted deadline. Returns True if it waited at all."""
        if not next_run_at:
            return False
        # Wall-clock time, possibly written by another process. Clamp it so a clock that
        # jumped backwards costs at most one max backoff instead of hours.
        remaining = min(from_iso(next_run_at) - self.store.clock(), self.BACKOFF_CAP_S)
        if remaining <= 0:
            return False
        self.sleep(remaining)
        return True
```
(lines 241–263)
- `_give_up` is the one place `on_failure` is read (D5). `frm` is passed in because it may be
  RUNNING (normal) or PENDING (interrupted last attempt). `_wait_until` sleeps only the remainder
  (`test_restart_during_backoff_waits_the_remainder`: 0.03 s of a 0.05 s backoff after a crash) and
  never more than `BACKOFF_CAP_S`: the deadline is wall-clock time from a possibly different process,
  so a clock that stepped back an hour costs 2 s, not 3600
  (`test_backoff_wait_is_clamped_when_the_clock_steps_backwards`). It returns whether it waited, which
  is how `_run_step` knows to re-check the cancel flag.

```python
    def _run_effect(self, wf_id: str, owner: str, step_name: str, attempt: int, tool: Tool,
                    name: str, payload: Dict[str, Any], fn: EffectFn) -> Dict[str, Any]:
        key = effect_key(wf_id, step_name, name)
        row = self.store.get_effect(key)
        if row is not None and row.status == "DONE":
            return row.result or {}  # done and recorded, don't call fn again
        if row is not None:  # CLAIMED but not DONE: we don't know if it happened
            action = "resend" if tool.idempotent else "fail"
            self.store.append_event(wf_id, step_name, "EFFECT_AMBIGUOUS", attempt, {
                "key": key, "claimed_by_attempt": row.attempt,
                "tool_idempotent": tool.idempotent, "action": action}, owner=owner)
            if not tool.idempotent:
                raise AmbiguousEffect(f"effect '{name}' claimed by attempt {row.attempt} but never "
                                      f"completed; receiver cannot dedupe; needs an operator")
            # idempotent receiver: same key again, it dedupes
        else:
            # TODO: store a payload hash and refuse a resend whose payload differs. Today a
            # resend rebuilds the payload from stored outputs, which only matches because
            # our tools are pure; the receiver would silently keep the first body.
            self.store.claim_effect(key, wf_id, step_name, name, attempt, owner=owner)
        result = fn(key, payload)        # outside any transaction
        self.store.complete_effect(key, result, owner=owner, attempt=attempt)
        return result
```
(lines 267–289)
- **The idempotency decision (D6/D7), the heart of the take-home.** Three cases on the ledger row:
  DONE → return the stored result, `fn` is never called (C5). CLAIMED → we do not know whether the
  effect happened; write `EFFECT_AMBIGUOUS` (the kernel knows that it does not know), then either
  re-call with the *same key* (receiver dedupes) or fail the step (receiver cannot). No row →
  commit CLAIMED, call `fn(key, payload)` outside any transaction, commit DONE. The receiver gets
  the key as its `Idempotency-Key`; `result` is whatever it returned (`{"status": "duplicate"}` on
  a resend), stored verbatim.

```python
@dataclass
class Projection:
    workflows: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    steps: Dict[Tuple[str, str], Dict[str, Any]] = field(default_factory=dict)
    effects: Dict[str, Dict[str, Any]] = field(default_factory=dict)


def _move(obj: Dict[str, Any], frm: str, to: str, allowed: set, where: str) -> None:
    if obj["state"] != frm or (frm, to) not in allowed:
        raise IllegalTransition(f"{where}: {obj['state']} -> {to} (event claims from {frm})")
    obj["state"] = to
```
(lines 294–304)
- The replay projection: plain dicts mirroring the three state tables. `_move` is the reducer's
  transition function: it checks the *current* projected state equals the event's claimed `frm`
  and the edge is allowed; otherwise the log itself is inconsistent (`test_replay_rejects_illegal_log`).

```python
def rebuild_state(events: Iterable[Event]) -> Projection:
    """Same ALLOWED sets as the store; an illegal transition in the log raises."""
    p = Projection()
    for e in events:
        t, pl, where = e.type, e.payload, f"event {e.seq} {e.type}"
        wf = p.workflows.get(e.workflow_id)
        st = p.steps.get((e.workflow_id, e.step_name or ""))
        if t == "WORKFLOW_CREATED":
            p.workflows[e.workflow_id] = {"state": "PENDING", "cancel_requested": False, "owner": None}
            for s in pl["steps"]:
                p.steps[(e.workflow_id, s["name"])] = {"state": "PENDING", "attempt": 0, "output": None,
                                                       "error": None, "next_run_at": None}
        elif t == "WORKFLOW_STATE":
            _move(wf, pl["from"], pl["to"], WF_ALLOWED, where)
        elif t == "CANCEL_REQUESTED":
            wf["cancel_requested"] = True
        elif t == "OWNER_CLAIMED":
            wf["owner"] = pl["owner"]
        elif t == "OWNER_RELEASED":
            wf["owner"] = None
        elif t == "ATTEMPT_STARTED":
            _move(st, "PENDING", "RUNNING", STEP_ALLOWED, where)
            st["attempt"], st["next_run_at"] = pl["attempt"], None
        elif t == "RETRY_SCHEDULED":
            _move(st, "RUNNING", "PENDING", STEP_ALLOWED, where)
            st["next_run_at"], st["error"] = pl["next_run_at"], pl["error"]
        elif t == "ATTEMPT_INTERRUPTED":
            _move(st, "RUNNING", "PENDING", STEP_ALLOWED, where)
        elif t == "STEP_SUCCEEDED":
            _move(st, "RUNNING", "SUCCEEDED", STEP_ALLOWED, where)
            st["output"], st["error"] = pl["output"], None
        elif t in ("STEP_FAILED", "STEP_SKIPPED"):
            _move(st, st["state"], t.split("_")[1], STEP_ALLOWED, where)
            st["error"] = pl["error"]
        elif t == "EFFECT_CLAIMED":
            p.effects[pl["key"]] = {"status": "CLAIMED", "result": None}
        elif t == "EFFECT_DONE":
            p.effects[pl["key"]].update(status="DONE", result=pl["result"])
        # ATTEMPT_FAILED, EFFECT_AMBIGUOUS, DECISION: explanatory, no state change
    return p
```
(lines 307–346)
- One branch per state-changing event type; mirror of what each `transition_*`/ledger call does to
  the tables. Note `STEP_FAILED`/`STEP_SKIPPED` use `st["state"]` as `frm` because they can come
  from RUNNING or PENDING. The three purely explanatory types change nothing. **If a branch forgets
  a field** (§6.10 bug 3: not clearing `next_run_at` on `ATTEMPT_STARTED`), `replay` on a retried
  workflow reports a diff: the reducer is also a test of the store's bookkeeping.

```python
def consistency_diff(store: Store, wf_id: str) -> List[str]:
    """Field-level differences between the log-derived projection and the stored rows."""
    p = rebuild_state(store.events(wf_id))
    diffs: List[str] = []

    def cmp(where: str, derived: Dict[str, Any], stored: Dict[str, Any]) -> None:
        for k, v in derived.items():
            if stored.get(k) != v:
                diffs.append(f"{where}.{k}: log says {v!r}, table says {stored.get(k)!r}")
```
(lines 349–357; the rest compares workflow, each step and each effect, and flags steps in the
table that the log never created)
- Output format is what `replay` prints under `INCONSISTENT:`, e.g.
  `step classify.attempt: log says 1, table says 7`.

### 3.4 `wfk/tools.py` (144 lines): the work

```python
class RetryableError(Exception):
    pass


class FatalError(Exception):
    pass


class Tool(Protocol):
    name: str
    # Declarative only for now: the kernel doesn't enforce it (run_effect is opt-in).
    side_effect_free: bool
    # The receiver dedupes on the key we pass, so an ambiguous effect can be resent.
    idempotent: bool

    def run(self, ctx: "StepContext", inputs: Dict[str, Any]) -> Dict[str, Any]: ...


class Sink(Protocol):
    """Receiver of the notify effect; ``send`` gets the kernel's idempotency key."""
    idempotent: bool  # e.g. an API with an Idempotency-Key header; a bare SMTP relay isn't

    def send(self, key: str, payload: Dict[str, Any]) -> Dict[str, Any]: ...
```
(lines 19–41)
- The two error types are the contract between tool and engine. `Tool` is structural: two flags and
  `run`. `side_effect_free` is a declaration the kernel does not enforce, and the comment at the
  declaration says so (a reviewer who greps the engine for it finds nothing, and should find the
  answer at the field); `run_effect` is opt-in. `idempotent` is deliberately "a statement about the receiver": `NotifyTool` copies it from
  its sink. `Sink` is the receiver interface; `send(key, payload)` is where the kernel-minted key
  crosses the boundary.

```python
class IngestTool:
    name, side_effect_free, idempotent = "ingest", True, True

    def run(self, ctx: "StepContext", inputs: Dict[str, Any]) -> Dict[str, Any]:
        doc = inputs.get("document")
        if not isinstance(doc, dict):
            raise FatalError("input has no 'document' object")
        text = doc.get("text") or ""
        return {"doc_id": doc.get("id") or f"doc-{ctx.workflow_id[:8]}",
                "mime": doc.get("mime", "text/plain"),
                "size_bytes": len(text.encode("utf-8"))}
```
(lines 46–56)
- Pure. A missing `document` is `FatalError` (retrying an input cannot help). Falls back to a
  doc id derived from the workflow id.

```python
class ExtractTool:
    name, side_effect_free, idempotent = "extract", True, True

    def run(self, ctx: "StepContext", inputs: Dict[str, Any]) -> Dict[str, Any]:
        text = inputs["document"].get("text")
        if not isinstance(text, str) or not text.strip():
            raise FatalError("document has no text to extract")  # retrying cannot help
        lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
        return {"title": lines[0][:80], "body": " ".join(lines[1:]), "word_count": len(text.split())}
```
(lines 59–67)
- First non-empty line is the title (max 80 chars), the rest the body. Whitespace-only text is the
  `FatalError` that `test_fatal_error_fails_without_retry` uses (`"text": "   "`).

```python
class ClassifyTool:
    name, side_effect_free, idempotent = "classify", True, True
    RULES = (
        ("invoice", ("invoice", "total due", "amount due", "payment terms")),
        ("contract", ("agreement", "hereby", "party", "term of")),
        ("resume", ("experience", "education", "skills")),
    )

    def run(self, ctx: "StepContext", inputs: Dict[str, Any]) -> Dict[str, Any]:
        text = (inputs["extract"]["title"] + " " + inputs["extract"]["body"]).lower()
        best, hits, matched = "other", 0, "default"
        for label, keywords in self.RULES:
            found = [k for k in keywords if k in text]
            if len(found) > hits:
                best, hits, matched = label, len(found), "keyword:" + ",".join(found)
        confidence = round(min(0.5 + 0.15 * hits, 0.95), 2) if hits else 0.3
        decision = {"label": best, "confidence": confidence, "rule": matched}
        ctx.record_decision("label", decision)
        return decision
```
(lines 70–88)
- Keyword rules; the label with most hits wins; confidence `0.5 + 0.15·hits` capped at 0.95 (3 hits
  → 0.95 for the example invoice). `record_decision` is the brief's "query decisions made during
  execution": `history --kind DECISION` returns `{"label": "invoice", "confidence": 0.95, "rule":
  "keyword:invoice,total due,payment terms"}`. It reads `inputs["extract"]`, the previous step's
  persisted output.

```python
class NotifyTool:
    """One email per workflow, sent through the effects ledger."""
    name, side_effect_free = "notify", False

    def __init__(self, sink: Sink) -> None:
        self.sink = sink
        self.idempotent = sink.idempotent

    def run(self, ctx: "StepContext", inputs: Dict[str, Any]) -> Dict[str, Any]:
        label = inputs["classify"]["label"]
        payload = {"to": inputs.get("notify_to", "triage@example.com"),
                   "subject": f"[{label}] {inputs['extract']['title']}",
                   "body": f"Document {inputs['ingest']['doc_id']} was classified as {label} "
                           f"(confidence {inputs['classify']['confidence']})."}
        return {"email": ctx.run_effect("email", payload, self.sink.send)}
```
(lines 91–105)
- The only effectful tool. Builds the payload from three earlier outputs, then hands the *function*
  `self.sink.send` to the kernel; the kernel decides whether to call it. Output is
  `{"email": <sink result>}`.

```python
class FileNotifier:
    """Demo receiver: one JSON line per key in a file, like a provider that honours an
    Idempotency-Key. It lives outside the database on purpose, so the crash window
    between "accepted" and "DONE" is real."""

    def __init__(self, path: str, idempotent: bool = True) -> None:
        self.path = path
        self.idempotent = idempotent
        self.calls = 0  # includes deduped calls
        self._seen: Optional[Dict[str, Dict[str, Any]]] = None

    def sent_keys(self) -> Dict[str, Dict[str, Any]]:
        # Loaded from the file on first use, so a process that takes over after a crash
        # sees what the dead one sent. Two live writers would each trust their own copy;
        # the owner fence keeps that from happening.
        if self._seen is None:
            self._seen = {}
            if os.path.exists(self.path):
                with open(self.path) as f:
                    for ln in f:
                        if ln.strip():
                            row = json.loads(ln)
                            self._seen[row["key"]] = row
        return self._seen

    def send(self, key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.calls += 1
        if self.idempotent and key in self.sent_keys():
            return {"status": "duplicate", "key": key}
        # idempotent=False: ignores the key, every call is a delivery
        row = {"key": key, "payload": payload}
        with open(self.path, "a") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self.sent_keys()[key] = row
        return {"status": "sent", "key": key, "to": payload["to"]}
```
(lines 108–144)
- Stands in for SES/SendGrid: a dedupe index keyed by the idempotency key, lazily loaded from the
  file (a "new process" reads the receiver's state from disk). `fsync` makes "accepted" durable so
  the crash window is real. `calls` vs lines in the file is how tests prove "two invocations, one
  effect". **Note:** `send` returns `payload["to"]`, so any payload without `to` is a `KeyError`
  (which the engine treats as retryable; see §6.8 pitfall). `idempotent=False` is how tests model
  a legacy relay, and it is behaviour, not a label: with it every call is a delivery, so a resend
  would be the duplicate (`test_file_notifier_honours_the_key_only_when_it_says_it_does`; before
  this the flag changed what the tool declared and nothing about what the receiver did).

### 3.5 `wfk/faults.py` (84 lines): crash injection, never imported by the kernel

```python
class SimulatedCrash(BaseException):
    """Stands in for kill -9. A BaseException so no `except Exception` swallows it."""
```
(lines 17–18) The single most important design choice in the test strategy.

```python
class _Wrapper:
    def __init__(self, inner: Tool) -> None:
        self.inner = inner
        self.name, self.side_effect_free, self.idempotent = inner.name, inner.side_effect_free, inner.idempotent


class Flaky(_Wrapper):
    """Raises RetryableError on the first ``fail_times`` calls, then delegates."""

    def __init__(self, inner: Tool, fail_times: int) -> None:
        super().__init__(inner)
        self.fail_times, self.calls = fail_times, 0

    def run(self, ctx: "StepContext", inputs: Dict[str, Any]) -> Dict[str, Any]:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RetryableError(f"{self.name}: transient failure #{self.calls}")
        return self.inner.run(ctx, inputs)


class Counting(_Wrapper):
    def __init__(self, inner: Tool) -> None:
        super().__init__(inner)
        self.calls = 0

    def run(self, ctx: "StepContext", inputs: Dict[str, Any]) -> Dict[str, Any]:
        self.calls += 1
        return self.inner.run(ctx, inputs)
```
(lines 21–48) Decorators (Java sense) around a tool: `Flaky` = `--fail step:N`; `Counting` proves
"not re-run after resume" by counting calls per process.

```python
class CrashingSink:
    """The receiver accepts, then we die before DONE commits. The worst case."""

    def __init__(self, inner: Sink) -> None:
        self.inner = inner
        self.idempotent = inner.idempotent

    def send(self, key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.inner.send(key, payload)
        raise SimulatedCrash("process died after the receiver accepted the notification")


class CrashAfterCommit(Store):
    """Dies right after ``step``'s SUCCEEDED commit, i.e. between two steps, which a
    tool wrapper can't reach."""

    def __init__(self, path: str, step: str, **kw: Any) -> None:
        super().__init__(path, **kw)
        self.crash_after = step

    def transition_step(self, wf_id: str, name: str, frm: str, to: str, *args: Any, **kw: Any) -> None:
        super().transition_step(wf_id, name, frm, to, *args, **kw)
        if to == "SUCCEEDED" and name == self.crash_after:
            raise SimulatedCrash(f"process died right after step {name} committed")
```
(lines 51–74) The two crash points that matter: C4 (receiver accepted, DONE not committed) via a
sink wrapper, and "between steps" via a Store subclass (a tool wrapper cannot reach the moment
after a commit). `--crash-in-notify` and `--crash-after STEP` in the CLI.

```python
def parse_fail_spec(spec: Optional[str]) -> Dict[str, int]:
    """'extract:2,notify:1' -> {'extract': 2, 'notify': 1}"""
    out: Dict[str, int] = {}
    for part in (spec or "").split(","):
        if part.strip():
            name, _, n = part.partition(":")
            out[name.strip()] = int(n or 1)
    return out
```
(lines 77–84)

### 3.6 `wfk/cli.py` (246 lines): wiring and the operator surface

```python
def build_registry(notifications: str, fail: Optional[str] = None,
                   crash_in_notify: bool = False) -> Dict[str, Tool]:
    sink = FileNotifier(notifications)
    reg: Dict[str, Tool] = {
        "ingest": IngestTool(), "extract": ExtractTool(), "classify": ClassifyTool(),
        "notify": NotifyTool(CrashingSink(sink) if crash_in_notify else sink),
    }
    for name, times in parse_fail_spec(fail).items():
        if name not in reg:
            raise ValueError(f"--fail: no such tool {name!r}")
        reg[name] = Flaky(reg[name], times)
    return reg
```
(lines 25–36) **This is where tools are bound to names.** The engine never sees this. Adding a tool
= one entry here (and in `tests/conftest.py::registry`).

```python
def _engine(a: argparse.Namespace, store: Store) -> Tuple[Engine, WorkflowSpec]:
    """--spec is either name@version or a JSON file. A file spec only exists for this
    process, so ``run`` needs the same flag later."""
    specs = dict(SPECS)
    if a.spec and os.path.exists(a.spec):
        spec = load_spec_json(a.spec)
        specs[spec.key] = spec
    else:
        name, _, ver = (a.spec or "document_triage@1").partition("@")
        spec = specs.get((name, int(ver or 1)))  # type: ignore[assignment]
        if spec is None:
            known = ", ".join(f"{n}@{v}" for n, v in sorted(specs))
            raise ValueError(f"unknown spec {a.spec!r}; registered: {known}")
    registry = build_registry(a.notifications, a.fail, a.crash_in_notify)
    return Engine(store, registry, specs), spec
```
(lines 39–53) `--spec` is either `name@version` or a path.

```python
def _run(engine: Engine, wf_id: str, take_over: bool) -> int:
    try:
        state = engine.run(wf_id, take_over=take_over)
    except AlreadyOwned as e:
        print(f"workflow {wf_id} is owned by {e.owner}; use --take-over if that process is dead",
              file=sys.stderr)
        return 3
    except SimulatedCrash as e:
        print(f"*** SIMULATED CRASH: {e} ***", file=sys.stderr)
        sys.stderr.flush()
        # os._exit, not sys.exit: SystemExit would run main()'s finally and close cleanly,
        # which isn't a crash. Like kill -9: no cleanup, claim stays on disk.
        os._exit(1)
    print(f"workflow {wf_id} finished: {state}")
    print_status(engine.store, engine.store.load_workflow(wf_id))
    return 0 if state == "SUCCEEDED" else 2
```
(lines 124–139) `os._exit(1)` skips `finally: store.close()` on purpose.

```python
def cmd_replay(a: argparse.Namespace, store: Store) -> int:
    """Read-only. Never runs a tool."""
    events = store.events(a.id)
    if not events:
        raise NotFound(f"workflow {a.id} has no events")
    for e in events:
        print(narrate(e))
    p = rebuild_state(events)  # raises IllegalTransition if the log itself is inconsistent
    print(f"--- derived from {len(events)} events: workflow {p.workflows[a.id]['state']}, steps "
          + ", ".join(f"{n}={s['state']}" for (w, n), s in p.steps.items() if w == a.id))
    diffs = consistency_diff(store, a.id)
    if diffs:
        print("INCONSISTENT:\n  " + "\n  ".join(diffs))
        return 1
    print("CONSISTENT: event log reproduces the stored state")
    return 0
```
(lines 170–185) D10: replay never calls a tool.

`cmd_history` (166–170) loads the workflow first: an unknown id is `NotFound`, exit 4, like every
other verb, not an empty history that looks like a workflow with no events.

`_NARRATORS` (lines 81–100) is one lambda per event type turning a payload into the one-line
narrative `history` prints; `narrate` (104–106) formats `seq  time  step  TYPE  text`. `main`
(190–248) builds the argparse tree, opens the store (wrapped in `CrashAfterCommit` when
`--crash-after` is given), dispatches `a.fn(a, store)`, and maps exceptions to exit codes:
`StoreError` → 4, `ValueError`/`OSError` → 5, `sqlite3.Error` → 6; `finally: store.close()`. Opening
the store has its own two handlers: a `StoreError` there is the schema-version refusal (4), a
`sqlite3.Error` a locked or unreadable file (6).

### 3.7 `tests/conftest.py` (69 lines): the harness

```python
class FakeClock:
    """Injected into Store (time) and Engine (sleep) so backoff never really waits."""

    def __init__(self, t: float = 1_700_000_000.0) -> None:
        self.t = t
        self.slept: List[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s
```
```python
    def _make(reg: Optional[Dict[str, Tool]] = None, crash_after: Optional[str] = None, **kw) -> Engine:
        store = CrashAfterCommit(db_path, crash_after, clock=clock) if crash_after else Store(db_path, clock=clock)
        stores.append(store)
        return Engine(store, reg or registry(FileNotifier(notifications)), sleep=clock.sleep, **kw)
```
- `make_engine()` called twice in a test = two processes on the same database file (a fresh `Store`,
  fresh tools, same disk). `clock.slept` records what the engine *would* have slept. This is why the
  whole suite runs in well under a second.

---

## 4. End-to-end traces (real output, `examples/doc.json`, Oct 30 run)

### 4.1 Clean `start`: 16 events, 11 transactions

`python -m wfk --db wfk.db start examples/doc.json --id clean`

| # | Where | What happens | SQL writes (one transaction each) | Event(s) |
|---|---|---|---|---|
| 1 | `cli.main` | parse args; `Store("wfk.db")` opens the connection, runs PRAGMAs + `CREATE TABLE IF NOT EXISTS` ×4 | DDL (idempotent) | |
| 2 | `cmd_start` → `_engine` | load `examples/doc.json`; resolve `document_triage@1`; `build_registry` → 4 tools, plain `FileNotifier` | | |
| 3 | `Engine.start` | validate input (dict, no key collision), `_check_tools`, register spec; `store.create_workflow("clean", "document_triage", 1, input, [("ingest",3),("extract",3),("classify",2),("notify",5)])` | INSERT workflows (PENDING) + 4× INSERT steps (PENDING, attempt 0) + INSERT events | 1 `WORKFLOW_CREATED` |
| 4 | `Engine.run` | load row (PENDING, not terminal); `_spec_for`; owner = `uuid4().hex[:8]`; `store.claim` | UPDATE workflows SET owner WHERE owner IS NULL + INSERT events | 2 `OWNER_CLAIMED` |
| 5 | `_run_owned` | state PENDING → `transition_workflow(PENDING, RUNNING)` | fence SELECT; UPDATE workflows state WHERE state='PENDING'; INSERT events | 3 `WORKFLOW_STATE PENDING -> RUNNING (started)` |
| 6 | `_run_owned` | resume scan: no RUNNING step | SELECT only | |
| 7 | `_run_step(ingest)` | cancel flag no; attempt 0 < 3; `next_run_at` NULL; `transition_step(PENDING→RUNNING, attempt=1, next_run_at=None)` | fence; UPDATE steps WHERE state='PENDING'; INSERT events | 4 `ATTEMPT_STARTED 1` |
| 8 | `IngestTool.run(ctx, inputs)` | `inputs` = `_inputs()` = the document + notify_to; returns `{"doc_id":"doc-4471","mime":"text/plain","size_bytes":152}` | none (no transaction open) | |
| 9 | `_run_step` | `transition_step(RUNNING→SUCCEEDED, output_json=..., error=None)` | fence; UPDATE steps; INSERT events | 5 `STEP_SUCCEEDED` |
| 10 | `_run_step(extract)` | same shape; inputs now include `"ingest": {...}`; output title `Invoice #4471 from Northwind Traders`, word_count 20 | 2 transactions | 6 `ATTEMPT_STARTED`, 7 `STEP_SUCCEEDED` |
| 11 | `_run_step(classify)` | `ATTEMPT_STARTED`; `ClassifyTool.run` finds 3 invoice keywords → confidence 0.95; **`ctx.record_decision("label", ...)`** → `store.append_event` (fenced, its own transaction); returns; `STEP_SUCCEEDED` | 3 transactions | 8 `ATTEMPT_STARTED`, 9 `DECISION`, 10 `STEP_SUCCEEDED` |
| 12 | `_run_step(notify)` | `ATTEMPT_STARTED` | 1 transaction | 11 `ATTEMPT_STARTED` |
| 13 | `NotifyTool.run` → `ctx.run_effect("email", payload, sink.send)` → `Engine._run_effect` | key = sha256(`clean:notify:email`); `get_effect` → None; `store.claim_effect` | fence; INSERT effects (CLAIMED); INSERT events | 12 `EFFECT_CLAIMED` |
| 14 | `_run_effect` | `fn(key, payload)` = `FileNotifier.send`: key unseen → append one JSON line to `notifications.jsonl`, fsync; returns `{"status":"sent","key":...,"to":"ap-team@example.com"}` | **file write, outside the DB** | |
| 15 | `_run_effect` | `store.complete_effect(key, result, attempt=1)` | fence; UPDATE effects SET status='DONE', result_json; INSERT events | 13 `EFFECT_DONE` |
| 16 | `_run_step` | `transition_step(RUNNING→SUCCEEDED)` with output `{"email": {...}}` | 1 transaction | 14 `STEP_SUCCEEDED` |
| 17 | `_run_owned` → `_finish` | all steps terminal-good → `transition_workflow(RUNNING, SUCCEEDED)` | fence; UPDATE workflows; INSERT events | 15 `WORKFLOW_STATE RUNNING -> SUCCEEDED` |
| 18 | `Engine.run` | `store.release(owner)` | UPDATE workflows SET owner=NULL WHERE owner=?; INSERT events | 16 `OWNER_RELEASED` |
| 19 | `cli._run` | print `finished: SUCCEEDED` + `print_status` (one SELECT per table); exit 0 | | |

The real `history clean` output (timestamps are UTC):
```
   1  06:52:06.284  workflow  WORKFLOW_CREATED     created document_triage@1
   2  06:52:06.284  workflow  OWNER_CLAIMED        claimed by ce54cb10
   3  06:52:06.284  workflow  WORKFLOW_STATE       PENDING -> RUNNING (started)
   4  06:52:06.284  ingest    ATTEMPT_STARTED      attempt 1 started
   5  06:52:06.285  ingest    STEP_SUCCEEDED       SUCCEEDED on attempt 1: {"doc_id": "doc-4471", "mime": "text/plain", "size_bytes": 152}
   6  06:52:06.285  extract   ATTEMPT_STARTED      attempt 1 started
   7  06:52:06.285  extract   STEP_SUCCEEDED       SUCCEEDED on attempt 1: {"body": "Payment terms: net 30. ...", "title": "Invoice #4471 from Northwind Traders", "word_count": 20}
   8  06:52:06.285  classify  ATTEMPT_STARTED      attempt 1 started
   9  06:52:06.285  classify  DECISION             decision label: {"confidence": 0.95, "label": "invoice", "rule": "keyword:invoice,total due,payment terms"}
  10  06:52:06.285  classify  STEP_SUCCEEDED       SUCCEEDED on attempt 1: {"confidence": 0.95, "label": "invoice", "rule": "keyword:invoice,total due,payment terms"}
  11  06:52:06.285  notify    ATTEMPT_STARTED      attempt 1 started
  12  06:52:06.285  notify    EFFECT_CLAIMED       effect 'email' claimed, key 340b213d1d88...
  13  06:52:06.286  notify    EFFECT_DONE          effect done: {"key": "340b213d...", "status": "sent", "to": "ap-team@example.com"}
  14  06:52:06.286  notify    STEP_SUCCEEDED       SUCCEEDED on attempt 1: {"email": {"key": "340b213d...", "status": "sent", "to": "ap-team@example.com"}}
  15  06:52:06.286  workflow  WORKFLOW_STATE       RUNNING -> SUCCEEDED (all steps succeeded or skipped)
  16  06:52:06.286  workflow  OWNER_RELEASED       released by ce54cb10
```

### 4.2 Crash mid-notify (`--crash-in-notify`), then resume: 20 events

Process 1: `start examples/doc.json --id crash2 --crash-in-notify`. Steps 1–13 of §4.1 are
identical (events 1–12). Then:

| # | Where | What happens | On disk after |
|---|---|---|---|
| 14' | `_run_effect` → `CrashingSink.send` | `inner.send(key, payload)` appends the line and fsyncs (**the receiver has accepted**); then `raise SimulatedCrash(...)` | `notifications.jsonl` has 1 line |
| 15' | propagation | `SimulatedCrash` is a `BaseException`: `_run_step`'s `except FatalError` / `except StoreError` / `except Exception` all miss it; `Engine.run`'s `except Exception` misses it; **no `release`**; `cli._run` catches it, prints `*** SIMULATED CRASH ***`, `os._exit(1)` | workflow RUNNING, `owner=58142013`; notify RUNNING attempt 1/5; effect `CLAIMED`, result `-`. **This is C4: the kernel cannot know whether the email left.** |

`status crash2` shows exactly that (`notify RUNNING 1/5`, `effect email@notify CLAIMED`).

Process 2: `run crash2` → `AlreadyOwned` → exit 3 ("owned by 58142013; use --take-over").
`run crash2 --take-over`:

| # | Where | What happens | Event |
|---|---|---|---|
| R1 | `Engine.run` | load (RUNNING, not terminal); `_spec_for`; new owner `536cb746`; `claim(take_over=True)` = unconditional UPDATE owner | 13 `OWNER_CLAIMED claimed by 536cb746 (take-over)` |
| R2 | `_run_owned` | state already RUNNING → no workflow transition. Resume scan finds notify RUNNING → `transition_step(RUNNING→PENDING)` with no `attempt` field (stays 1) | 14 `ATTEMPT_INTERRUPTED attempt 1 found RUNNING on resume; back to PENDING` |
| R3 | step loop | ingest/extract/classify SUCCEEDED → `continue` (never re-run; `Counting` tools prove 0 calls in `test_restart_resumes_without_rerunning`) | |
| R4 | `_run_step(notify)` | attempt 1 < 5; `next_run_at` NULL; `PENDING→RUNNING, attempt=2` | 15 `ATTEMPT_STARTED attempt 2` |
| R5 | `NotifyTool.run` → `_run_effect` | same key (no attempt in it); `get_effect` → CLAIMED, not DONE → `append_event(EFFECT_AMBIGUOUS, action=resend)` because `tool.idempotent` is True (copied from `FileNotifier`) | 16 `EFFECT_AMBIGUOUS effect 2b1de699f0d7... claimed by attempt 1 never completed; tool idempotent=True -> resend` |
| R6 | `_run_effect` | no new claim (row exists); `fn(key, payload)` → `FileNotifier.send` (a fresh instance; `sent_keys()` loads the file, finds the key) → returns `{"status":"duplicate","key":...}`, **writes nothing** | |
| R7 | `_run_effect` | `complete_effect(key, result, attempt=2)` | 17 `EFFECT_DONE effect done: {..., "status": "duplicate"}` (attempt column = 2) |
| R8 | `_run_step` | `RUNNING→SUCCEEDED`, output `{"email": {"status": "duplicate", ...}}` | 18 `STEP_SUCCEEDED on attempt 2` |
| R9 | `_finish` | `RUNNING→SUCCEEDED` | 19 `WORKFLOW_STATE` |
| R10 | `Engine.run` | `release(536cb746)` | 20 `OWNER_RELEASED` |

Result: two invocations of the receiver, one line in `notifications.jsonl`, and a log that says
why. `replay crash2` → `derived from 20 events ... CONSISTENT`. This is `test_notify_effect_once_across_crash`.

**Variant, non-idempotent receiver** (`test_ambiguous_non_idempotent_fails_safely`): R5 writes
`EFFECT_AMBIGUOUS ... action=fail` and raises `AmbiguousEffect`; `_run_step` catches it as
`FatalError`, reason `ambiguous_effect`; `_give_up` with notify's `on_failure` → with
`fail_workflow` (the test's `triage_strict` spec) the step is FAILED, the workflow FAILED, the effect
row stays CLAIMED for an operator, the receiver was called exactly once ever. With the default
`skip` policy the step would be SKIPPED and the workflow would still SUCCEED.

**Variant, crash between steps** (`--crash-after classify`, `test_restart_resumes_without_rerunning`):
`CrashAfterCommit.transition_step` raises right after classify's SUCCEEDED commit. On disk: three
SUCCEEDED, notify PENDING 0/5, workflow RUNNING, owner set. Resume: no RUNNING step so no
`ATTEMPT_INTERRUPTED`; notify starts attempt 1 normally; one notification.

### 4.3 Retry path (`--fail extract:2`): 22 events

Events 1–5 as in §4.1. Then, with `Flaky(ExtractTool(), 2)` registered as `extract`:

| # | Where | What happens | Event |
|---|---|---|---|
| 1 | `_run_step(extract)` | `PENDING→RUNNING, attempt=1` | 6 `ATTEMPT_STARTED attempt 1` |
| 2 | `Flaky.run` | `calls=1 <= 2` → `raise RetryableError("extract: transient failure #1")` | |
| 3 | `_run_step` `except Exception` | `retryable = 1 < 3` True; delay = `min(0.05·2⁰, 2.0)` = 0.05; `next_run_at = now+0.05`; `transition_step(RUNNING→PENDING, error=..., next_run_at=...)` with **two** events | 7 `ATTEMPT_FAILED attempt 1 failed: extract: transient failure #1 (retryable)`, 8 `RETRY_SCHEDULED retry in 0.05s at ...: attempt 1 of 3 failed` |
| 4 | loop | re-read row: PENDING, attempt 1, `next_run_at` set; cancel no; `1 < 3`; `_wait_until` sleeps the remainder (~0.05 s) | |
| 5 | | `PENDING→RUNNING, attempt=2, next_run_at=None` | 9 `ATTEMPT_STARTED attempt 2` |
| 6 | `Flaky.run` | `calls=2 <= 2` → raise | |
| 7 | | `retryable = 2 < 3`; delay `0.05·2¹` = 0.1 | 10 `ATTEMPT_FAILED`, 11 `RETRY_SCHEDULED retry in 0.1s` |
| 8 | | sleep ~0.1 s; `PENDING→RUNNING, attempt=3` | 12 `ATTEMPT_STARTED attempt 3` |
| 9 | `Flaky.run` | `calls=3 > 2` → delegate to `ExtractTool.run` → success | |
| 10 | | `RUNNING→SUCCEEDED` | 13 `STEP_SUCCEEDED on attempt 3` |

Then classify and notify as in §4.1 (events 14–22). `ingest.attempt` is still 1: retries never
touch an earlier step. If attempt 3 had also failed: `retryable = 3 < 3` False → `_give_up(...,
"exhausted", ...)` → extract FAILED (policy `fail_workflow`) → workflow FAILED, classify/notify
stay PENDING 0/n forever, visible in `status`. `test_retry_then_success` asserts delays
`[0.05, 0.1]`, `clock.slept == [0.05, 0.1]`, and `step.next_run_at is None` at the end.

---

## 5. Walk them through it in 10 minutes

**Order and what to say** (have the repo open, terminal split):

1. (1 min) **The one-liner and the layering.** "Engine decides, Store remembers, Tool does. Six
   modules; the engine imports no concrete tool and nothing from `faults`; the registry is built in
   `cli.py`." Show the `wfk/` listing and the docstring at the top of `store.py`.
2. (2 min) **The state machine as data.** Open `store.py:19-33`. "Both machines are set literals.
   `transition_*` refuses anything not in them before opening a transaction, and `rebuild_state` in
   `engine.py` applies the same sets on read, so there is one machine, not two." Point at the two
   edges added during implementation (`PENDING → FAILED | SKIPPED`) and say why: a step interrupted
   on its last attempt.
3. (2 min) **One write = one fenced transaction.** Open `store.py:352-370` (`transition_step`).
   Read it top to bottom: ALLOWED check, `BEGIN IMMEDIATE`, owner fence, `UPDATE ... WHERE state = ?`,
   `rowcount != 1 → LostClaim`, append events, COMMIT. "Tables and log cannot disagree because they
   are written together; a stale engine cannot write anything because every write passes `_fence`."
4. (3 min) **The effects ledger.** Open `engine.py:267-289` (`_run_effect`) and `effect_key` above
   it. "Key has no attempt number. Three cases: DONE → return stored result; CLAIMED → record
   `EFFECT_AMBIGUOUS`, then resend if the tool says its receiver dedupes, else fail the step and
   leave the row for an operator; no row → claim, call, complete." Then state the guarantee verbatim.
5. (2 min) **Run it live**: `scripts/demo.sh` sections 3–5, or just
   `.venv/bin/python -m pytest -q tests/test_idempotency.py -v` and then open
   `test_notify_effect_once_across_crash` and read the asserts aloud: `healthy.calls == 1 and
   crashing.calls == 1` (two invocations), `len(_lines(notifications)) == 1` (one effect),
   `result["status"] == "duplicate"`, `attempt == 2`. If there is time: `replay <id>` → CONSISTENT.

**The three decisions to emphasize** (and the alternative you rejected for each):

- **Tables + log in one transaction, with a reducer that proves they agree** (D2), rejected pure
  event sourcing (fold on every read) and tables-only (no audit). "`status` is one SELECT; the
  audit trail is complete by construction; `replay` is the proof."
- **Effects ledger with the ambiguity recorded and resolved per tool** (D6/D7), rejected the
  transactional outbox + dispatcher (same window, hidden in the relay, and `status` would say
  SUCCEEDED while nothing has left) and the dedupe-only sink (kernel keeps no record). "I made the
  unavoidable window a first-class, queryable fact."
- **Claim with a fence re-checked in every write, not a TTL lease** (D9), rejected lease-with-
  stealing because stealing without a fencing token at commit lets a slow tool and a new owner
  both commit the same attempt. "I traded auto-recovery of a dead owner for a provable property;
  `--take-over` is the operator's explicit action, and the evolution section says how the claim
  becomes `owner, lease_until, lease_token` with every commit carrying `WHERE lease_token = ?`."

**"How did you use AI?" (honest, confident, 30 seconds):**

> "The brief encouraged it, so I used it the way I use a strong pair: I wrote the design record
> first, debated three shapes for the side-effect problem, and chose the ledger. Then I used AI to
> draft code against that design, and I reviewed every line, pushed back where it was wrong, and
> reworked it: the owner fence on the ledger writes and the two `PENDING → FAILED | SKIPPED` edges
> both came out of my review, not the first draft. Tests and fault injection were my call: crashes at
> the real boundaries, a `BaseException` for kill -9, no hooks inside the kernel. I own the
> decisions and the code; I can rebuild any function from memory, and I'm happy to extend it right
> now without any assistant."

Rules for that answer: never apologetic, never "the AI did X"; speak in "I decided", "I rejected",
"I reviewed"; name two specific things you changed in review (the fence on the ledger, the two
extra edges; both are documented in `docs/DESIGN.md` under "State machines" and "Ownership: the claim and the fence"); then pivot to the live drill.

**Pitfalls in the room:** don't claim exactly-once (say the guarantee precisely); don't call
`replay` a re-execution; don't say the lease is "missing" (say it was a deliberate trade and name
the evolution); if asked why Python when you're a Java engineer, "stdlib + sqlite3, zero
dependencies, fits the brief's 3–5 hours; the design is language-independent and I'd write the same
in Java with JDBC and `BEGIN IMMEDIATE`'s equivalent, `SELECT ... FOR UPDATE`."

---

## 6. Live-change drills (no AI; built and tested in a scratch copy; the base suite plus each drill's own test green)

Rules of the room: say the four-line plan first (which layer it touches → which events/edges change →
what the reducer needs → the test in one sentence), then type. After each drill run
`.venv/bin/python -m pytest -q`. Revert with `git checkout -- .` and delete new test files.
Every drill below is the exact diff that passed; the `+` lines are what you type.

### 6.1 Add a step: `Redact` between Extract and Classify

**Prompt:** "Add a redaction step that masks emails and amounts before classification, without
breaking workflows already running v1."

**Plan:** new pure tool in `tools.py`; a new spec *version* in `spec.py` (never edit v1); register
the tool in `cli.build_registry` and `tests/conftest.py::registry`; test that v2 redacts and v1 is
untouched. No engine change, no store change, no new event type.

`wfk/tools.py`, insert before `class ClassifyTool:` (after line 76):
```python
class RedactTool:
    """Masks email addresses and dollar amounts in the extracted body (pure, re-runnable)."""
    name, side_effect_free, idempotent = "redact", True, True
    PATTERNS = (("email", r"[\w.+-]+@[\w-]+\.[\w.]+"), ("amount", r"\$[\d,]+(?:\.\d{2})?"))

    def run(self, ctx: "StepContext", inputs: Dict[str, Any]) -> Dict[str, Any]:
        import re
        body, counts = inputs["extract"]["body"], {}
        for label, pattern in self.PATTERNS:
            body, counts[label] = re.subn(pattern, f"[{label}]", body)
        ctx.record_decision("redactions", counts)
        return {"body": body, "redactions": counts}
```

`wfk/spec.py`, replace line 53:
```python
TRIAGE_V2 = WorkflowSpec("document_triage", 2, (
    StepSpec("ingest", "ingest", max_attempts=3),
    StepSpec("extract", "extract", max_attempts=3),
    StepSpec("redact", "redact", max_attempts=3),
    StepSpec("classify", "classify", max_attempts=2),
    StepSpec("notify", "notify", max_attempts=5, on_failure="skip"),
))

SPECS: Dict[Tuple[str, int], WorkflowSpec] = {TRIAGE_V1.key: TRIAGE_V1, TRIAGE_V2.key: TRIAGE_V2}
```

`wfk/cli.py` line 23: add `RedactTool` to the import; line 30: add `"redact": RedactTool(),` to
the registry dict. `tests/conftest.py` line 10 and line 50: the same two additions.

`tests/test_redact.py`:
```python
from wfk.spec import TRIAGE_V1, TRIAGE_V2

from .conftest import INPUT


def test_v2_redacts_and_v1_is_untouched(make_engine):
    engine = make_engine()
    v2 = engine.start(TRIAGE_V2, INPUT)
    assert engine.run(v2) == "SUCCEEDED"
    redact = engine.store.load_step(v2, "redact")
    assert redact.output["body"] == "Total due: [amount]. Payment terms: net 30."
    assert engine.store.events(v2, "DECISION")[0].payload == {"key": "redactions", "data": {"email": 0, "amount": 1}}
    assert [s.name for s in engine.store.load_steps(v2)] == ["ingest", "extract", "redact", "classify", "notify"]

    v1 = engine.start(TRIAGE_V1, INPUT)   # a workflow pinned to v1 never sees the new step
    assert engine.run(v1) == "SUCCEEDED"
    assert [s.name for s in engine.store.load_steps(v1)] == ["ingest", "extract", "classify", "notify"]
```
CLI check: `python -m wfk --db /tmp/x.db start examples/doc.json --id v2 --spec document_triage@2`
shows `redact SUCCEEDED 1/3 {"body": "... Total due: [amount] for cloud storage ..."}`.

**Pitfalls:** editing `TRIAGE_V1` in place (a running v1 workflow would resolve a 5-step spec
against 4 step rows; `load_step("redact")` → `NotFound`). Forgetting `conftest.registry` (every
test fails with "needs tools not in the registry: ['redact']" the moment any spec references it;
with v1 tests still passing, only the new test fails, which is a hint that the registry is the
missing piece). Should classify read `inputs["redact"]["body"]`? Say it out loud: "classify still
reads `extract`; if redaction must feed classification, that is a v2 `ClassifyTool` reading
`inputs.get("redact", inputs["extract"])`, and I'd version the tool too."

### 6.2 Per-step timeout

**Prompt:** "A tool can hang. Add a per-step timeout."

**Plan:** `timeout_s` on `StepSpec`; the engine runs the tool in a daemon thread and `join`s with
the timeout; on expiry raise `RetryableError("timed out")` (so the retry policy applies) and mark
the `StepContext` closed so a late `record_decision`/`run_effect` from the abandoned thread is
refused. **Pitfall you must say unprompted:** the SQLite connection is thread-bound by default
(`check_same_thread=True`); a tool thread calling `ctx.record_decision` would get
`ProgrammingError: SQLite objects created in a thread can only be used in that same thread`
(treated as retryable by D4, so the symptom is a retried step with that error in `history`). Since
the engine thread is blocked in `join`, access is still serialized, so `check_same_thread=False` is
safe here. And the C3 caveat: a timeout *inside* an effect becomes an ambiguity on the next attempt,
by design.

`wfk/spec.py`:
```python
-from typing import Dict, Tuple
+from typing import Dict, Optional, Tuple
...
     backoff_base_s: float = 0.05       # delay = min(base * 2**(attempt-1), Engine.BACKOFF_CAP_S)
+    timeout_s: Optional[float] = None  # per attempt; expiry is a RetryableError

     def __post_init__(self) -> None:
+        if self.timeout_s is not None and self.timeout_s <= 0:
+            raise ValueError(f"step {self.name}: timeout_s must be > 0")
         if self.max_attempts < 1:
```

`wfk/store.py` line 199:
```python
-        self.conn = sqlite3.connect(path, isolation_level=None)  # explicit BEGIN/COMMIT only
+        # explicit BEGIN/COMMIT only; check_same_thread=False lets a timed-out tool thread write
+        # through ctx.* (the engine thread is blocked in join, so access is still serialized)
+        self.conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
```

`wfk/engine.py`:
```python
 import hashlib
+import threading
 import time
...
-from .tools import FatalError, Tool
+from .tools import FatalError, RetryableError, Tool
```
In `StepContext.__init__` (after line 50) and the two services:
```python
         self._engine, self._owner, self._tool = engine, owner, tool
+        self.closed = False  # set by the engine when the attempt timed out
+
+    def _open(self) -> None:
+        if self.closed:
+            raise StoreError(f"step {self.step_name} attempt {self.attempt} is over; late write refused")

     def record_decision(self, key: str, data: Dict[str, Any]) -> None:
         """Write a DECISION event: what the tool decided and why."""
+        self._open()
         self._engine.store.append_event(...)
...
     def run_effect(self, name: str, payload: Dict[str, Any], fn: EffectFn) -> Dict[str, Any]:
+        self._open()
         return self._engine._run_effect(...)
```
In `_run_step` line 217:
```python
-                output = tool.run(ctx, self._inputs(wf_id))  # the only place a tool runs; no transaction open
+                output = self._call(tool, ctx, self._inputs(wf_id), step.timeout_s)  # the only place a tool runs
```
New method before `_give_up`:
```python
    def _call(self, tool: Tool, ctx: StepContext, inputs: Dict[str, Any],
              timeout_s: Optional[float]) -> Dict[str, Any]:
        """Run the tool; with a timeout, in a daemon thread that is abandoned on expiry
        (its late ctx.* calls are refused because the context is closed)."""
        if timeout_s is None:
            return tool.run(ctx, inputs)
        box: Dict[str, Any] = {}

        def target() -> None:
            try:
                box["out"] = tool.run(ctx, inputs)
            except BaseException as exc:  # re-raised in the engine thread
                box["exc"] = exc

        t = threading.Thread(target=target, daemon=True)
        t.start()
        t.join(timeout_s)
        if t.is_alive():
            ctx.closed = True
            raise RetryableError(f"timed out after {timeout_s}s")
        if "exc" in box:
            raise box["exc"]
        return box["out"]
```

Test (`tests/test_drills2.py`):
```python
class Hangs:
    name, side_effect_free, idempotent = "extract", True, True

    def __init__(self):
        self.calls = 0

    def run(self, ctx, inputs):
        self.calls += 1
        if self.calls == 1:
            time.sleep(0.5)          # first attempt hangs well past the timeout
            ctx.record_decision("late", {})   # refused: the attempt is over
        ctx.record_decision("on_time", {"attempt": ctx.attempt})  # a normal write from the tool thread
        return {"title": "t", "body": "b", "word_count": 2}


def test_step_timeout_retries(make_engine, notifications):
    spec = WorkflowSpec("triage_timeout", 1, (
        StepSpec("ingest", "ingest"),
        StepSpec("extract", "extract", max_attempts=2, timeout_s=0.05),
        StepSpec("classify", "classify"), StepSpec("notify", "notify", on_failure="skip")))
    tool = Hangs()
    engine = make_engine(registry(FileNotifier(notifications), extract=tool), specs={spec.key: spec})
    wf_id = engine.start(spec, INPUT)
    assert engine.run(wf_id) == "SUCCEEDED"
    assert tool.calls == 2
    failed = engine.store.events(wf_id, "ATTEMPT_FAILED")[0].payload
    assert failed["error"] == "timed out after 0.05s" and failed["retryable"]
    time.sleep(0.6)                                             # let the abandoned thread finish
    mine = [e.payload["key"] for e in engine.store.events(wf_id, "DECISION") if e.step_name == "extract"]
    assert mine == ["on_time"]                                  # attempt 2's write landed, the late one was refused
```
**Pitfalls:** `SimulatedCrash` inside the thread must be re-raised in the engine thread (hence
`except BaseException` in `target`); without that a crash in a timed tool would be swallowed. Say:
"Python cannot kill a thread; the honest version abandons it and fences its writes. In production
the tool is a service call with a deadline, and the fence is the lease token."

### 6.3 Workflow-level timeout (deadline)

**Prompt:** "A triage that takes more than N seconds end to end should fail."

**Plan:** `deadline_s` on `WorkflowSpec`, measured from `workflows.created_at` (already persisted,
survives restarts); checked between steps (cooperative, like cancel); outcome is `FAILED` with a
reason, no new state or event type.

`wfk/spec.py`:
```python
     steps: Tuple[StepSpec, ...]
+    deadline_s: Optional[float] = None   # workflow-level budget, measured from created_at

     def __post_init__(self) -> None:
+        if self.deadline_s is not None and self.deadline_s <= 0:
+            raise ValueError(f"spec {self.name}: deadline_s must be > 0")
         names = [s.name for s in self.steps]
```
`wfk/engine.py` `_run_owned`, before the step loop (line 165):
```python
+        deadline = from_iso(wf.created_at) + spec.deadline_s if spec.deadline_s else None
         for step in spec.steps:
+            if deadline is not None and self.store.clock() > deadline:
+                return self._finish(wf_id, owner, "FAILED", f"deadline of {spec.deadline_s}s exceeded before step {step.name}")
             row = self.store.load_step(wf_id, step.name)
```
Test:
```python
def test_workflow_deadline_fails_between_steps(make_engine, clock):
    spec = WorkflowSpec("triage_deadline", 1, TRIAGE_V1.steps, deadline_s=10.0)
    engine = make_engine(specs={spec.key: spec})
    wf_id = engine.start(spec, INPUT)
    clock.t += 11                                         # the workflow sat in a queue for 11 s
    assert engine.run(wf_id) == "FAILED"
    last = engine.store.events(wf_id, "WORKFLOW_STATE")[-1].payload
    assert last["to"] == "FAILED" and "deadline" in last["reason"]
    assert all(s.attempt == 0 for s in engine.store.load_steps(wf_id))   # nothing ran
```
**Pitfalls:** checking inside `_run_step`'s `while True` too (between retries) is a one-line
extension; say it. A deadline hit *during* a tool is the per-step timeout's job. If they want a
distinct terminal state `TIMED_OUT`: that is a new `WF_ALLOWED` edge `("RUNNING","TIMED_OUT")`,
`WF_TERMINAL`, and the reducer needs nothing new because `WORKFLOW_STATE` carries `to`; but say
you'd rather keep the machine closed and put it in the reason.

### 6.4 Parallel / fan-out steps

**Prompt:** "Notify several recipients" or "run steps in parallel."

**Ask first:** a DAG of steps, or fan-out inside one step? For the brief's workflow, fan-out
inside the step is right: each recipient is its own ledger effect (`effect_name = "email:<addr>"`),
so a crash mid-way leaves some DONE and one CLAIMED, and resume finishes only the unsent ones.

`wfk/tools.py`, append:
```python
class FanOutNotifyTool:
    """Fan-out inside one step: one email per recipient, each its own ledger effect
    (effect_name = "email:<address>"), sent one after another. A crash mid-way leaves some
    DONE and one CLAIMED; resume finishes only the ones that are not DONE. (Sequential on
    purpose: one Store connection, one writer. Real concurrency needs a Store per thread.)"""
    name, side_effect_free = "notify", False

    def __init__(self, sink: Sink) -> None:
        self.sink = sink
        self.idempotent = sink.idempotent

    def run(self, ctx: "StepContext", inputs: Dict[str, Any]) -> Dict[str, Any]:
        recipients = inputs.get("notify_to_all") or [inputs.get("notify_to", "triage@example.com")]
        label, title = inputs["classify"]["label"], inputs["extract"]["title"]

        def one(to: str) -> Dict[str, Any]:
            payload = {"to": to, "subject": f"[{label}] {title}", "body": f"classified as {label}"}
            return ctx.run_effect(f"email:{to}", payload, self.sink.send)

        return {"emails": {to: one(to) for to in recipients}}
```
Test (`tests/test_drills3.py`):
```python
class CrashOnSecond:
    """Receiver that accepts the first send, then the process dies."""
    idempotent = True

    def __init__(self, inner):
        self.inner, self.calls = inner, 0

    def send(self, key, payload):
        self.calls += 1
        r = self.inner.send(key, payload)
        if self.calls == 2:
            raise SimulatedCrash("died after the second recipient accepted")
        return r


def test_fan_out_resume_finishes_only_the_unsent(make_engine, notifications):
    people = ["a@x.com", "b@x.com", "c@x.com"]
    inp = {**INPUT, "notify_to_all": people}
    sink1 = FileNotifier(notifications)
    engine1 = make_engine(registry(sink1, notify=FanOutNotifyTool(CrashOnSecond(sink1))))
    wf_id = engine1.start(TRIAGE_V1, inp)
    with pytest.raises(SimulatedCrash):
        engine1.run(wf_id)
    statuses = sorted(e.status for e in engine1.store.effects_for(wf_id))
    assert statuses == ["CLAIMED", "DONE"]          # a@ done, b@ accepted but unrecorded, c@ never claimed

    sink2 = FileNotifier(notifications)
    engine2 = make_engine(registry(sink2, notify=FanOutNotifyTool(sink2)))
    assert engine2.run(wf_id, take_over=True) == "SUCCEEDED"
    assert sink2.calls == 2                          # b@ (resend, deduped) and c@; a@ came from the ledger
    with open(notifications) as f:
        assert len(f.readlines()) == 3               # exactly one line per recipient
    out = engine2.store.load_step(wf_id, "notify").output["emails"]
    assert out["a@x.com"]["status"] == "sent" and out["b@x.com"]["status"] == "duplicate"
    assert len(engine2.store.events(wf_id, "EFFECT_AMBIGUOUS")) == 1
```
**Pitfalls (I hit the first one building this):** a `ThreadPoolExecutor` calling `ctx.run_effect`
from worker threads fails with the SQLite thread error (every attempt, then SKIPPED after 5).
Concurrency needs one `Store` per thread or a lock around the connection; say so and keep the drill
sequential. For a true DAG: `depends_on: Tuple[str, ...]` on `StepSpec`, a ready-set loop instead
of the ordered `for`, and the resume scan already handles several RUNNING steps (it loops over all).

### 6.5 Cancellation mid-step (cooperative, inside a tool)

**Prompt:** "Cancel currently waits for the running tool. Let a long tool notice it."

**Plan:** `ctx.check_cancel()` reads the flag and raises `Cancelled`; `_run_step` catches it
*before* the generic handlers, moves the step `RUNNING → PENDING` with a new explanatory event
`ATTEMPT_CANCELLED`, returns `"CANCELLED"`; the reducer treats the new event like
`ATTEMPT_INTERRUPTED`; `cli._NARRATORS` gets a line. Never call `check_cancel` inside an effect.

`wfk/engine.py`:
```python
+class Cancelled(Exception):
+    """Raised by ctx.check_cancel(): the tool stopped because cancel was requested."""
+
+
 class AmbiguousEffect(FatalError):
```
In `StepContext`:
```python
+    def check_cancel(self) -> None:
+        """Long tools call this between units of work, never inside an effect."""
+        if self._engine.store.cancel_requested(self.workflow_id):
+            raise Cancelled(f"step {self.step_name} attempt {self.attempt}: cancel requested")
```
In `_run_step`, first handler after the `try` (before `except FatalError`):
```python
+            except Cancelled as e:
+                self.store.transition_step(wf_id, step.name, "RUNNING", "PENDING", owner,
+                                           [("ATTEMPT_CANCELLED", {"attempt": attempt, "error": str(e)})],
+                                           attempt_no=attempt, error=str(e))
+                return "CANCELLED"
             except FatalError as e:
```
In `rebuild_state`:
```python
-        elif t == "ATTEMPT_INTERRUPTED":
+        elif t in ("ATTEMPT_INTERRUPTED", "ATTEMPT_CANCELLED"):
             _move(st, "RUNNING", "PENDING", STEP_ALLOWED, where)
+            if t == "ATTEMPT_CANCELLED":
+                st["error"] = pl["error"]
```
`wfk/cli.py` `_NARRATORS`:
```python
+    "ATTEMPT_CANCELLED": lambda p: f"attempt {p['attempt']} stopped: {p['error']}",
```
Test:
```python
class CancelsItself:
    name, side_effect_free, idempotent = "classify", True, True

    def __init__(self, engine):
        self.engine, self.calls = engine, 0

    def run(self, ctx, inputs):
        self.calls += 1
        self.engine.cancel(ctx.workflow_id, by="operator")   # arrives while the tool is mid-work
        ctx.check_cancel()                                   # the tool notices and stops
        raise AssertionError("unreachable")


def test_cancel_mid_step(make_engine, notifications):
    engine = make_engine()
    tool = CancelsItself(engine)
    engine.registry = registry(FileNotifier(notifications), classify=tool)
    wf_id = engine.start(TRIAGE_V1, INPUT)
    assert engine.run(wf_id) == "CANCELLED"
    assert tool.calls == 1
    step = engine.store.load_step(wf_id, "classify")
    assert step.state == "PENDING" and step.attempt == 1
    assert [e.type for e in engine.store.events(wf_id) if e.step_name == "classify"] == ["ATTEMPT_STARTED", "ATTEMPT_CANCELLED"]
    assert engine.store.load_step(wf_id, "notify").attempt == 0
    from wfk.engine import consistency_diff
    assert consistency_diff(engine.store, wf_id) == []
```
**Pitfalls:** `Cancelled` must not subclass `FatalError` or `StoreError` (it would be caught by the
wrong handler); the reducer branch is mandatory or `replay` reports `step classify.state: log says
RUNNING, table says PENDING`; the cancelled attempt is charged (consistent with interrupted).

### 6.6 Jittered exponential backoff

**Prompt:** "Thundering herd: add jitter."

**Plan:** inject randomness like the clock (`rand` callable on `Engine`), apply full jitter
`delay * uniform(0.5, 1.0)`; the persisted `delay_s`/`next_run_at` already make replay
deterministic (the log records the jittered value, not the formula). Tests default `rand` to 1.0 so
the existing `[0.05, 0.1]` assertion holds.

`wfk/engine.py`:
```python
 import hashlib
+import random
...
     def __init__(self, store: Store, registry: Dict[str, Tool],
                  specs: Optional[Dict[Tuple[str, int], WorkflowSpec]] = None,
-                 sleep: Callable[[float], None] = time.sleep) -> None:
-        self.store, self.registry, self.sleep = store, registry, sleep
+                 sleep: Callable[[float], None] = time.sleep,
+                 rand: Callable[[], float] = random.random) -> None:
+        self.store, self.registry, self.sleep, self.rand = store, registry, sleep, rand
...
                 delay = min(step.backoff_base_s * 2 ** (attempt - 1), self.BACKOFF_CAP_S)
+                delay = round(delay * (0.5 + 0.5 * self.rand()), 6)  # full jitter in [delay/2, delay]
                 next_run_at = to_iso(self.store.clock() + delay)
```
`tests/conftest.py` in `_make`:
```python
+        kw.setdefault("rand", lambda: 1.0)  # no jitter unless a test injects it
         return Engine(store, reg or registry(FileNotifier(notifications)), sleep=clock.sleep, **kw)
```
Test:
```python
def test_jitter_is_bounded_and_persisted(make_engine, notifications, clock):
    engine = make_engine(registry(FileNotifier(notifications), extract=Flaky(ExtractTool(), 2)), rand=lambda: 0.0)
    wf_id = engine.start(TRIAGE_V1, INPUT)
    assert engine.run(wf_id) == "SUCCEEDED"
    delays = [r.payload["delay_s"] for r in engine.store.events(wf_id, "RETRY_SCHEDULED")]
    assert delays == [0.025, 0.05]                       # half of 0.05 and 0.1: the lower bound
    assert [round(s, 3) for s in clock.slept] == delays  # the persisted value is what was slept
```
**Pitfalls:** `random.random()` without injection makes `test_retry_then_success` flaky; `round`
keeps the stored float readable; a per-step cap is one more `StepSpec` field (`backoff_cap_s`)
replacing `self.BACKOFF_CAP_S` in that one line.

### 6.7 Per-tenant concurrency limit (priority / rate limiting)

**Prompt:** "Multi-tenant: one noisy tenant must not starve the others."

**Plan:** tenant comes from the input (`input["tenant"]`, no schema change: `json_extract` on
`input_json`); the limit is enforced *inside the claim transaction* (count + claim under one
`BEGIN IMMEDIATE`, so two engines cannot both squeeze in); refusal is a new `StoreError`
(`TenantBusy`), nothing written. Priority: say that a scheduler verb (`run-next`) would pick
`PENDING` rows ordered by `json_extract(input_json, '$.priority')`; the kernel is unchanged.

`wfk/store.py`:
```python
+class TenantBusy(StoreError):
+    """The tenant's concurrency limit is reached; try again later. Nothing was written."""
+
+
 class AlreadyOwned(StoreError):
...
+    def running_for_tenant(self, tenant: str) -> int:
+        """Live workflows of a tenant: RUNNING (or claimed PENDING) rows whose input names it."""
+        return self.conn.execute(
+            "SELECT count(*) FROM workflows WHERE owner IS NOT NULL AND state IN ('PENDING', 'RUNNING')"
+            " AND json_extract(input_json, '$.tenant') = ?", (tenant,)).fetchone()[0]
+
-    def claim(self, wf_id: str, owner: str, take_over: bool = False) -> None:
-        """Become the one engine running this workflow."""
+    def claim(self, wf_id: str, owner: str, take_over: bool = False,
+              tenant_limit: Optional[int] = None) -> None:
+        """... With ``tenant_limit`` the claim is refused (``TenantBusy``) when the workflow's
+        tenant already has that many live workflows; count and claim share one transaction."""
         with self._tx():
+            if tenant_limit is not None:
+                tenant = self.load_workflow(wf_id).input.get("tenant")
+                if tenant is not None and self.running_for_tenant(tenant) >= tenant_limit:
+                    raise TenantBusy(f"tenant {tenant!r} already has {tenant_limit} live workflows")
             if take_over:
```
`wfk/engine.py`: `tenant_limit: Optional[int] = None` on `__init__` (stored as `self.tenant_limit`)
and `self.store.claim(workflow_id, owner, take_over, tenant_limit=self.tenant_limit)` at line 117.

Test:
```python
def test_tenant_limit_refuses_second_live_workflow(make_engine):
    engine = make_engine(tenant_limit=1)
    a = engine.start(TRIAGE_V1, {**INPUT, "tenant": "acme"})
    b = engine.start(TRIAGE_V1, {**INPUT, "tenant": "acme"})
    engine.store.claim(a, "live-engine")             # acme already has one live workflow
    with pytest.raises(TenantBusy):
        engine.run(b)
    assert engine.store.load_workflow(b).owner is None and len(engine.store.events(b)) == 1
    engine.store.release(a, "live-engine")
    assert engine.run(b) == "SUCCEEDED"
```
**Pitfalls:** counting outside the claim transaction is a race; `TenantBusy` must be a
`StoreError` so the CLI maps it to exit 4 with a message (or give it its own exit code); say that
the real version is a `tenant_id` column (D14) and the first component of the effect key.

### 6.8 A new effect type whose receiver is NOT idempotent

**Prompt:** "Add an archive step that writes to a legacy system which cannot dedupe."

**Plan:** second effectful tool through `run_effect` with its own `effect_name`; `idempotent`
copied from the receiver (False); the kernel already does the right thing: an ambiguous claim
fails the step with `ambiguous_effect` and leaves the row CLAIMED. The design question to voice:
`on_failure` for that step (`fail_workflow` = stop and page a human; `skip` = continue and page).

`wfk/tools.py`, append:
```python
class ArchiveTool:
    """Second effectful step: stores the document in an archive whose receiver does NOT
    honour the key (like a legacy SMTP relay). The ledger still records the attempt;
    an ambiguous claim therefore fails the step instead of re-sending."""
    name, side_effect_free = "archive", False

    def __init__(self, sink: Sink) -> None:
        self.sink = sink
        self.idempotent = sink.idempotent

    def run(self, ctx: "StepContext", inputs: Dict[str, Any]) -> Dict[str, Any]:
        payload = {"to": "archive://triage", "doc_id": inputs["ingest"]["doc_id"],
                   "label": inputs["classify"]["label"]}
        return {"archive": ctx.run_effect("put", payload, self.sink.send)}
```
Test:
```python
def test_non_idempotent_archive_fails_on_ambiguity(make_engine, tmp_path):
    spec = WorkflowSpec("triage_archive", 1, TRIAGE_V1.steps + (
        StepSpec("archive", "archive", max_attempts=3),))
    specs = {**SPECS, spec.key: spec}
    archive_file = str(tmp_path / "archive.jsonl")
    sink1 = FileNotifier(archive_file, idempotent=False)
    engine1 = make_engine(registry(FileNotifier(str(tmp_path / "n.jsonl")),
                                   archive=ArchiveTool(CrashingSink(sink1))), specs=specs)
    wf_id = engine1.start(spec, INPUT)
    with pytest.raises(SimulatedCrash):
        engine1.run(wf_id)
    sink2 = FileNotifier(archive_file, idempotent=False)
    engine2 = make_engine(registry(FileNotifier(str(tmp_path / "n.jsonl")), archive=ArchiveTool(sink2)), specs=specs)
    assert engine2.run(wf_id, take_over=True) == "FAILED"
    assert sink2.calls == 0                                     # never re-sent
    assert engine2.store.get_effect(effect_key(wf_id, "archive", "put")).status == "CLAIMED"
    assert engine2.store.events(wf_id, "STEP_FAILED")[0].payload["reason"] == "ambiguous_effect"
    assert engine2.store.load_step(wf_id, "notify").state == "SUCCEEDED"   # earlier effect untouched
```
**Pitfall I hit:** my first payload had no `"to"`, so `FileNotifier.send` raised `KeyError: 'to'`
*after* the ledger row was CLAIMED. D4 treated the `KeyError` as retryable; attempt 2 found the
CLAIMED row, wrote `EFFECT_AMBIGUOUS`, and failed with `ambiguous_effect`, and the test said "DID NOT
RAISE SimulatedCrash". Reading `history` (`ATTEMPT_FAILED 'to'` then `EFFECT_AMBIGUOUS`) found it in
one minute. Lesson to say aloud: a bug in the receiver call lands as an ambiguity, which is the
kernel being honest; the fix is in the tool.

### 6.9 Replay shows a diff after tampering

**Prompt:** "Prove replay detects a corrupted row" / "what if the log and tables disagree?"

**Plan:** `consistency_diff` already does it; the drill is to show it three ways and read the output.

Table tampered (`test_replay_detects_tampered_table` does exactly this):
```
sqlite3 wfk.db "UPDATE steps SET attempt = 7 WHERE workflow_id = 't' AND name = 'classify'"
python -m wfk --db wfk.db replay t
...
--- derived from 16 events: workflow SUCCEEDED, steps ingest=SUCCEEDED, extract=SUCCEEDED, classify=SUCCEEDED, notify=SUCCEEDED
INCONSISTENT:
  step classify.attempt: log says 1, table says 7
```
(exit 1). Log tampered (an event deleted):
```
sqlite3 wfk.db "DELETE FROM events WHERE workflow_id = 't' AND type = 'EFFECT_DONE'"
INCONSISTENT:
  effect 543148adf389.status: log says 'CLAIMED', table says 'DONE'
  effect 543148adf389.result: log says None, table says {'key': '543148adf389...', 'status': 'sent', 'to': 'ap-team@example.com'}
```
Log made illegal (an event inserted that skips RUNNING; `test_replay_rejects_illegal_log`):
```
error: event 17 STEP_SUCCEEDED: SUCCEEDED -> SUCCEEDED (event claims from RUNNING)
```
(exit 4, raised by `_move` in the reducer). If asked to add a test for the deleted-event case:
```python
def test_replay_detects_missing_event(make_engine, db_path, capsys):
    engine = make_engine()
    wf_id = engine.start(TRIAGE_V1, INPUT)
    engine.run(wf_id)
    engine.store.conn.execute("DELETE FROM events WHERE workflow_id = ? AND type = 'EFFECT_DONE'", (wf_id,))
    diffs = consistency_diff(engine.store, wf_id)
    assert any(d.startswith("effect ") and "log says 'CLAIMED', table says 'DONE'" in d for d in diffs)
    assert cli.main(["--db", db_path, "replay", wf_id]) == 1
```
**Pitfalls:** the diff compares *derived* fields only (`cmp` iterates `derived.items()`), so a
column the reducer does not track (e.g. `updated_at`) is never reported; say that is deliberate.
Tamper-evidence (hash-chaining `prev_hash`) is prose in D14, not built.

### 6.10 Bug hunt: three seeded bugs, found with tests and `history`

Verified by seeding each bug alone in a clean copy and running the suite.

**Bug 1: the effect key includes the attempt number.**
`engine.py:269` becomes `key = effect_key(wf_id, step_name, f"{name}#{attempt}")`.
*Caught by 5 tests:* `test_notify_effect_once_across_crash`, `test_ambiguous_non_idempotent_fails_safely`,
`test_done_effect_is_not_resent_on_resume`, `test_replay_rebuild_equals_tables`,
`test_fenced_out_engine_cannot_finish_the_effect`. *How you'd find it from `history`:* after a
crash/resume there are two `EFFECT_CLAIMED` lines with different keys and no `EFFECT_AMBIGUOUS`;
`notifications.jsonl` has two lines. Symptom → "the key changed between attempts" → `effect_key`.

**Bug 2: the state fence is dropped from `transition_step`.**
`store.py:358-359` loses `AND state = ?` (and `frm` from the params).
*Caught by: no existing test.* This is a real gap; say so and close it on the spot:
```python
def test_stale_row_view_cannot_commit(make_engine):
    """The UPDATE must carry WHERE state = frm (the second fence)."""
    engine = make_engine()
    wf_id = engine.start(TRIAGE_V1, INPUT)
    engine.store.transition_step(wf_id, "ingest", "PENDING", "RUNNING", None,
                                 [("ATTEMPT_STARTED", {"attempt": 1})], attempt_no=1, attempt=1)
    with pytest.raises(LostClaim):   # a stale view still thinks it is PENDING
        engine.store.transition_step(wf_id, "ingest", "PENDING", "RUNNING", None,
                                     [("ATTEMPT_STARTED", {"attempt": 1})], attempt_no=1, attempt=1)
    assert engine.store.load_step(wf_id, "ingest").state == "RUNNING"
```
(passes on clean code, fails with the bug). *From `history`:* the log would show two
`ATTEMPT_STARTED attempt 1` for one step and `replay` would raise `RUNNING -> RUNNING (event claims
from PENDING)`: the reducer is the second line of defence.

**Bug 3: the reducer forgets to clear `next_run_at` on `ATTEMPT_STARTED`.**
`engine.py:323` becomes `st["attempt"] = pl["attempt"]`.
*Caught by 1 test:* `test_replay_rebuild_equals_tables` (after a retry the table has
`next_run_at = NULL`, the projection still has the deadline). *From the CLI:* `replay` on any
retried workflow prints `step extract.next_run_at: log says '2026-...', table says None`. Symptom →
"the table is right, the reducer is wrong" → the `ATTEMPT_STARTED` branch.

**Two more, for the gap conversation (each caught by no existing test; the fixing test is shown):**

Bug 4: `Engine.run` loses the `release` in its `except Exception` (claim leaks on an error).
```python
class KernelBug:
    name, side_effect_free, idempotent = "classify", True, True

    def run(self, ctx, inputs):
        raise StoreError("simulated kernel failure inside ctx.*")


def test_engine_error_releases_claim(make_engine, notifications):
    engine = make_engine(registry(FileNotifier(notifications), classify=KernelBug()))
    wf_id = engine.start(TRIAGE_V1, INPUT)
    with pytest.raises(StoreError):
        engine.run(wf_id)
    assert engine.store.load_workflow(wf_id).owner is None
```
Bug 5: `retryable = attempt <= row.max_attempts` (off by one). `history` shows the tell:
```
  12  classify  ATTEMPT_FAILED       attempt 2 failed: classify: transient failure #2 (retryable)
  13  classify  RETRY_SCHEDULED      retry in 0.1s at ...: attempt 2 of 2 failed
  14  classify  STEP_FAILED          FAILED (exhausted): attempt 2 of 2 was interrupted; no attempts left (error before it: ...)
```
A retry scheduled for "attempt 2 of 2", and a STEP_FAILED that lies ("was interrupted"). The test:
```python
def test_last_attempt_is_not_retryable(make_engine, notifications):
    engine = make_engine(registry(FileNotifier(notifications), classify=Flaky(ClassifyTool(), 99)))
    wf_id = engine.start(TRIAGE_V1, INPUT)
    assert engine.run(wf_id) == "FAILED"
    assert len(engine.store.events(wf_id, "RETRY_SCHEDULED")) == 1          # max_attempts 2 -> one retry
    assert engine.store.events(wf_id, "ATTEMPT_FAILED")[-1].payload["retryable"] is False
    assert engine.store.load_step(wf_id, "classify").error == "classify: transient failure #2"
```
**How to say it:** "The suite pins the guarantees (key stability, fence on owner, replay). Two
mechanical fences are under-tested: the `WHERE state = ?` and the error-path release. Here are the
two tests." Owning a gap beats pretending there is none.

### 6.11 Warm-ups (5 minutes each)

- **`status --json`:** in `cmd_status`, `if a.json: print(json.dumps({"workflow": wf.__dict__,
  "steps": [s.__dict__ for s in store.load_steps(wf.id)], "effects": [e.__dict__ for e in
  store.effects_for(wf.id)]}, sort_keys=True))`; add `st.add_argument("--json", action="store_true")`.
  Frozen dataclasses expose `__dict__`; `dataclasses.asdict` also works.
- **`status --state RUNNING`:** `list_workflows(state: Optional[str] = None)` appending
  `WHERE state = ?`.
- **Operator verb `effects resolve <key> --as done|failed`:** a fenced store method that moves
  `CLAIMED → DONE` with `result={"resolved_by": "operator"}` and appends `EFFECT_RESOLVED{by}`;
  reducer branch; then `run` continues. Test: non-idempotent ambiguity, resolve, workflow SUCCEEDED,
  receiver never called.

---

## 7. Debugging playbook

**Reading `history <id>`.** Format: `seq  HH:MM:SS.mmm  step|workflow  TYPE  narrative`. A healthy
clean run is exactly 16 events in the order of §4.1; memorize that shape and diff against it.
Patterns:

| You see | It means |
|---|---|
| `ATTEMPT_FAILED ... (retryable)` followed by `RETRY_SCHEDULED retry in Xs` | normal retry; X doubles each time from `backoff_base_s` |
| `ATTEMPT_FAILED ... (not retryable)` then `STEP_FAILED (fatal)` | the tool raised `FatalError` |
| `STEP_FAILED (exhausted)` after N `RETRY_SCHEDULED` | budget used up (N = max_attempts−1) |
| `STEP_FAILED (exhausted): attempt N of N was interrupted` with no preceding `ATTEMPT_FAILED` for N | crashed on the last attempt; resumed with no budget (the `PENDING → FAILED` edge) |
| `OWNER_CLAIMED ... (take-over)` then `ATTEMPT_INTERRUPTED` | a resume after a crash mid-attempt |
| `OWNER_CLAIMED` without take-over, no `OWNER_RELEASED` at the end, workflow not terminal | the process died (or is still running) |
| `EFFECT_CLAIMED` with no `EFFECT_DONE` and nothing after | C3/C4: crashed during or right after the receiver call |
| `EFFECT_AMBIGUOUS ... -> resend` then `EFFECT_DONE {"status": "duplicate"}` | the receiver deduped; one effect |
| `EFFECT_AMBIGUOUS ... -> fail` then `STEP_FAILED (ambiguous_effect)` | non-idempotent receiver; operator needed; effect row still CLAIMED |
| `DECISION decision label: {...}` | tool-authored; `--kind DECISION` filters to these |
| `STEP_SKIPPED (exhausted, policy=skip)` | `on_failure=skip` applied; workflow continues |
| `CANCEL_REQUESTED by cli` then `WORKFLOW_STATE RUNNING -> CANCELLED` | cooperative cancel honoured between attempts; the running step is left PENDING |
| error text `SQLite objects created in a thread can only be used in that same thread` | a tool called `ctx.*` from another thread (see §6.2) |
| error text `KeyError: 'x'` retried N times | D4: an unexpected exception is retried; the tool has a bug (see §6.8) |

Use `--kind TYPE` to filter and `--json` for machine-readable lines (one JSON object per event,
with `seq`, `attempt`, `payload`).

**Which test isolates which guarantee:**

| Guarantee | Test | File |
|---|---|---|
| exponential backoff, `next_run_at` persisted then cleared, earlier steps untouched | `test_retry_then_success` | `test_retry.py` |
| exhaustion fails the workflow, later steps never start, claim released | `test_exhaustion_fails_workflow` | `test_failure_policy.py` |
| `on_failure=skip` lets the workflow succeed | `test_skip_policy_lets_workflow_succeed` | `test_failure_policy.py` |
| one effect across a crash with an idempotent receiver (C3/C4) | `test_notify_effect_once_across_crash` | `test_idempotency.py` |
| non-idempotent receiver → `ambiguous_effect`, row left CLAIMED, one send ever | `test_ambiguous_non_idempotent_fails_safely` | `test_idempotency.py` |
| DONE row returned without calling the receiver (C5) | `test_done_effect_is_not_resent_on_resume` | `test_idempotency.py` |
| crash between steps: nothing re-run; stale claim blocks; `--take-over` clears | `test_restart_resumes_without_rerunning` | `test_restart.py` |
| a second owner is refused | `test_claim_blocks_second_owner` | `test_restart.py` |
| crash during backoff waits only the remainder | `test_restart_during_backoff_waits_the_remainder` | `test_restart.py` |
| a stalled engine that lost its claim cannot commit anything after a take-over | `test_fenced_out_engine_cannot_finish_the_effect` | `test_restart.py` |
| reducer reproduces every row after retry + crash + resume; `replay` prints CONSISTENT | `test_replay_rebuild_equals_tables` | `test_replay.py` |
| a hand-edited row makes `replay` exit 1 | `test_replay_detects_tampered_table` | `test_replay.py` |
| an illegal log raises `IllegalTransition` | `test_replay_rejects_illegal_log` | `test_replay.py` |
| illegal transition writes nothing | `test_illegal_transition_raises_and_writes_nothing` | `test_transitions.py` |
| owner fence on transitions / on ledger and loose events | `test_stale_owner_cannot_commit` / `test_stale_owner_cannot_write_ledger_or_events` | `test_transitions.py` |
| cooperative cancel between attempts; cancel before first run; terminal refuses | `test_cancel_between_attempts`, `test_cancel_before_first_run` | `test_transitions.py` |
| `FatalError` → one attempt, no retry, later steps PENDING | `test_fatal_error_fails_without_retry` | `test_transitions.py` |
| input validated before persisting; duplicate id refused | `test_start_rejects_bad_input_and_duplicate_id` | `test_transitions.py` |
| a finished workflow is never claimed | `test_run_on_terminal_workflow_does_not_claim` | `test_transitions.py` |
| a cancel that lands during a backoff sleep stops the next attempt | `test_cancel_during_backoff_is_honoured_before_the_next_attempt` | `test_transitions.py` |
| a non-JSON tool output fails once (`fatal`), never retried | `test_non_json_output_is_fatal_not_retried` | `test_transitions.py` |
| a clock that stepped back an hour waits the 2 s cap, not 3600 s | `test_backoff_wait_is_clamped_when_the_clock_steps_backwards` | `test_restart.py` |
| exit codes 4/5 with messages, never tracebacks; `--spec file.json` on start and run | `test_operator_errors_exit_cleanly`, `test_spec_file_round_trips_through_start_and_run` | `test_cli.py` |
| a database file from a newer schema version is refused (exit 4) | `test_newer_schema_is_refused` | `test_cli.py` |

Run one: `.venv/bin/python -m pytest -q -k notify_effect_once -vv`; stop at first failure: `-x`;
print on failure: pytest shows the assertion's left/right values, and `engine.store.events(wf_id)`
in the failing test is the fastest view of what happened.

**Reproducing crashes with the fault flags** (all on `start` and `run`; everything in a temp dir):
```bash
D=$(mktemp -d); W() { python3 -m wfk --db $D/wfk.db --notifications $D/n.jsonl "$@"; }
W start examples/doc.json --id retry --fail extract:2          # two retries, success on attempt 3
W start examples/doc.json --id c1 --crash-after classify       # exit 1, owner left, notify PENDING 0/5
W run c1                                                       # exit 3: owned by <token>
W run c1 --take-over                                           # finishes; one notification
W start examples/doc.json --id c2 --crash-in-notify            # exit 1; notify RUNNING 1/5; effect CLAIMED; 1 line in n.jsonl
W status c2; W run c2 --take-over; W history c2 --kind EFFECT_AMBIGUOUS
wc -l $D/n.jsonl                                               # still one line per workflow
W replay c2                                                    # CONSISTENT
```
`--fail` wraps a tool in `Flaky`; `--crash-after STEP` wraps the Store in `CrashAfterCommit`;
`--crash-in-notify` wraps the sink in `CrashingSink`. The crash exits via `os._exit(1)` so the claim
is left exactly as a real kill would leave it. `scripts/demo.sh` runs all of this.

**Common errors and what they mean:**

| Message (stderr) | Exit | Cause / fix |
|---|---|---|
| `workflow X is owned by abcd1234; use --take-over if that process is dead` | 3 | a live or dead engine holds the claim; confirm it is dead, then `run X --take-over` |
| `error: workflow X already exists` | 4 | `start --id X` twice; use `run X` to resume |
| `error: workflow X not found` | 4 | wrong id or wrong `--db` |
| `error: workflow X is SUCCEEDED; cannot cancel` | 4 | terminal states are absorbing |
| `error: event 17 STEP_SUCCEEDED: SUCCEEDED -> SUCCEEDED (event claims from RUNNING)` | 4 | `replay` found the log itself inconsistent (`IllegalTransition` from the reducer) |
| `error: unknown spec 'nope@3'; registered: document_triage@1` | 5 | typo, or a file spec not passed with `--spec` on `run` ("pinned to spec custom@1, which is not registered") |
| `error: --fail: no such tool 'ghost'` | 5 | the `--fail` name must be a registry key |
| `error: workflow input must be a JSON object, not list` / `input keys collide with step names: ['extract']` | 5 | `Engine.start` validation, nothing persisted |
| `error: /path/bad.json: malformed spec: __init__() got an unexpected keyword argument 'nme'` | 5 | `load_spec_json`; a wrong key in a step object |
| `error: /path/wfk.db: schema version 99 is newer than this kernel's 1` | 4 | the file was written by a newer kernel; upgrade the kernel, never downgrade the file |
| `database error: database is locked` | 6 | another process holds the write lock (a hung engine mid-transaction); SQLite's default busy timeout is 5 s |
| `*** SIMULATED CRASH: process died after the receiver accepted the notification ***` | 1 | you asked for it (`--crash-in-notify`); resume with `run --take-over` |
| `INCONSISTENT:` + `step classify.attempt: log says 1, table says 7` (stdout) | 1 | a table row disagrees with the log; something wrote around the store |
| `ProgrammingError: SQLite objects created in a thread can only be used in that same thread` (in `history` as an attempt error) | | a tool used `ctx.*` from another thread; see §6.2 |

---

## 8. Cheat card (one page)

**Guarantee (verbatim):** at-least-once invocation, exactly-once recording, exactly-once effect iff
the receiver honours the idempotency key; an unknowable non-idempotent outcome → `EFFECT_AMBIGUOUS`,
step FAILED (`ambiguous_effect`), stop; never guess, never fork.

**Three decisions:** (1) tables + append-only log in one `BEGIN IMMEDIATE` transaction, reducer
proves agreement (D2/D10); (2) effects ledger, key `sha256(workflow:step:effect)` without attempt,
ambiguity recorded then resolved by the tool's declared `idempotent` (D6/D7); (3) claim + owner
fence re-checked in every write instead of a TTL lease; `--take-over` is the operator's act (D9).

**Anchors (file:line):**
- `spec.py:14-26` StepSpec; `:30-43` WorkflowSpec; `:46-53` TRIAGE_V1 + SPECS; `:56-64` JSON loader
- `store.py:19-33` WF_ALLOWED / STEP_ALLOWED / STEP_FIELDS; `:44-69` errors; `:72-118` schema
- `store.py:178` SCHEMA_VERSION; `:189-212` `Store.__init__` (pragmas, user_version check); `:220-236` `_tx`
  (BEGIN IMMEDIATE); `:238-250` `_fence`; `:252-265` `_append` (the observability funnel)
- `store.py:243-264` create_workflow; `:307-334` claim/release; `:336-347` transition_workflow;
  `:349-360` request_cancel; `:381-403` transition_step; `:416-441` claim_effect/complete_effect;
  `:450-465` append_event/events
- `engine.py:26-35` AmbiguousEffect + `effect_key`; `:43-61` StepContext; `:75-90` start;
  `:92-106` cancel; `:108-126` run (except Exception vs BaseException); `:143-180` `_run_owned`
  (resume); `:190-247` `_run_step` (retry loop; cancel re-check after the wait at 208);
  `:249-258` `_encode_output`; `:260-284` `_give_up`/`_wait_until` (clamp at 280);
  `:288-313` `_run_effect`; `:333-373` `rebuild_state`; `:376-401` `consistency_diff`
- `tools.py:19-41` errors, Tool, Sink protocols; `:59-101` ingest/extract/classify; `:104-118`
  NotifyTool; `:121-164` FileNotifier
- `faults.py:17-18` SimulatedCrash(BaseException); `:55-64` CrashingSink; `:67-78` CrashAfterCommit
- `cli.py:25-36` build_registry (where tools get names); `:125-142` `_run` (os._exit); `:173-188`
  cmd_replay; `:227-249` main + exit codes
- `tests/conftest.py:19-31` FakeClock; `:56-69` make_engine (fresh Store = new process)

**Key terms:** attempt = one tool invocation (counts *started*; interrupted is charged). Fence =
owner check + `WHERE state = frm`, both inside the transaction. Claim = CAS on `owner`. Effect key =
no attempt number. Ambiguous = CLAIMED without DONE. Resume = RUNNING step → `ATTEMPT_INTERRUPTED`
→ PENDING, attempt kept, continue from first non-terminal step. Replay = read-only fold + diff.
Decision = tool-authored event. `side_effect_free` = re-run freely; `idempotent` = receiver dedupes
on the key.

**Numbers:** 4 tables; 16 event types; edges 5 (wf) + 7 (step); 6 verbs; exit codes 0/1/2/3/4/5/6;
31 tests, 7 files; clean run = 16 events, crash+resume = 20; backoff `min(0.05·2^(n−1), 2.0)`;
triage attempts 3/3/2/5, notify `skip`; owner token 8 hex; default workflow id 12 hex.

**Event types (16):** WORKFLOW_CREATED, WORKFLOW_STATE, OWNER_CLAIMED, OWNER_RELEASED,
CANCEL_REQUESTED, ATTEMPT_STARTED, ATTEMPT_FAILED, RETRY_SCHEDULED, ATTEMPT_INTERRUPTED,
STEP_SUCCEEDED, STEP_FAILED, STEP_SKIPPED, EFFECT_CLAIMED, EFFECT_DONE, EFFECT_AMBIGUOUS, DECISION.
State-changing: all except ATTEMPT_FAILED, EFFECT_AMBIGUOUS, DECISION.

**Crash points (notify):** C0 created / C1 after classify / C2 after ATTEMPT_STARTED / C3 after
CLAIMED, during send / C4 after send, before DONE (= C3 on disk) / C5 after DONE, before
STEP_SUCCEEDED / C6 after STEP_SUCCEEDED, before workflow SUCCEEDED / C7 after SUCCEEDED, before
release. Only C3/C4 can produce a second *call*; never a second *effect* with a keyed receiver.

**Drill plan shape (say before typing):** layer → events/edges → reducer → test in one sentence.
Keep the machine closed: new behaviour is new events, rarely new edges. Thread + `ctx.*` needs
`check_same_thread=False`. Every new effect gets its own `effect_name`. Run `pytest -q` after each.

**"How did you use AI":** design record first, three shapes debated, ledger chosen; AI drafted
against the design; I reviewed every line and changed it (fence on the ledger writes; the two
`PENDING → FAILED | SKIPPED` edges); tests and fault boundaries were my call; I own it, and I'll
extend it now without any assistant.

---

## 9. What changed in the review pass (Oct 31) and how to talk about it

Six commits after the Oct 30 candidate (`38e305e..b51a529`): four fixes with tests, fifteen comments
at the decisions a reviewer will probe, a production roadmap in the README. Tests 23 → 27. Each row:
where it is, what it says, and the one or two sentences to say if asked. Say them as your own
reasoning, in the past tense ("I noticed", "I decided"); they are.

| Where | What is there | How to talk about it |
|---|---|---|
| `engine.py:191-192` (fix, `test_cancel_during_backoff_is_honoured_before_the_next_attempt`) | The cancel flag is re-read after any backoff wait; `_wait_until` returns whether it waited. | "My cancel check was before the sleep, so a cancel that arrived during a two-second backoff ran one more attempt. For notify that is one more send. I re-check after the wait; the test injects the cancel into the sleep itself." |
| `engine.py:200`, `:249-258` (fix, `test_non_json_output_is_fatal_not_retried`) | `_encode_output`: a step output must be a JSON object; anything else is `FatalError`. | "A tool returning something I cannot persist is a bug, not a transient. Before, `dumps` failed inside the retry ladder and the step was retried N times with backoff and then blamed on a 'transient failure'. Now it fails once with reason `fatal` and the error names JSON. The kernel-side counterpart of 'a tool that knows better raises FatalError'." |
| `engine.py:257-259` (fix, `test_backoff_wait_is_clamped_when_the_clock_steps_backwards`) | The wait is `min(remainder, BACKOFF_CAP_S)`. | "`next_run_at` is wall-clock time written by a possibly different process. If the clock steps backwards, NTP or a VM restore, the remainder can be hours although no delay I schedule exceeds two seconds. Clamping bounds the damage of a skewed clock to one maximum backoff. The test moves the fake clock back an hour and asserts a two-second sleep." |
| `store.py:178`, `:202-212` (feat, `test_newer_schema_is_refused`) | `PRAGMA user_version` stamped at first open; a newer file raises `StoreError`, exit 4. | "`CREATE TABLE IF NOT EXISTS` makes opening idempotent but says nothing about which schema the file holds. A file written by a newer kernel would be misread silently. One pragma; the migration ladder would run in that same branch." |
| `engine.py:32-34` (comment) | The effect key is deliberately free of the spec version; a step rename is a key change; migration carries an alias; tenant id goes first. | "The key must survive a migration, or the receiver sees a new key and the duplicate the ledger exists to prevent goes out. So the key is workflow, step, effect name, nothing else, and renaming a step is a versioning event with an alias." |
| `engine.py:143-145` (comment) | An interrupted attempt retries immediately; fine under a human `--take-over`, needs a backoff under automatic lease expiry. | "Resume is an operator action here, so immediate is right. The moment resume is automatic, an input that kills the worker gets redelivered at once max_attempts times; the interrupted path would schedule a backoff like a failed one. It is in the roadmap next to jitter." |
| `engine.py:184-186` (comment) | The exhausted branch is the poison-pill bound. | "`ATTEMPT_STARTED` is committed before the tool runs, so a crash costs an attempt. An input that kills the process every time reaches the failure policy after max_attempts crashes instead of being resumed forever." |
| `engine.py:213-214` (comment) | No jitter, and why. | "Deterministic delays keep the retry test exact and a single process never contends with itself. A fleet retrying the same outage needs jitter, one line, or the retries synchronise into waves." |
| `engine.py:283-285` (comment) | The ledger records the claim, not the payload; the resend rebuilds it from persisted outputs; production adds a payload hash. | "A resend is byte-identical only because my tools are pure functions of their persisted inputs. A receiver dedupes by key and keeps the first body, so a changed payload would vanish silently. The production row carries a payload hash and the kernel refuses a resend that differs." |
| `store.py:184-186` (comment) | Connection thread affinity; a second `Store` is a second process; fsync cost of `synchronous=FULL`. | "One connection, one thread, one process; the owner fence, not the object, keeps two apart. FULL is an fsync per commit, about a dozen per clean run, which is the price of 'durable before the next action' on one disk; Postgres pays it per batch." |
| `store.py:208-210` (comment) | `BEGIN IMMEDIATE` vs `DEFERRED`; busy timeout then exit 6. | "A deferred transaction evaluates my fence against a snapshot that another writer may have made stale, and the upgrade to the write lock then fails with SQLITE_BUSY. IMMEDIATE takes the lock first so the fence reads the truth the commit is based on." |
| `store.py:222-223` (comment) | The fence is checked at commit time because the dangerous process is the slow live one. | "The process that died is harmless. The one that was alive and slow still believes it owns the workflow; nothing it does between transactions matters, and the first thing it tries to commit is refused." |
| `store.py:232-233` (comment) | `_append` is the single funnel for every state change: the observability hook. | "Every state change is one INSERT here, so metrics, a span per attempt and a change feed hang off one place after COMMIT, with no second instrumentation path to keep in sync." |
| `store.py:284-287` (comment) | `take_over` is unconditional by design; the fence protects the data; an in-flight effect still lands; fleet lease + token. | "The operator asserts the old owner is dead. If they are wrong, the fence stops every commit, but a side effect already inside the old tool still lands, which is exactly what the ledger is for. The fleet replaces the assertion with `lease_until` and a `lease_token` in every WHERE." |
| `tools.py:120-122` (comment) | The file is the receiver's truth, the dict its cache; two live writers is the receiver's problem. | "The index is loaded once from the file, so a take-over process sees what the dead one wrote. Two live processes appending would each trust their own index; a real provider dedupes in its own database, and the kernel's fence is what keeps a second live process from getting here." |
| `cli.py:134-136` (comment) | `os._exit`, not `sys.exit`. | "`SystemExit` unwinds through `finally` and closes the connection cleanly; the demo would be rehearsing a graceful shutdown while claiming to show a crash. The stale owner left on disk is the evidence nothing ran after the kill." |
| README "Production roadmap" | Six items in order: lease + token, reconciler + `effects resolve`, payload hash, backoff-on-interruption + jitter, Postgres + tenant, observability off the funnel. | "The order is dependency order: the lease removes the human from recovery and everything in the scaling section follows from it; the reconciler closes the one loop the kernel hands to a human with no verb; the rest is hardening." |

**If asked "what would you still change?"** Name upgrade 1 from `PRINCIPAL_REVIEW.md`: the
`effects resolve` verb (drill 6.11), then the payload hash. Both are small; say why you kept them
out of the submission (the code is complete without them and a live-built verb is a better story).

## 10. What changed in the Oct 31 evening pass and how to talk about it

Five commits after `e003e50` (`9883d73..080c8f0`): three fixes with tests, two new tests for paths
the design record names, and the docs following. Tests 27 → 31. Full audit with the ranking a Trase
reviewer would use: `TR_AUDIT.md`. Same rule as §9: say these as your own reasoning, past tense.

| Where | What is there | How to talk about it |
|---|---|---|
| `engine.py:137-139` (fix, `test_cancel_racing_start_is_reported_not_raised`) | After the re-read under the claim, a terminal state is returned as-is. | "`run` reads the row, then claims it. A `cancel` landing in between leaves a CANCELLED, unowned row, and the claim still succeeds on it. My `_finish` then tried RUNNING→CANCELLED and the operator saw `LostClaim` for a workflow that had ended correctly. The disk was right; the signal was wrong. One check under the claim; the test injects the cancel at the claim boundary with a `Store` subclass, the same way the crash tests inject crashes." |
| `tests/test_restart.py` (`test_interrupted_last_attempt_goes_to_the_failure_policy`, `test_crash_after_step_failed_finishes_the_workflow`) | The two resume-only paths now have tests. | "The design record says the `PENDING → FAILED \| SKIPPED` edges were the case I missed on paper. The code handled it; nothing pinned it. Now a crash inside classify's last attempt resumes to FAILED with reason `exhausted`, the error names both the interruption and the earlier error, there is no `ATTEMPT_FAILED` for the interrupted attempt, and `replay` is consistent through that edge. The second test kills the process right after `STEP_FAILED` commits and checks the next process finishes the workflow without a new attempt." |
| `cli.py:164` (fix, `test_operator_errors_exit_cleanly`) | `history <unknown id>` is exit 4. | "Every other verb raised `NotFound`; `history` printed nothing and exited 0, so a typo looked like an empty history. The README promises every operator error is a message with an exit code; this one was the exception." |
| `store.py:189-195` (fix, `test_newer_schema_is_refused`) | The version check runs before the DDL; refusal closes the connection; the test asserts the newer file has none of our tables. | "Refuse before touching. `IF NOT EXISTS` made the old order harmless today, but the day v2 renames a table, running v1's DDL first against a v2 file is exactly the corruption the stamp exists to prevent." |
| `tools.py:29-30` (comment), `tools.py:135-137` (fix, `test_file_notifier_honours_the_key_only_when_it_says_it_does`) | `side_effect_free` labelled as unenforced; `FileNotifier(idempotent=False)` delivers every call. | "Two honesty fixes on the central abstraction. `side_effect_free` is read by nobody in the kernel, so the field now says it is a declaration, not a contract. And the demo receiver that stands for a bare SMTP relay was quietly deduping like SES; now the flag is behaviour, which is what makes the non-idempotent test mean something." |

**Not done, on purpose** (say so if asked "what else did you find?"): `rebuild_state` turns an
orphan `EFFECT_DONE` or a step event before `WORKFLOW_CREATED` into a `KeyError`/`TypeError`
traceback instead of an `IllegalTransition` finding (only a corrupted log reaches it; a 15-minute
drill, same shape as 6.9); no index on `effects(workflow_id)`; `backoff_base_s` is not validated
against negatives. Each is in `TR_AUDIT.md` with the reasoning.
