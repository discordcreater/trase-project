"""Orchestration: decides what runs next, never does the work itself.

``Engine.run`` is a pure function of (database, registry): it holds no state
between transitions, so a restart at any instruction resumes correctly.
This module imports no concrete tool and no fault-injection code; the
registry is assembled by the caller.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .spec import SPECS, StepSpec, WorkflowSpec
from .store import (
    STEP_ALLOWED, WF_ALLOWED, WF_TERMINAL,
    Event, EventSpec, IllegalTransition, LostClaim, Store, StoreError, WorkflowRow,
    dumps, from_iso, to_iso,
)
from .tools import FatalError, Tool

EffectFn = Callable[[str, Dict[str, Any]], Dict[str, Any]]


class AmbiguousEffect(FatalError):
    """A previous attempt claimed this effect and never recorded the outcome,
    and the tool is not idempotent. Only an operator can resolve it."""


def effect_key(workflow_id: str, step_name: str, effect_name: str) -> str:
    # No attempt number: every attempt of the same logical effect shares one key,
    # so the receiver can dedupe across retries and restarts.
    # The key is also deliberately free of the spec version: a workflow migrated to a
    # newer spec must keep presenting the same key for the same logical effect, or the
    # receiver sees a new key and the duplicate the ledger exists to prevent goes out.
    # Renaming a step is therefore a key change; a migration that renames one has to
    # carry a key alias (old name -> new name) with it. A tenant id would go first.
    return hashlib.sha256(f"{workflow_id}:{step_name}:{effect_name}".encode()).hexdigest()


class StepContext:
    """What a tool is allowed to know (its identity) and do (two kernel services).
    The store is never exposed; both services write under the engine's claim."""

    def __init__(self, engine: "Engine", workflow_id: str, owner: str,
                 step: StepSpec, attempt: int, tool: Tool) -> None:
        self.workflow_id, self.step_name, self.attempt = workflow_id, step.name, attempt
        self._engine, self._owner, self._tool = engine, owner, tool

    def record_decision(self, key: str, data: Dict[str, Any]) -> None:
        """Write a DECISION event: what the tool decided and why."""
        self._engine.store.append_event(self.workflow_id, self.step_name, "DECISION", self.attempt,
                                        {"key": key, "data": data}, owner=self._owner)

    def run_effect(self, name: str, payload: Dict[str, Any], fn: EffectFn) -> Dict[str, Any]:
        """Perform ``fn(key, payload)`` at most once per (workflow, step, name) as far as
        the kernel can prove; see Engine._run_effect for the crash semantics."""
        return self._engine._run_effect(self.workflow_id, self._owner, self.step_name,
                                        self.attempt, self._tool, name, payload, fn)


