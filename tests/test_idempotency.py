import pytest

from wfk.engine import effect_key
from wfk.spec import SPECS, StepSpec, TRIAGE_V1, WorkflowSpec
from wfk.faults import CrashingSink, SimulatedCrash
from wfk.tools import FileNotifier, NotifyTool

from .conftest import INPUT, registry


def _lines(path):
    with open(path) as f:
        return [ln for ln in f if ln.strip()]


def test_notify_effect_once_across_crash(make_engine, notifications):
    """Worst case (C4): receiver accepted, process dies before DONE commits.
    On resume the kernel records the ambiguity and, because the receiver honours
    the key, re-sends with the same key; the receiver dedupes. One effect."""
    crashing = FileNotifier(notifications)
    engine1 = make_engine(registry(crashing, notify=NotifyTool(CrashingSink(crashing))))
    wf_id = engine1.start(TRIAGE_V1, INPUT)
    with pytest.raises(SimulatedCrash):
        engine1.run(wf_id)

    key = effect_key(wf_id, "notify", "email")
    assert engine1.store.get_effect(key).status == "CLAIMED"
    assert engine1.store.load_step(wf_id, "notify").state == "RUNNING"
    assert len(_lines(notifications)) == 1
    assert engine1.store.load_workflow(wf_id).owner is not None  # dead process never released

    healthy = FileNotifier(notifications)
    engine2 = make_engine(registry(healthy))  # "new process": fresh store, fresh tools
    assert engine2.run(wf_id, take_over=True) == "SUCCEEDED"

    store = engine2.store
    assert [e.type for e in store.events(wf_id) if e.step_name == "notify"].count("ATTEMPT_INTERRUPTED") == 1
    ambiguous = store.events(wf_id, "EFFECT_AMBIGUOUS")
    assert len(ambiguous) == 1 and ambiguous[0].payload["action"] == "resend"
    assert healthy.calls == 1 and crashing.calls == 1  # two invocations in total ...
    assert len(_lines(notifications)) == 1               # ... one effect
    assert store.get_effect(key).status == "DONE"
    assert store.get_effect(key).result["status"] == "duplicate"
    assert store.load_step(wf_id, "notify").attempt == 2


def test_ambiguous_non_idempotent_fails_safely(make_engine, notifications):
    """Same crash, but the receiver cannot dedupe: the kernel refuses to guess."""
    spec = WorkflowSpec("triage_strict", 1, TRIAGE_V1.steps[:3] + (
        StepSpec("notify", "notify", max_attempts=5, on_failure="fail_workflow"),))
    specs = {**SPECS, spec.key: spec}

    crashing = FileNotifier(notifications, idempotent=False)
    engine1 = make_engine(registry(crashing, notify=NotifyTool(CrashingSink(crashing))), specs=specs)
    wf_id = engine1.start(spec, INPUT)
    with pytest.raises(SimulatedCrash):
        engine1.run(wf_id)

    legacy = FileNotifier(notifications, idempotent=False)
    engine2 = make_engine(registry(legacy), specs=specs)
    assert engine2.run(wf_id, take_over=True) == "FAILED"

    store = engine2.store
    failed = store.events(wf_id, "STEP_FAILED")[0].payload
    assert failed["reason"] == "ambiguous_effect"
    assert store.events(wf_id, "EFFECT_AMBIGUOUS")[0].payload["action"] == "fail"
    assert legacy.calls == 0 and crashing.calls == 1  # exactly one send ever
    assert len(_lines(notifications)) == 1
    assert store.get_effect(effect_key(wf_id, "notify", "email")).status == "CLAIMED"  # left for an operator


def test_done_effect_is_not_resent_on_resume(make_engine, notifications):
    """C5: DONE committed, crash before STEP_SUCCEEDED. The stored result is returned, fn never called."""
    engine1 = make_engine(crash_after="classify")
    wf_id = engine1.start(TRIAGE_V1, INPUT)
    with pytest.raises(SimulatedCrash):
        engine1.run(wf_id)
    # Simulate C5 by hand: the kernel has recorded DONE but the step is still RUNNING.
    store = engine1.store
    key = effect_key(wf_id, "notify", "email")
    owner = store.load_workflow(wf_id).owner  # the dead process's claim, still there
    store.transition_step(wf_id, "notify", "PENDING", "RUNNING", owner,
                          [("ATTEMPT_STARTED", {"attempt": 1})], attempt_no=1, attempt=1)
    store.claim_effect(key, wf_id, "notify", "email", 1, owner=owner)
    store.complete_effect(key, {"status": "sent", "key": key}, owner=owner, attempt=1)

    sink = FileNotifier(notifications)
    engine2 = make_engine(registry(sink))
    assert engine2.run(wf_id, take_over=True) == "SUCCEEDED"
    assert sink.calls == 0
    assert engine2.store.load_step(wf_id, "notify").output == {"email": {"status": "sent", "key": key}}


def test_file_notifier_honours_the_key_only_when_it_says_it_does(notifications):
    """idempotent=True keeps the first body per key; idempotent=False delivers every call.
    The kernel trusts this flag when deciding whether to resend after a crash, so it has
    to mean what it says."""
    payload = {"to": "ap@example.com", "subject": "s", "body": "b"}
    honours = FileNotifier(notifications, idempotent=True)
    assert honours.send("k1", payload)["status"] == "sent"
    assert honours.send("k1", payload)["status"] == "duplicate"
    assert len(_lines(notifications)) == 1

    relay = FileNotifier(notifications, idempotent=False)
    assert relay.send("k2", payload)["status"] == "sent"
    assert relay.send("k2", payload)["status"] == "sent"      # delivered twice
    assert len(_lines(notifications)) == 3
