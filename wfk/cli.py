"""CLI: python -m wfk [--db PATH] {start,run,status,cancel,replay,history} ...

The registry (tool name -> Tool) is assembled here, including the
fault-injection wrappers the demo and tests use.

Exit codes: 0 ok; 1 replay found an inconsistency; 2 the workflow ended FAILED/CANCELLED;
3 owned by another engine; 4 store refused (not found, exists, illegal, lost claim);
5 bad input, spec or arguments; 6 database error (e.g. locked by a hung process).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from typing import Callable, Dict, List, Optional, Tuple

from .engine import Engine, consistency_diff, rebuild_state
from .faults import CrashAfterCommit, CrashingSink, Flaky, SimulatedCrash, parse_fail_spec
from .spec import SPECS, WorkflowSpec, load_spec_json
from .store import AlreadyOwned, Event, NotFound, Store, StoreError, WorkflowRow
from .tools import ClassifyTool, ExtractTool, FileNotifier, IngestTool, NotifyTool, Tool


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


def _engine(a: argparse.Namespace, store: Store) -> Tuple[Engine, WorkflowSpec]:
    """The engine plus the spec ``--spec`` names: a registered ``name@version`` or a JSON
    file, which is registered for this process (``run`` needs the same flag later)."""
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


# ---- output helpers -----------------------------------------------------------

def _short(s: str, n: int = 90) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"


def print_status(store: Store, wf: WorkflowRow) -> None:
    print(f"workflow {wf.id}  {wf.spec_name}@{wf.spec_version}  state={wf.state}"
          f"  owner={wf.owner or 'none'}  cancel_requested={'yes' if wf.cancel_requested else 'no'}")
    print(f"  created {wf.created_at}  updated {wf.updated_at}")
    print(f"  {'step':<10}{'state':<11}{'attempt':<9}{'next_run_at':<34}detail")
    for s in store.load_steps(wf.id):
        detail = json.dumps(s.output, sort_keys=True) if s.state == "SUCCEEDED" else (s.error or "")
        print(f"  {s.name:<10}{s.state:<11}{f'{s.attempt}/{s.max_attempts}':<9}"
              f"{s.next_run_at or '-':<34}{_short(detail)}")
    for e in store.effects_for(wf.id):
        print(f"  effect {e.effect_name}@{e.step_name}  {e.status}  key={e.key[:12]}..."
              f"  result={json.dumps(e.result, sort_keys=True) if e.result else '-'}")


def _j(obj: object) -> str:
    return json.dumps(obj, sort_keys=True)


# One narrator per event type, built once; p is the event payload.
_NARRATORS: Dict[str, Callable[[Dict], str]] = {
    "WORKFLOW_CREATED": lambda p: f"created {p['spec_name']}@{p['spec_version']}",
    "WORKFLOW_STATE": lambda p: f"{p['from']} -> {p['to']} ({p['reason']})",
    "ATTEMPT_STARTED": lambda p: f"attempt {p['attempt']} started",
    "ATTEMPT_FAILED": lambda p: f"attempt {p['attempt']} failed: {p['error']}"
                                f" ({'retryable' if p['retryable'] else 'not retryable'})",
    "RETRY_SCHEDULED": lambda p: f"retry in {p['delay_s']}s at {p['next_run_at']}: {p['reason']}",
    "ATTEMPT_INTERRUPTED": lambda p: f"attempt {p['attempt']} found RUNNING on resume; back to PENDING",
    "STEP_SUCCEEDED": lambda p: f"SUCCEEDED on attempt {p['attempt']}: {_j(p['output'])}",
    "STEP_FAILED": lambda p: f"FAILED ({p['reason']}): {p['error']}",
    "STEP_SKIPPED": lambda p: f"SKIPPED ({p['reason']}, policy={p['policy']}): {p['error']}",
    "EFFECT_CLAIMED": lambda p: f"effect '{p['effect_name']}' claimed, key {p['key'][:12]}...",
    "EFFECT_DONE": lambda p: f"effect done: {_j(p['result'])}",
    "EFFECT_AMBIGUOUS": lambda p: f"effect {p['key'][:12]}... claimed by attempt {p['claimed_by_attempt']}"
                                  f" never completed; tool idempotent={p['tool_idempotent']} -> {p['action']}",
    "DECISION": lambda p: f"decision {p['key']}: {_j(p['data'])}",
    "CANCEL_REQUESTED": lambda p: f"cancel requested by {p['by']}",
    "OWNER_CLAIMED": lambda p: f"claimed by {p['owner']}" + (" (take-over)" if p.get("take_over") else ""),
    "OWNER_RELEASED": lambda p: f"released by {p['owner']}",
}


def narrate(e: Event) -> str:
    text = _NARRATORS.get(e.type, _j)(e.payload)
    return f"{e.seq:>4}  {e.created_at[11:23]}  {e.step_name or 'workflow':<10}{e.type:<20} {text}"


# ---- verbs ---------------------------------------------------------------------

def cmd_start(a: argparse.Namespace, store: Store) -> int:
    with open(a.input) as f:
        input_doc = json.load(f)
    engine, spec = _engine(a, store)
    wf_id = engine.start(spec, input_doc, a.id)
    print(f"started workflow {wf_id}")
    return 0 if a.no_run else _run(engine, wf_id, take_over=False)


def cmd_run(a: argparse.Namespace, store: Store) -> int:
    engine, _ = _engine(a, store)
    return _run(engine, a.id, a.take_over)


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
        # os._exit, not sys.exit: SystemExit would unwind through main()'s ``finally``
        # and close the connection cleanly, and the demo would be rehearsing a graceful
        # shutdown while claiming to show a crash. The stale owner left on disk is the
        # evidence that nothing ran after the kill.
        os._exit(1)  # like kill -9: no cleanup, no release of the claim
    print(f"workflow {wf_id} finished: {state}")
    print_status(engine.store, engine.store.load_workflow(wf_id))
    return 0 if state == "SUCCEEDED" else 2


def cmd_status(a: argparse.Namespace, store: Store) -> int:
    for wf in ([store.load_workflow(a.id)] if a.id else store.list_workflows()):
        print_status(store, wf)
    return 0


def cmd_cancel(a: argparse.Namespace, store: Store) -> int:
    state = Engine(store, {}).cancel(a.id)
    if state == "CANCELLED":
        print(f"workflow {a.id}: CANCELLED")
        return 0
    owner = store.load_workflow(a.id).owner
    if owner:
        print(f"workflow {a.id}: cancel requested; engine {owner} stops before its next attempt."
              f" If that process is dead, `run {a.id} --take-over` finalizes the cancellation.")
    else:
        print(f"workflow {a.id}: cancel requested; it is {state} with no live engine and"
              f" will be CANCELLED on its next `run`.")
    return 0


def cmd_history(a: argparse.Namespace, store: Store) -> int:
    store.load_workflow(a.id)  # an unknown id is an error (exit 4), not an empty history
    for e in store.events(a.id, a.kind):
        print(json.dumps(e.__dict__, sort_keys=True) if a.json else narrate(e))
    return 0


def cmd_replay(a: argparse.Namespace, store: Store) -> int:
    """Read-only: fold the log, print the narrative, diff against the tables. Never runs a tool."""
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


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="wfk", description="durable workflow kernel")
    p.add_argument("--db", default="wfk.db")
    p.add_argument("--notifications", default="notifications.jsonl", help="FileNotifier output")
    sub = p.add_subparsers(dest="verb", required=True)

    running = argparse.ArgumentParser(add_help=False)  # shared by start and run
    running.add_argument("--spec", help="name@version or a spec JSON file (default document_triage@1)")
    running.add_argument("--fail", metavar="STEP:N[,STEP:N]", help="tool raises RetryableError on its first N calls")
    running.add_argument("--crash-after", metavar="STEP", help="die right after STEP's success is committed")
    running.add_argument("--crash-in-notify", action="store_true", help="die after the receiver accepted, before DONE is committed")

    s = sub.add_parser("start", parents=[running], help="create a workflow and run it")
    s.add_argument("input", help="JSON file: workflow input")
    s.add_argument("--id", help="workflow id (default: random)")
    s.add_argument("--no-run", action="store_true", help="create only")
    s.set_defaults(fn=cmd_start)
    r = sub.add_parser("run", parents=[running], help="run or resume an existing workflow")
    r.add_argument("id")
    r.add_argument("--take-over", action="store_true", help="clear a dead process's claim")
    r.set_defaults(fn=cmd_run)
    st = sub.add_parser("status")
    st.add_argument("id", nargs="?")
    st.set_defaults(fn=cmd_status)
    c = sub.add_parser("cancel")
    c.add_argument("id")
    c.set_defaults(fn=cmd_cancel)
    h = sub.add_parser("history", help="print the event log")
    h.add_argument("id")
    h.add_argument("--kind", help="only this event type, e.g. DECISION")
    h.add_argument("--json", action="store_true")
    h.set_defaults(fn=cmd_history)
    rp = sub.add_parser("replay", help="rebuild state from the log and check it against the tables")
    rp.add_argument("id")
    rp.set_defaults(fn=cmd_replay)

    a = p.parse_args(argv)
    crash_after = getattr(a, "crash_after", None)
    try:
        store = CrashAfterCommit(a.db, crash_after) if crash_after else Store(a.db)
    except StoreError as e:  # a database file written by a newer kernel
        print(f"error: {e}", file=sys.stderr)
        return 4
    except sqlite3.Error as e:
        print(f"database error: {e}", file=sys.stderr)
        return 6
    try:
        return a.fn(a, store)
    except StoreError as e:
        print(f"error: {e}", file=sys.stderr)
        return 4
    except (ValueError, OSError) as e:  # bad JSON, unknown spec/tool, missing file
        print(f"error: {e}", file=sys.stderr)
        return 5
    except sqlite3.Error as e:
        print(f"database error: {e}", file=sys.stderr)
        return 6
    finally:
        store.close()
