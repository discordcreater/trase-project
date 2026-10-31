from wfk.spec import TRIAGE_V1
from wfk.faults import Counting, Flaky
from wfk.tools import ClassifyTool, FileNotifier, NotifyTool

from .conftest import INPUT, registry


def test_exhaustion_fails_workflow(make_engine, notifications):
    """classify has max_attempts=2 and on_failure=fail_workflow."""
    notify = Counting(NotifyTool(FileNotifier(notifications)))
    engine = make_engine(registry(FileNotifier(notifications),
                                  classify=Flaky(ClassifyTool(), fail_times=99), notify=notify))
    wf_id = engine.start(TRIAGE_V1, INPUT)

    assert engine.run(wf_id) == "FAILED"

    classify = engine.store.load_step(wf_id, "classify")
    assert classify.state == "FAILED" and classify.attempt == 2
    assert engine.store.events(wf_id, "STEP_FAILED")[0].payload["reason"] == "exhausted"
    assert engine.store.load_step(wf_id, "notify").state == "PENDING"  # never started
    assert notify.calls == 0
    assert engine.store.load_workflow(wf_id).owner is None  # claim released


def test_skip_policy_lets_workflow_succeed(make_engine, notifications):
    """notify has on_failure=skip: five failures skip the step, the workflow still succeeds."""
    notify = Flaky(NotifyTool(FileNotifier(notifications)), fail_times=99)
    engine = make_engine(registry(FileNotifier(notifications), notify=notify))
    wf_id = engine.start(TRIAGE_V1, INPUT)

    assert engine.run(wf_id) == "SUCCEEDED"

    step = engine.store.load_step(wf_id, "notify")
    assert step.state == "SKIPPED" and step.attempt == 5 and notify.calls == 5
    skipped = engine.store.events(wf_id, "STEP_SKIPPED")[0].payload
    assert skipped["policy"] == "skip" and skipped["reason"] == "exhausted"
