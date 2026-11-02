"""Workflow specs as dataclasses, plus a small JSON loader.

Don't edit a published version; a running workflow stays on the version it started with.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, Tuple

ON_FAILURE = ("fail_workflow", "skip")


@dataclass(frozen=True)
class StepSpec:
    name: str
    tool: str                          # registry key
    max_attempts: int = 3
    on_failure: str = "fail_workflow"  # or "skip"
    backoff_base_s: float = 0.05       # delay = min(base * 2**(attempt-1), Engine.BACKOFF_CAP_S)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError(f"step {self.name}: max_attempts must be >= 1")
        if self.on_failure not in ON_FAILURE:
            raise ValueError(f"step {self.name}: on_failure must be one of {ON_FAILURE}")


@dataclass(frozen=True)
class WorkflowSpec:
    name: str
    version: int
    steps: Tuple[StepSpec, ...]

    def __post_init__(self) -> None:
        names = [s.name for s in self.steps]
        if len(names) != len(set(names)):
            raise ValueError(f"spec {self.name}: duplicate step names")

    @property
    def key(self) -> Tuple[str, int]:
        return (self.name, self.version)


TRIAGE_V1 = WorkflowSpec("document_triage", 1, (
    StepSpec("ingest", "ingest", max_attempts=3),
    StepSpec("extract", "extract", max_attempts=3),
    StepSpec("classify", "classify", max_attempts=2),
    StepSpec("notify", "notify", max_attempts=5, on_failure="skip"),
))

SPECS: Dict[Tuple[str, int], WorkflowSpec] = {TRIAGE_V1.key: TRIAGE_V1}


def load_spec_json(path: str) -> WorkflowSpec:
    """{"name": ..., "version": 1, "steps": [{"name", "tool", "max_attempts"?, "on_failure"?}]}"""
    with open(path) as f:
        raw = json.load(f)
    try:
        steps = tuple(StepSpec(**s) for s in raw["steps"])
        return WorkflowSpec(raw["name"], int(raw["version"]), steps)
    except (KeyError, TypeError) as e:
        raise ValueError(f"{path}: malformed spec: {e}") from None
