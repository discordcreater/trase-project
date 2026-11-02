"""The tools that do the actual work. They never touch the store.

A tool gets a StepContext plus the workflow input merged with earlier steps'
outputs, and returns a JSON-able dict.

RetryableError means try again, FatalError means fail the step now. Anything
else is treated as retryable; I'd rather retry a bug a few times than drop work.
"""
from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any, Dict, Optional, Protocol

if TYPE_CHECKING:
    from .engine import StepContext


class RetryableError(Exception):
    pass


class FatalError(Exception):
    pass


class Tool(Protocol):
    name: str
    # Declarative only for now: the kernel doesn't enforce it (run_effect is opt-in).
    side_effect_free: bool
    # The receiver dedupes on the key we pass, so an ambiguous effect can be resent.
    idempotent: bool

    def run(self, ctx: "StepContext", inputs: Dict[str, Any]) -> Dict[str, Any]: ...


class Sink(Protocol):
    """Receiver of the notify effect; ``send`` gets the kernel's idempotency key."""
    idempotent: bool  # e.g. an API with an Idempotency-Key header; a bare SMTP relay isn't

    def send(self, key: str, payload: Dict[str, Any]) -> Dict[str, Any]: ...


# ---- document triage tools --------------------------------------------------

class IngestTool:
    name, side_effect_free, idempotent = "ingest", True, True

    def run(self, ctx: "StepContext", inputs: Dict[str, Any]) -> Dict[str, Any]:
        doc = inputs.get("document")
        if not isinstance(doc, dict):
            raise FatalError("input has no 'document' object")
        text = doc.get("text") or ""
        return {"doc_id": doc.get("id") or f"doc-{ctx.workflow_id[:8]}",
                "mime": doc.get("mime", "text/plain"),
                "size_bytes": len(text.encode("utf-8"))}


class ExtractTool:
    name, side_effect_free, idempotent = "extract", True, True

    def run(self, ctx: "StepContext", inputs: Dict[str, Any]) -> Dict[str, Any]:
        text = inputs["document"].get("text")
        if not isinstance(text, str) or not text.strip():
            raise FatalError("document has no text to extract")  # retrying cannot help
        lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
        return {"title": lines[0][:80], "body": " ".join(lines[1:]), "word_count": len(text.split())}


class ClassifyTool:
    name, side_effect_free, idempotent = "classify", True, True
    RULES = (
        ("invoice", ("invoice", "total due", "amount due", "payment terms")),
        ("contract", ("agreement", "hereby", "party", "term of")),
        ("resume", ("experience", "education", "skills")),
    )

    def run(self, ctx: "StepContext", inputs: Dict[str, Any]) -> Dict[str, Any]:
        text = (inputs["extract"]["title"] + " " + inputs["extract"]["body"]).lower()
        best, hits, matched = "other", 0, "default"
        for label, keywords in self.RULES:
            found = [k for k in keywords if k in text]
            if len(found) > hits:
                best, hits, matched = label, len(found), "keyword:" + ",".join(found)
        confidence = round(min(0.5 + 0.15 * hits, 0.95), 2) if hits else 0.3
        decision = {"label": best, "confidence": confidence, "rule": matched}
        ctx.record_decision("label", decision)
        return decision


class NotifyTool:
    """One email per workflow, sent through the effects ledger."""
    name, side_effect_free = "notify", False

    def __init__(self, sink: Sink) -> None:
        self.sink = sink
        self.idempotent = sink.idempotent

    def run(self, ctx: "StepContext", inputs: Dict[str, Any]) -> Dict[str, Any]:
        label = inputs["classify"]["label"]
        payload = {"to": inputs.get("notify_to", "triage@example.com"),
                   "subject": f"[{label}] {inputs['extract']['title']}",
                   "body": f"Document {inputs['ingest']['doc_id']} was classified as {label} "
                           f"(confidence {inputs['classify']['confidence']})."}
        return {"email": ctx.run_effect("email", payload, self.sink.send)}


class FileNotifier:
    """Demo receiver: one JSON line per key in a file, like a provider that honours an
    Idempotency-Key. It lives outside the database on purpose, so the crash window
    between "accepted" and "DONE" is real."""

    def __init__(self, path: str, idempotent: bool = True) -> None:
        self.path = path
        self.idempotent = idempotent
        self.calls = 0  # includes deduped calls
        self._seen: Optional[Dict[str, Dict[str, Any]]] = None

    def sent_keys(self) -> Dict[str, Dict[str, Any]]:
        # Loaded from the file on first use, so a process that takes over after a crash
        # sees what the dead one sent. Two live writers would each trust their own copy;
        # the owner fence keeps that from happening.
        if self._seen is None:
            self._seen = {}
            if os.path.exists(self.path):
                with open(self.path) as f:
                    for ln in f:
                        if ln.strip():
                            row = json.loads(ln)
                            self._seen[row["key"]] = row
        return self._seen

    def send(self, key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.calls += 1
        if self.idempotent and key in self.sent_keys():
            return {"status": "duplicate", "key": key}
        # idempotent=False: ignores the key, every call is a delivery
        row = {"key": key, "payload": payload}
        with open(self.path, "a") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self.sent_keys()[key] = row
        return {"status": "sent", "key": key, "to": payload["to"]}
