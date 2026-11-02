"""Persistence. The only module that talks to SQLite.

Each state change is one BEGIN IMMEDIATE transaction that updates the row and
appends the events explaining it, so tables and log can't drift apart. Every
workflow-scoped write also checks ``owner`` inside that transaction: a process
that lost its claim can't write anything.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

# The state machines. transition_* refuses anything not listed here.
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


SCHEMA = """
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
"""


def to_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="microseconds")


def from_iso(s: str) -> float:
    return datetime.fromisoformat(s).timestamp()


def dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class WorkflowRow:
    id: str
    spec_name: str
    spec_version: int
    input: Dict[str, Any]
    state: str
    cancel_requested: bool
    owner: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class StepRow:
    workflow_id: str
    name: str
    idx: int
    state: str
    attempt: int
    max_attempts: int
    output: Optional[Dict[str, Any]]
    error: Optional[str]
    next_run_at: Optional[str]
    updated_at: str


@dataclass(frozen=True)
class EffectRow:
    key: str
    workflow_id: str
    step_name: str
    effect_name: str
    attempt: int
    status: str
    result: Optional[Dict[str, Any]]


@dataclass(frozen=True)
class Event:
    seq: int
    workflow_id: str
    step_name: Optional[str]
    type: str
    attempt: Optional[int]
    payload: Dict[str, Any]
    created_at: str


# (type, payload): events written inside a transition's transaction.
EventSpec = Tuple[str, Dict[str, Any]]


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

    def close(self) -> None:
        self.conn.close()

    def now(self) -> str:
        return to_iso(self.clock())

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

    def _fence(self, wf_id: str, owner: Optional[str]) -> None:
        """Inside a transaction: the workflow must be owned by ``owner`` (None = unowned).

        Checked on every write rather than once at claim time, because the process to
        worry about is the slow one that still thinks it owns the workflow."""
        r = self.conn.execute("SELECT owner FROM workflows WHERE id = ?", (wf_id,)).fetchone()
        if r is None:
            raise NotFound(f"workflow {wf_id} not found")
        if r["owner"] != owner:
            raise LostClaim(f"workflow {wf_id} is owned by {r['owner']}, not {owner}")

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

    # ---- workflows ---------------------------------------------------------

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

    def _workflow(self, r: sqlite3.Row) -> WorkflowRow:
        return WorkflowRow(r["id"], r["spec_name"], r["spec_version"], json.loads(r["input_json"]),
                           r["state"], bool(r["cancel_requested"]), r["owner"],
                           r["created_at"], r["updated_at"])

    def load_workflow(self, wf_id: str) -> WorkflowRow:
        r = self.conn.execute("SELECT * FROM workflows WHERE id = ?", (wf_id,)).fetchone()
        if r is None:
            raise NotFound(f"workflow {wf_id} not found")
        return self._workflow(r)

    def list_workflows(self) -> List[WorkflowRow]:
        rows = self.conn.execute("SELECT * FROM workflows ORDER BY created_at, id").fetchall()
        return [self._workflow(r) for r in rows]

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

    # ---- steps -------------------------------------------------------------

    def _step(self, r: sqlite3.Row) -> StepRow:
        out = json.loads(r["output_json"]) if r["output_json"] is not None else None
        return StepRow(r["workflow_id"], r["name"], r["idx"], r["state"], r["attempt"],
                       r["max_attempts"], out, r["error"], r["next_run_at"], r["updated_at"])

    def load_steps(self, wf_id: str) -> List[StepRow]:
        rows = self.conn.execute(
            "SELECT * FROM steps WHERE workflow_id = ? ORDER BY idx", (wf_id,)).fetchall()
        return [self._step(r) for r in rows]

    def load_step(self, wf_id: str, name: str) -> StepRow:
        r = self.conn.execute(
            "SELECT * FROM steps WHERE workflow_id = ? AND name = ?", (wf_id, name)).fetchone()
        if r is None:
            raise NotFound(f"step {wf_id}/{name} not found")
        return self._step(r)

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

    # ---- effects ledger ----------------------------------------------------

    def _effect(self, r: sqlite3.Row) -> EffectRow:
        res = json.loads(r["result_json"]) if r["result_json"] is not None else None
        return EffectRow(r["key"], r["workflow_id"], r["step_name"], r["effect_name"],
                         r["attempt"], r["status"], res)

    def get_effect(self, key: str) -> Optional[EffectRow]:
        r = self.conn.execute("SELECT * FROM effects WHERE key = ?", (key,)).fetchone()
        return self._effect(r) if r is not None else None

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

    def effects_for(self, wf_id: str) -> List[EffectRow]:
        rows = self.conn.execute(
            "SELECT * FROM effects WHERE workflow_id = ? ORDER BY created_at", (wf_id,)).fetchall()
        return [self._effect(r) for r in rows]

    # ---- events (append-only: nothing in this module updates or deletes them) ---

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