class Engine:
    BACKOFF_CAP_S = 2.0

    def __init__(self, store: Store, registry: Dict[str, Tool],
                 specs: Optional[Dict[Tuple[str, int], WorkflowSpec]] = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.store, self.registry, self.sleep = store, registry, sleep
        self.specs = dict(SPECS if specs is None else specs)  # own copy: start() may register

    # ---- public API ---------------------------------------------------------

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

    def cancel(self, workflow_id: str, by: str = "cli") -> str:
        """Cooperative cancel; returns the workflow state afterwards. Terminal refuses;
        PENDING+unowned cancels now; otherwise a flag the owning engine honours
        between attempts, never mid-tool."""
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
        # A BaseException (KeyboardInterrupt, SystemExit, a simulated crash) is the process
        # dying: it propagates without a release and leaves the stale owner a real crash leaves.
        self.store.release(workflow_id, owner)
        return state

    # ---- orchestration ------------------------------------------------------

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

    def _run_owned(self, wf_id: str, owner: str, spec: WorkflowSpec) -> str:
        wf = self.store.load_workflow(wf_id)  # re-read under our claim
        if wf.state in WF_TERMINAL:
            # A cancel landed between run()'s read and the claim: the row was PENDING and
            # unowned, so the claim went through on a row that is already CANCELLED. There
            # is nothing to run and nothing to finish; report it and let run() release.
            return wf.state
        if wf.state == "PENDING":
            self.store.transition_workflow(wf_id, "PENDING", "RUNNING", owner, "started")

        # Resume: a step found RUNNING was interrupted mid-attempt (at most one).
        # Its attempt stays counted; next_run_at is left as-is (NULL after ATTEMPT_STARTED,
        # so the retry is immediate). That is right here, where resume is an operator's
        # `run --take-over`. With automatic lease expiry (README, A) an input that kills
        # the worker would be redelivered at once, max_attempts times in a row; there the
        # interrupted path should schedule a backoff like a failed attempt does.
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
                # Only reachable after a crash between STEP_FAILED and the workflow's
                # own FAILED commit: finish what the dead process started.
                return self._finish(wf_id, owner, "FAILED", f"step {step.name} failed")
            outcome = self._run_step(wf_id, owner, step)
            if outcome in ("FAILED", "CANCELLED"):
                return self._finish(wf_id, owner, outcome, f"step {step.name}: {outcome.lower()}")
        return self._finish(wf_id, owner, "SUCCEEDED", "all steps succeeded or skipped")

    def _finish(self, wf_id: str, owner: str, state: str, reason: str) -> str:
        self.store.transition_workflow(wf_id, "RUNNING", state, owner, reason)
        return state

    def _inputs(self, wf_id: str) -> Dict[str, Any]:
        """Workflow input + {step_name: output} for every SUCCEEDED step, read from the store."""
        inputs = dict(self.store.load_workflow(wf_id).input)
        for row in self.store.load_steps(wf_id):
            if row.state == "SUCCEEDED":
                inputs[row.name] = row.output
        return inputs

    def _run_step(self, wf_id: str, owner: str, step: StepSpec) -> str:
        """Drive one step to SUCCEEDED / SKIPPED / FAILED, or return CANCELLED."""
        tool = self.registry[step.tool]
        while True:
            row = self.store.load_step(wf_id, step.name)
            if self.store.cancel_requested(wf_id):
                return "CANCELLED"
            if row.attempt >= row.max_attempts:
                # Only an interrupted last attempt gets here (a retry is never scheduled
                # without budget). No ATTEMPT_FAILED: the attempt was interrupted, not failed.
                # This branch is also the poison-pill bound: an input that kills the process
                # on every attempt is charged one attempt per crash (ATTEMPT_STARTED is
                # committed before the tool runs), so it reaches the failure policy instead
                # of being resumed forever.
                error = f"attempt {row.attempt} of {row.max_attempts} was interrupted; no attempts left"
                if row.error:
                    error += f" (error before it: {row.error})"
                return self._give_up(wf_id, owner, step, row.state, row.attempt, "exhausted", error, [])
            if self._wait_until(row.next_run_at) and self.store.cancel_requested(wf_id):
                return "CANCELLED"  # a cancel that arrived during the backoff is honoured before the attempt

            attempt = row.attempt + 1
            self.store.transition_step(wf_id, step.name, "PENDING", "RUNNING", owner,
                                       [("ATTEMPT_STARTED", {"attempt": attempt})],
                                       attempt_no=attempt, attempt=attempt, next_run_at=None)
            ctx = StepContext(self, wf_id, owner, step, attempt, tool)
            try:
                output = tool.run(ctx, self._inputs(wf_id))  # the only place a tool runs; no transaction open
                output_json = self._encode_output(output)      # a non-JSON output is a tool bug: fatal, not transient
            except FatalError as e:
                failed: EventSpec = ("ATTEMPT_FAILED", {"attempt": attempt, "error": str(e), "retryable": False})
                reason = "ambiguous_effect" if isinstance(e, AmbiguousEffect) else "fatal"
                return self._give_up(wf_id, owner, step, "RUNNING", attempt, reason, str(e), [failed])
            except StoreError:
                raise  # the kernel's own failure inside ctx.* (e.g. a lost claim) is not the tool's
            except Exception as e:  # RetryableError and anything unexpected
                retryable = attempt < row.max_attempts
                failed = ("ATTEMPT_FAILED", {"attempt": attempt, "error": str(e), "retryable": retryable})
                if not retryable:
                    return self._give_up(wf_id, owner, step, "RUNNING", attempt, "exhausted", str(e), [failed])
                # Deterministic backoff, no jitter: the retry test asserts the exact delays
                # and one process never contends with itself. A fleet retrying the same
                # downstream outage would need jitter here (one line) or the retries
                # synchronise into waves.
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
        """A step output must be a JSON object: it is persisted and fed to later steps.
        Anything else cannot be fixed by retrying, so it is a FatalError."""
        if not isinstance(output, dict):
            raise FatalError(f"tool returned {type(output).__name__}, not a JSON object")
        try:
            return dumps(output)
        except (TypeError, ValueError) as e:
            raise FatalError(f"tool output is not JSON-serializable: {e}") from e

    def _give_up(self, wf_id: str, owner: str, step: StepSpec, frm: str, attempt: int,
                 reason: str, error: str, events: List[EventSpec]) -> str:
        """Step-level failure handling: on_failure decides FAILED (stops the workflow) or SKIPPED."""
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
        # next_run_at is wall-clock time written by a possibly different process. If the
        # clock stepped backwards between the write and this read (NTP, a VM restore), the
        # remainder can be hours even though no scheduled delay ever exceeds BACKOFF_CAP_S.
        # Clamping bounds the damage of a skewed clock to one maximum backoff.
        remaining = min(from_iso(next_run_at) - self.store.clock(), self.BACKOFF_CAP_S)
        if remaining <= 0:
            return False
        self.sleep(remaining)
        return True

    # ---- effects ledger -----------------------------------------------------

    def _run_effect(self, wf_id: str, owner: str, step_name: str, attempt: int, tool: Tool,
                    name: str, payload: Dict[str, Any], fn: EffectFn) -> Dict[str, Any]:
        key = effect_key(wf_id, step_name, name)
        row = self.store.get_effect(key)
        if row is not None and row.status == "DONE":
            return row.result or {}  # already performed and recorded: never call fn again
        if row is not None:  # CLAIMED, never DONE: the effect may or may not have happened
            action = "resend" if tool.idempotent else "fail"
            self.store.append_event(wf_id, step_name, "EFFECT_AMBIGUOUS", attempt, {
                "key": key, "claimed_by_attempt": row.attempt,
                "tool_idempotent": tool.idempotent, "action": action}, owner=owner)
            if not tool.idempotent:
                raise AmbiguousEffect(f"effect '{name}' claimed by attempt {row.attempt} but never "
                                      f"completed; receiver cannot dedupe; needs an operator")
            # idempotent receiver: re-call with the SAME key, it dedupes
        else:
            # The ledger records that the effect was claimed, not what was sent: on a
            # resend the payload is rebuilt from persisted step outputs, which is the same
            # bytes as long as the tool is a pure function of its inputs (ours are). A
            # receiver dedupes by key and keeps the first body, so the production version
            # of this row carries a payload hash and the kernel refuses a resend whose
            # payload differs, instead of letting a changed message vanish silently.
            self.store.claim_effect(key, wf_id, step_name, name, attempt, owner=owner)
        result = fn(key, payload)        # external call, outside any transaction
        self.store.complete_effect(key, result, owner=owner, attempt=attempt)
        return result


# --------------------------------------------------------------------------
# Replay: fold the event log into a projection and compare with the tables.
# --------------------------------------------------------------------------

@dataclass
class Projection:
    workflows: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    steps: Dict[Tuple[str, str], Dict[str, Any]] = field(default_factory=dict)
    effects: Dict[str, Dict[str, Any]] = field(default_factory=dict)


def _move(obj: Dict[str, Any], frm: str, to: str, allowed: set, where: str) -> None:
    if obj["state"] != frm or (frm, to) not in allowed:
        raise IllegalTransition(f"{where}: {obj['state']} -> {to} (event claims from {frm})")
    obj["state"] = to


def rebuild_state(events: Iterable[Event]) -> Projection:
    """Pure reducer: the same ALLOWED sets as the store, applied to the log.
    An illegal transition in the log is itself a finding (raises)."""
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


def consistency_diff(store: Store, wf_id: str) -> List[str]:
    """Field-level differences between the log-derived projection and the stored rows."""
    p = rebuild_state(store.events(wf_id))
    diffs: List[str] = []

    def cmp(where: str, derived: Dict[str, Any], stored: Dict[str, Any]) -> None:
        for k, v in derived.items():
            if stored.get(k) != v:
                diffs.append(f"{where}.{k}: log says {v!r}, table says {stored.get(k)!r}")

    wf = store.load_workflow(wf_id)
    cmp("workflow", p.workflows.get(wf_id, {}),
        {"state": wf.state, "cancel_requested": wf.cancel_requested, "owner": wf.owner})
    stored_steps = {s.name: s for s in store.load_steps(wf_id)}
    for (_, name), d in ((k, v) for k, v in p.steps.items() if k[0] == wf_id):
        s = stored_steps.get(name)
        cmp(f"step {name}", d, {} if s is None else {
            "state": s.state, "attempt": s.attempt, "output": s.output,
            "error": s.error, "next_run_at": s.next_run_at})
    stored_effects = {e.key: e for e in store.effects_for(wf_id)}
    for key, d in p.effects.items():
        e = stored_effects.get(key)
        cmp(f"effect {key[:12]}", d, {} if e is None else {"status": e.status, "result": e.result})
    for name in set(stored_steps) - {k[1] for k in p.steps}:
        diffs.append(f"step {name}: in table, not in log")
    return diffs
