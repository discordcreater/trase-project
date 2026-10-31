from wfk.spec import TRIAGE_V1
from wfk.faults import Flaky
from wfk.tools import ExtractTool, FileNotifier

from .conftest import INPUT, registry


def test_retry_then_success(make_engine, notifications, clock):
    flaky = Flaky(ExtractTool(), fail_times=2)
    engine = make_engine(registry(FileNotifier(notifications), extract=flaky))
    wf_id = engine.start(TRIAGE_V1, INPUT)

    assert engine.run(wf_id) == "SUCCEEDED"

    step = engine.store.load_step(wf_id, "extract")
    assert step.state == "SUCCEEDED" and step.attempt == 3 and flaky.calls == 3
    retries = engine.store.events(wf_id, "RETRY_SCHEDULED")
    assert [r.payload["delay_s"] for r in retries] == [0.05, 0.1]  # exponential backoff
    assert [round(s, 3) for s in clock.slept] == [0.05, 0.1]  # the persisted next_run_at was honoured
    # next_run_at was persisted on the row between attempts, then cleared on the next start
    assert all(r.payload["next_run_at"] for r in retries) and step.next_run_at is None
    # retries never touched the earlier step
    assert engine.store.load_step(wf_id, "ingest").attempt == 1
