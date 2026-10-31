#!/usr/bin/env bash
# Demo: a clean run, a retry, a crash mid-notify with safe resume, and replay.
# Usage: scripts/demo.sh            (uses .venv/bin/python if present, else python3)
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-$([ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)}"
DIR="$(mktemp -d)"
W() { "$PY" -m wfk --db "$DIR/wfk.db" --notifications "$DIR/notifications.jsonl" "$@"; }
hr() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }
# number of receiver lines carrying the effect key of workflow $1 (the file is keyed by effect key, not workflow id)
sent() {
  local key
  key="$(W history "$1" --kind EFFECT_CLAIMED --json | "$PY" -c 'import json,sys; print(json.loads(sys.stdin.readline())["payload"]["key"])')"
  grep -c "$key" "$DIR/notifications.jsonl" || true   # grep -c exits 1 on zero matches
}

hr "1. Clean run"
W start examples/doc.json --id clean

hr "2. Retry: extract fails twice (RetryableError), succeeds on attempt 3"
W start examples/doc.json --id retry --fail extract:2
W history retry --kind RETRY_SCHEDULED

hr "3. Crash after classify committed (like kill -9); then resume in a new process"
W start examples/doc.json --id crash1 --crash-after classify || echo "(process exited $?)"
W status crash1
echo "-- resume without --take-over is refused: the dead process still holds the claim"
W run crash1 || true
echo "-- operator confirms the process is dead and takes over"
W run crash1 --take-over
echo "-- ingest/extract/classify were not re-run (attempt counts unchanged); one notification:"
sent crash1

hr "4. Worst case: crash AFTER the receiver accepted the email, BEFORE the kernel recorded it"
W start examples/doc.json --id crash2 --crash-in-notify || echo "(process exited $?)"
echo "-- on disk: notify RUNNING, effect CLAIMED (not DONE), one line already in the receiver:"
W status crash2 | grep -E 'notify|effect'
W run crash2 --take-over | grep -E 'finished|notify|effect'
echo "-- still exactly one notification for crash2 (receiver deduped on the key):"
sent crash2
W history crash2 --kind EFFECT_AMBIGUOUS

hr "5. Decisions are queryable; replay rebuilds state from the log and checks it"
W history crash2 --kind DECISION
W replay crash2 | tail -3

hr "6. Cancel"
W start examples/doc.json --id pending --no-run
W cancel pending
W status pending | head -1

hr "7. Operator errors are messages, not tracebacks (exit codes 4 = store refused, 5 = bad input)"
W start examples/doc.json --id clean || echo "(exit $?)"
W run nope || echo "(exit $?)"

echo
echo "artifacts in $DIR"
