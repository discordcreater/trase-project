"""Tools: the only code that does work. Tools never touch the store.

A tool receives a ``StepContext`` (identity + two kernel services:
``record_decision`` and ``run_effect``) and the merged inputs
(workflow input + outputs of earlier steps), and returns a JSON-able dict.

Error contract:
  RetryableError  "try again"              -> retried up to max_attempts
  FatalError      "this can never work"    -> step fails now
  anything else   treated as retryable     (losing less work is the safe default)

Fault-injection wrappers for tests and the demo live in ``faults.py``.
"""
from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any, Dict, Optional, Protocol

if TYPE_CHECKING:  # tools depend on the context's *interface* only
    from .engine import StepContext


class RetryableError(Exception):
    pass


class FatalError(Exception):
    pass


class Tool(Protocol):
    name: str
    side_effect_free: bool  # True: no ledger needed, safe to re-run on retry
    # idempotent: True means the receivers of this tool's effects dedupe on the key the
    # kernel passes them, so an ambiguous (CLAIMED, not DONE) effect may be re-called
    # with the same key. It is a statement about the receiver, declared by the tool.
    idempotent: bool

    def run(self, ctx: "StepContext", inputs: Dict[str, Any]) -> Dict[str, Any]: ...


class Sink(Protocol):
    """Receiver of the notify effect. ``send`` gets the idempotency key the kernel minted."""
    idempotent: bool  # honours the key (SES, SendGrid, Stripe do; a bare SMTP relay does not)

    def send(self, key: str, payload: Dict[str, Any]) -> Dict[str, Any]: ...


# --------------------------------------------------------------------------
# Document triage tools
# --------------------------------------------------------------------------

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
        ctx.record_decision("label", decision)  # auditable: why did this document get this label
        return decision


class NotifyTool:
    """The only effectful step: one email per workflow, through the effects ledger."""
    name, side_effect_free = "notify", False

    def __init__(self, sink: Sink) -> None:
        self.sink = sink
        self.idempotent = sink.idempotent  # the tool's only effect goes to this receiver

    def run(self, ctx: "StepContext", inputs: Dict[str, Any]) -> Dict[str, Any]:
        label = inputs["classify"]["label"]
        payload = {"to": inputs.get("notify_to", "triage@example.com"),
                   "subject": f"[{label}] {inputs['extract']['title']}",
                   "body": f"Document {inputs['ingest']['doc_id']} was classified as {label} "
                           f"(confidence {inputs['classify']['confidence']})."}
        return {"email": ctx.run_effect("email", payload, self.sink.send)}


class FileNotifier:
    """Demo receiver: appends one JSON line per *key* to a file, like a provider
    that honours an Idempotency-Key. Writes through its own file handle, outside
    the database, so the crash window between "accepted" and "DONE" is real.
    The set of seen keys is read from the file once per process and kept in memory,
    the way a provider keeps its dedupe index."""

    def __init__(self, path: str, idempotent: bool = True) -> None:
        self.path = path
        self.idempotent = idempotent
        self.calls = 0  # invocations, including deduped ones; the tests compare it to lines written
        self._seen: Optional[Dict[str, Dict[str, Any]]] = None

    def sent_keys(self) -> Dict[str, Dict[str, Any]]:
        # The file is the receiver's truth and this dict is its cache, loaded on first use:
        # a process that takes over after a crash reads the lines the dead one wrote, so
        # the dedupe survives the restart. What it does not survive is two live processes
        # appending to the same file: each would trust its own index. That is the
        # receiver's problem by design (a real provider dedupes in its own database), and
        # the kernel's owner fence is what keeps a second live process from getting here.
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
        if key in self.sent_keys():
            return {"status": "duplicate", "key": key}
        row = {"key": key, "payload": payload}
        with open(self.path, "a") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self.sent_keys()[key] = row
        return {"status": "sent", "key": key, "to": payload["to"]}
