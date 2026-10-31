import pytest

from wfk.spec import TRIAGE_V1
from wfk.store import AlreadyOwned
from wfk.faults import Counting, SimulatedCrash
from wfk.tools import ClassifyTool, ExtractTool, FileNotifier, IngestTool, NotifyTool

from .conftest import INPUT, registry


def _counting(notifications):
    return {"ingest": Counting(IngestTool()), "extract": Counting(ExtractTool()),
            "classify": Counting(ClassifyTool()),
            "notify": Counting(NotifyTool(FileNotifier(notifications)))}


def test_restart_resumes_without_rerunning(make_engine, notifications):
    """Process dies right after classify's success commits; a new process finishes the job."""
    first = _counting(notifications)
    engine1 = make_engine(first, crash_after="classify")
    wf_id = engine1.start(TRIAGE_V1, INPUT)
    with pytest.raises(SimulatedCrash):
        engine1.run(wf_id)

    states = {s.name: s.state for s in engine1.store.load_steps(wf_id)}
    assert states == {"ingest": "SUCCEEDED", "extract": "SUCCEEDED",
                      "classify": "SUCCEEDED", "notify": "PENDING"}
    assert engine1.store.load_workflow(wf_id).state == "RUNNING"

    second = _counting(notifications)
    engine2 = make_engine(second)
    with pytest.raises(AlreadyOwned):      # the dead process's claim is still there ...
        engine2.run(wf_id)
    assert engine2.run(wf_id, take_over=True) == "SUCCEEDED"   # ... and an operator clears it

    assert {k: t.calls for k, t in first.items()} == {"ingest": 1, "extract": 1, "classify": 1, "notify": 0}
    assert {k: t.calls for k, t in second.items()} == {"ingest": 0, "extract": 0, "classify": 0, "notify": 1}
    assert engine2.store.events(wf_id, "ATTEMPT_INTERRUPTED") == []  # notify had not started
    assert engine2.store.load_workflow(wf_id).owner is None
    with open(notifications) as f:
        assert len(f.readlines()) == 1


def test_claim_blocks_second_owner(make_engine):
    engine = make_engine()
    wf_id = engine.start(TRIAGE_V1, INPUT)
    engine.store.claim(wf_id, "process-A")
    with pytest.raises(AlreadyOwned) as exc:
        engine.run(wf_id)
    assert exc.value.owner == "process-A"
    assert engine.run(wf_id, take_over=True) == "SUCCEEDED"


def test_restart_during_backoff_waits_the_remainder(make_engine, notifications, clock):
    """Process dies while waiting out a backoff; the deadline is on disk, so the new
    process waits the remainder instead of firing immediately or waiting the full delay."""
    from wfk.faults import Flaky

    def die_while_sleeping(_s):
        raise SimulatedCrash("killed during backoff")

    engine1 = make_engine(registry(FileNotifier(notifications), extract=Flaky(ExtractTool(), 1)))
    engine1.sleep = die_while_sleeping
    wf_id = engine1.start(TRIAGE_V1, INPUT)
    with pytest.raises(SimulatedCrash):
        engine1.run(wf_id)
    step = engine1.store.load_step(wf_id, "extract")
    assert step.state == "PENDING" and step.attempt == 1 and step.next_run_at is not None

    clock.t += 0.02                                      # 20 ms of the 50 ms backoff elapsed
    engine2 = make_engine(registry(FileNotifier(notifications)))
    assert engine2.run(wf_id, take_over=True) == "SUCCEEDED"
    assert [round(s, 3) for s in clock.slept] == [0.03]  # waited the remainder only
    assert engine2.store.load_step(wf_id, "extract").attempt == 2


def test_fenced_out_engine_cannot_finish_the_effect(make_engine, notifications):
    """Engine A stalls inside the receiver call. The operator takes over with engine B,
    which resends (the receiver dedupes) and finishes the workflow. When A wakes up,
    every write it tries is fenced out: the ledger and the log stay as B left them."""
    from wfk.engine import effect_key
    from wfk.store import LostClaim

    class StallingSink:
        idempotent = True

        def __init__(self, inner, while_stalled):
            self.inner, self.while_stalled = inner, while_stalled

        def send(self, key, payload):
            result = self.inner.send(key, payload)   # receiver accepted ...
            self.while_stalled()                     # ... meanwhile B takes over and finishes
            return result

    def take_over_with_b():
        assert engine_b.run(wf_id, take_over=True) == "SUCCEEDED"

    engine_b = make_engine(registry(FileNotifier(notifications)))
    sink_a = FileNotifier(notifications)
    engine_a = make_engine(registry(sink_a, notify=NotifyTool(StallingSink(sink_a, take_over_with_b))))
    wf_id = engine_a.start(TRIAGE_V1, INPUT)
    with pytest.raises(LostClaim):
        engine_a.run(wf_id)

    store = engine_b.store
    wf = store.load_workflow(wf_id)
    assert wf.state == "SUCCEEDED" and wf.owner is None
    effect = store.get_effect(effect_key(wf_id, "notify", "email"))
    assert effect.status == "DONE" and effect.result["status"] == "duplicate"  # B's record, not A's
    assert store.events(wf_id)[-1].type == "OWNER_RELEASED"                    # nothing after B released
    assert store.events(wf_id, "EFFECT_DONE")[0].attempt == 2                  # attributed to B's attempt
    with open(notifications) as f:
        assert len(f.readlines()) == 1


def test_backoff_wait_is_clamped_when_the_clock_steps_backwards(make_engine, notifications, clock):
    """next_run_at is wall-clock time from another process. If the clock stepped back by an
    hour, the remainder must be bounded by the largest delay the engine ever schedules."""
    from wfk.engine import Engine
    from wfk.faults import Flaky

    def die_while_sleeping(_s):
        raise SimulatedCrash("killed during backoff")

    engine1 = make_engine(registry(FileNotifier(notifications), extract=Flaky(ExtractTool(), 1)))
    engine1.sleep = die_while_sleeping
    wf_id = engine1.start(TRIAGE_V1, INPUT)
    with pytest.raises(SimulatedCrash):
        engine1.run(wf_id)

    clock.t -= 3600                                       # the clock went back an hour
    engine2 = make_engine(registry(FileNotifier(notifications)))
    assert engine2.run(wf_id, take_over=True) == "SUCCEEDED"
    assert clock.slept == [Engine.BACKOFF_CAP_S]          # 2 s, not 3600.05 s
