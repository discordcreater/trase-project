"""Fault injection for tests and the demo. Nothing in here is imported by the kernel.

Crashes are injected at the boundary where they happen in real life:
  - inside a tool / receiver call   (``CrashingSink``: the receiver accepted, then we die)
  - right after a commit             (``CrashAfterCommit``: a Store that dies after a given
                                      step's SUCCEEDED commit, i.e. between two steps)
The engine knows nothing about any of this.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

from .store import Store
from .tools import RetryableError, Sink, Tool

if TYPE_CHECKING:
    from .engine import StepContext


class SimulatedCrash(BaseException):
    """Stands in for kill -9. Deliberately not an Exception so no handler swallows it;
    the engine lets it propagate without releasing its claim, like a dead process."""


class _Wrapper:
    def __init__(self, inner: Tool) -> None:
        self.inner = inner
        self.name, self.side_effect_free, self.idempotent = inner.name, inner.side_effect_free, inner.idempotent


class Flaky(_Wrapper):
    """Raises RetryableError on the first ``fail_times`` calls, then delegates."""

    def __init__(self, inner: Tool, fail_times: int) -> None:
        super().__init__(inner)
        self.fail_times, self.calls = fail_times, 0

    def run(self, ctx: "StepContext", inputs: Dict[str, Any]) -> Dict[str, Any]:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RetryableError(f"{self.name}: transient failure #{self.calls}")
        return self.inner.run(ctx, inputs)


class Counting(_Wrapper):
    def __init__(self, inner: Tool) -> None:
        super().__init__(inner)
        self.calls = 0

    def run(self, ctx: "StepContext", inputs: Dict[str, Any]) -> Dict[str, Any]:
        self.calls += 1
        return self.inner.run(ctx, inputs)


class CrashingSink:
    """Worst case: the receiver accepted, then the process dies before DONE commits."""

    def __init__(self, inner: Sink) -> None:
        self.inner = inner
        self.idempotent = inner.idempotent

    def send(self, key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.inner.send(key, payload)
        raise SimulatedCrash("process died after the receiver accepted the notification")


class CrashAfterCommit(Store):
    """A Store that dies right after ``step``'s SUCCEEDED commit: the crash point
    *between* two steps, which no tool wrapper can reach."""

    def __init__(self, path: str, step: str, **kw: Any) -> None:
        super().__init__(path, **kw)
        self.crash_after = step

    def transition_step(self, wf_id: str, name: str, frm: str, to: str, *args: Any, **kw: Any) -> None:
        super().transition_step(wf_id, name, frm, to, *args, **kw)
        if to == "SUCCEEDED" and name == self.crash_after:
            raise SimulatedCrash(f"process died right after step {name} committed")


def parse_fail_spec(spec: Optional[str]) -> Dict[str, int]:
    """'extract:2,notify:1' -> {'extract': 2, 'notify': 1}"""
    out: Dict[str, int] = {}
    for part in (spec or "").split(","):
        if part.strip():
            name, _, n = part.partition(":")
            out[name.strip()] = int(n or 1)
    return out
