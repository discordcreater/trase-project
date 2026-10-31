import pytest

from wfk.spec import TRIAGE_V1
from wfk.store import IllegalTransition, LostClaim
from wfk.tools import ExtractTool, FileNotifier, RetryableError

from .conftest import INPUT, registry


def test_illegal_transition_raises_and_writes_nothing(make_engine):
    engine = make_engine()
    wf_id = engine.start(TRIAGE_V1, INPUT)
    before = len(engine.store.events(wf_id))

    with pytest.raises(IllegalTransition):
        engine.store.transition_step(wf_id, "ingest", "PENDING", "SUCCEEDED", None,
                                     [("STEP_SUCCEEDED", {"attempt": 1, "output": {}})])

    assert len(engine.store.events(wf_id)) == before
    assert engine.store.load_step(wf_id, "ingest").state == "PENDING"


def test_stale_owner_cannot_commit(make_engine):
    """A transition carries the owner; a process that lost its claim is fenced out and rolled back."""
    engine = make_engine()
    wf_id = engine.start(TRIAGE_V1, INPUT)
    engine.store.claim(wf_id, "A")
    before = len(engine.store.events(wf_id))
    with pytest.raises(LostClaim):
        engine.store.transition_workflow(wf_id, "PENDING", "RUNNING", "B", "stale")
    assert engine.store.load_workflow(wf_id).state == "PENDING"
    assert len(engine.store.events(wf_id)) == before


class CancelsThenFails:
    """Requests cancellation from inside the tool, then fails: the engine must see
    the flag before the next attempt and never start it."""
    name, side_effect_free, idempotent = "classify", True, True

    def __init__(self, engine):
        self.engine, self.calls = engine, 0

    def run(self, ctx, inputs):
        self.calls += 1
        self.engine.cancel(ctx.workflow_id, by="test")
        raise RetryableError("transient")


def test_cancel_between_attempts(make_engine, notifications):
    engine = make_engine()
    tool = CancelsThenFails(engine)
    engine.registry = registry(FileNotifier(notifications), classify=tool)
    wf_id = engine.start(TRIAGE_V1, INPUT)

    assert engine.run(wf_id) == "CANCELLED"

    assert tool.calls == 1
    assert engine.store.load_step(wf_id, "classify").state == "PENDING"
    assert engine.store.load_step(wf_id, "notify").attempt == 0
    assert engine.store.events(wf_id, "CANCEL_REQUESTED")[0].payload["by"] == "test"
    with pytest.raises(IllegalTransition):  # terminal refuses
        engine.cancel(wf_id)
    assert engine.run(wf_id) == "CANCELLED"  # running a terminal workflow is a no-op


def test_cancel_before_first_run(make_engine):
    engine = make_engine()
    wf_id = engine.start(TRIAGE_V1, INPUT)
    assert engine.cancel(wf_id) == "CANCELLED"
    assert engine.store.load_workflow(wf_id).state == "CANCELLED"
    assert engine.run(wf_id) == "CANCELLED"
    assert engine.store.load_step(wf_id, "ingest").attempt == 0


def test_fatal_error_fails_without_retry(make_engine, notifications):
    engine = make_engine()
    wf_id = engine.start(TRIAGE_V1, {"document": {"id": "x", "text": "   "}})
    assert engine.run(wf_id) == "FAILED"
    extract = engine.store.load_step(wf_id, "extract")
    assert extract.state == "FAILED" and extract.attempt == 1
    assert engine.store.events(wf_id, "STEP_FAILED")[0].payload["reason"] == "fatal"
    assert engine.store.events(wf_id, "RETRY_SCHEDULED") == []
    # classify and notify stayed PENDING, visible in status
    assert [s.state for s in engine.store.load_steps(wf_id)] == ["SUCCEEDED", "FAILED", "PENDING", "PENDING"]


def test_stale_owner_cannot_write_ledger_or_events(make_engine):
    """The owner fence covers every write, not only transitions: a process that lost
    its claim can neither claim/complete an effect nor append an event."""
    from wfk.engine import effect_key
    engine = make_engine()
    wf_id = engine.start(TRIAGE_V1, INPUT)
    engine.store.claim(wf_id, "A")
    key = effect_key(wf_id, "notify", "email")
    before = len(engine.store.events(wf_id))

    with pytest.raises(LostClaim):
        engine.store.claim_effect(key, wf_id, "notify", "email", 1, owner="B")
    engine.store.claim_effect(key, wf_id, "notify", "email", 1, owner="A")
    with pytest.raises(LostClaim):
        engine.store.complete_effect(key, {"status": "sent"}, owner="B", attempt=1)
    with pytest.raises(LostClaim):
        engine.store.append_event(wf_id, "classify", "DECISION", 1, {"key": "x", "data": {}}, owner="B")

    assert engine.store.get_effect(key).status == "CLAIMED"
    assert [e.type for e in engine.store.events(wf_id)[before:]] == ["EFFECT_CLAIMED"]


def test_start_rejects_bad_input_and_duplicate_id(make_engine):
    from wfk.store import AlreadyExists
    engine = make_engine()
    with pytest.raises(ValueError):
        engine.start(TRIAGE_V1, [1, 2])                       # not an object: fail before persisting
    with pytest.raises(ValueError):
        engine.start(TRIAGE_V1, {**INPUT, "extract": "x"})    # would be shadowed by extract's output
    assert engine.store.list_workflows() == []
    engine.start(TRIAGE_V1, INPUT, "w1")
    with pytest.raises(AlreadyExists):
        engine.start(TRIAGE_V1, INPUT, "w1")


def test_run_on_terminal_workflow_does_not_claim(make_engine):
    engine = make_engine()
    wf_id = engine.start(TRIAGE_V1, INPUT)
    assert engine.run(wf_id) == "SUCCEEDED"
    n = len(engine.store.events(wf_id))
    assert engine.run(wf_id) == "SUCCEEDED"
    assert len(engine.store.events(wf_id)) == n  # no OWNER_CLAIMED / OWNER_RELEASED noise


def test_cancel_during_backoff_is_honoured_before_the_next_attempt(make_engine, notifications, clock):
    """A cancel that lands while the engine sleeps out a backoff must stop the next
    attempt, not run it first and notice afterwards."""
    from wfk.faults import Flaky
    flaky = Flaky(ExtractTool(), fail_times=99)
    engine = make_engine(registry(FileNotifier(notifications), extract=flaky))
    wf_id = engine.start(TRIAGE_V1, INPUT)

    def cancel_while_sleeping(s):
        clock.sleep(s)
        engine.cancel(wf_id, by="operator")

    engine.sleep = cancel_while_sleeping
    assert engine.run(wf_id) == "CANCELLED"
    extract = engine.store.load_step(wf_id, "extract")
    assert flaky.calls == 1 and extract.attempt == 1 and extract.state == "PENDING"
    assert engine.store.load_step(wf_id, "classify").attempt == 0
