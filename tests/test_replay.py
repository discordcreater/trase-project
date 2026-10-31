import pytest

from wfk import cli
from wfk.engine import consistency_diff, rebuild_state
from wfk.spec import TRIAGE_V1
from wfk.store import IllegalTransition
from wfk.faults import CrashingSink, Flaky, SimulatedCrash
from wfk.tools import ExtractTool, FileNotifier, NotifyTool

from .conftest import INPUT, registry


def test_replay_rebuild_equals_tables(make_engine, notifications, db_path, capsys):
    """After a retry, a crash in notify and a resume, the log reproduces every row."""
    crashing = FileNotifier(notifications)
    engine1 = make_engine(registry(crashing, extract=Flaky(ExtractTool(), 2),
                                   notify=NotifyTool(CrashingSink(crashing))))
    wf_id = engine1.start(TRIAGE_V1, INPUT)
    with pytest.raises(SimulatedCrash):
        engine1.run(wf_id)
    assert consistency_diff(engine1.store, wf_id) == []   # consistent even mid-crash

    engine2 = make_engine()
    assert engine2.run(wf_id, take_over=True) == "SUCCEEDED"
    assert consistency_diff(engine2.store, wf_id) == []

    p = rebuild_state(engine2.store.events(wf_id))
    assert p.workflows[wf_id] == {"state": "SUCCEEDED", "cancel_requested": False, "owner": None}
    assert p.steps[(wf_id, "extract")]["attempt"] == 3
    assert list(p.effects.values())[0]["status"] == "DONE"

    assert cli.main(["--db", db_path, "replay", wf_id]) == 0
    out = capsys.readouterr().out
    assert "CONSISTENT" in out and "DECISION" in out and "EFFECT_AMBIGUOUS" in out


def test_replay_detects_tampered_table(make_engine, db_path, capsys):
    engine = make_engine()
    wf_id = engine.start(TRIAGE_V1, INPUT)
    engine.run(wf_id)
    engine.store.conn.execute("UPDATE steps SET attempt = 7 WHERE workflow_id = ? AND name = 'classify'", (wf_id,))

    assert consistency_diff(engine.store, wf_id) == ["step classify.attempt: log says 1, table says 7"]
    assert cli.main(["--db", db_path, "replay", wf_id]) == 1
    assert "INCONSISTENT" in capsys.readouterr().out


def test_replay_rejects_illegal_log(make_engine):
    """The reducer applies the same state machine: a log that skips RUNNING is a finding."""
    engine = make_engine()
    wf_id = engine.start(TRIAGE_V1, INPUT)
    engine.store.append_event(wf_id, "ingest", "STEP_SUCCEEDED", 1, {"attempt": 1, "output": {}}, owner=None)
    with pytest.raises(IllegalTransition):
        rebuild_state(engine.store.events(wf_id))
