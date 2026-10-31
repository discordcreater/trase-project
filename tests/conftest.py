from __future__ import annotations

from typing import Callable, Dict, List, Optional

import pytest

from wfk.engine import Engine
from wfk.faults import CrashAfterCommit
from wfk.store import Store
from wfk.tools import ClassifyTool, ExtractTool, FileNotifier, IngestTool, NotifyTool, Tool

INPUT = {
    "document": {"id": "doc-1", "mime": "text/plain",
                 "text": "Invoice #1\nTotal due: $10. Payment terms: net 30."},
    "notify_to": "ap@example.com",
}


class FakeClock:
    """Injected into Store (time) and Engine (sleep) so backoff never really waits."""

    def __init__(self, t: float = 1_700_000_000.0) -> None:
        self.t = t
        self.slept: List[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "wfk.db")


@pytest.fixture
def notifications(tmp_path) -> str:
    return str(tmp_path / "notifications.jsonl")


def registry(sink: FileNotifier, **overrides: Tool) -> Dict[str, Tool]:
    reg: Dict[str, Tool] = {"ingest": IngestTool(), "extract": ExtractTool(),
                            "classify": ClassifyTool(), "notify": NotifyTool(sink)}
    reg.update(overrides)
    return reg


@pytest.fixture
def make_engine(db_path, notifications, clock) -> Callable[..., Engine]:
    """Each call opens a *fresh* Store on the same file: a new process, same disk.
    ``crash_after=<step>`` makes that process die right after the step's SUCCEEDED commit."""
    stores: List[Store] = []

    def _make(reg: Optional[Dict[str, Tool]] = None, crash_after: Optional[str] = None, **kw) -> Engine:
        store = CrashAfterCommit(db_path, crash_after, clock=clock) if crash_after else Store(db_path, clock=clock)
        stores.append(store)
        return Engine(store, reg or registry(FileNotifier(notifications)), sleep=clock.sleep, **kw)

    yield _make
    for s in stores:
        s.close()
