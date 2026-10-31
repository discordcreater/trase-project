import json

import pytest

from wfk import cli


def _write(tmp_path, name, obj):
    p = tmp_path / name
    p.write_text(json.dumps(obj))
    return str(p)


@pytest.fixture
def run(tmp_path, db_path, notifications):
    def _run(*args):
        return cli.main(["--db", db_path, "--notifications", notifications, *args])
    return _run


def test_operator_errors_exit_cleanly(run, tmp_path, capsys):
    doc = _write(tmp_path, "doc.json", {"document": {"id": "d", "text": "Invoice\nTotal due: 1"}})
    assert run("start", doc, "--id", "w1") == 0
    assert run("start", doc, "--id", "w1") == 4                       # exists: not a traceback
    assert "already exists" in capsys.readouterr().err
    assert run("start", doc, "--spec", "nope@3") == 5
    assert run("start", _write(tmp_path, "list.json", [1, 2])) == 5    # not an object
    assert run("start", doc, "--fail", "ghost:1") == 5
    assert run("status", "missing") == 4
    assert run("history", "missing") == 4                              # not an empty history
    assert run("cancel", "w1") == 4                                    # terminal refuses
    assert run("start", doc, "--spec", _write(tmp_path, "bad.json", {"name": "x", "version": 1,
                                                                    "steps": [{"nme": "a"}]})) == 5


def test_spec_file_round_trips_through_start_and_run(run, tmp_path, capsys):
    spec = {"name": "custom", "version": 1,
            "steps": [{"name": "ingest", "tool": "ingest"}, {"name": "extract", "tool": "extract"}]}
    spec_path = _write(tmp_path, "spec.json", spec)
    doc = _write(tmp_path, "doc.json", {"document": {"id": "d", "text": "Hello\nworld"}})
    assert run("start", doc, "--id", "c1", "--spec", spec_path, "--no-run") == 0
    assert run("run", "c1") == 5                                       # file spec not registered here
    assert "not registered" in capsys.readouterr().err
    assert run("run", "c1", "--spec", spec_path) == 0
    assert "custom@1" in capsys.readouterr().out


def test_newer_schema_is_refused(run, db_path, capsys):
    """A file from a newer kernel is refused before any DDL runs: it is handed back untouched."""
    import sqlite3
    sqlite3.connect(db_path).execute("PRAGMA user_version = 99").close()
    assert run("status") == 4
    assert "newer than this kernel" in capsys.readouterr().err
    tables = sqlite3.connect(db_path).execute("SELECT name FROM sqlite_master").fetchall()
    assert tables == []                                                # nothing of ours was created
