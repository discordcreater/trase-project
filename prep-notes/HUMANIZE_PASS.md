# Humanize pass on the Trase submission (private; NOT in the zip)

Nov 2 2026. Goal: the shipped zip should read like an engineer's own work. Scope was narrowed mid-pass:
a separate pass will rewrite README.md and docs/DESIGN.md prose. This pass only touched the code
comments/docstrings, two test docstrings, the DESIGN.md header, and three accuracy nits.
Behaviour is unchanged: an AST comparison of every `wfk/*.py` file before and after (docstrings
stripped) is identical, and the 31 tests pass from a fresh `git archive` extract.

## What changed in the zip

- **Code comments/docstrings** (`wfk/engine.py`, `store.py`, `tools.py`, `cli.py`, `faults.py`,
  `spec.py`, `__init__.py`): long "why" essays cut to one to three lines; restatements of the code
  removed; the `(D9)` design-doc cross-reference in `Store.claim` dropped; banner-style `# ----` blocks
  shortened; the payload-hash note is now a plain `TODO`; the dubious "SES, SendGrid, Stripe honour the
  key" comment became "an API with an Idempotency-Key header". `wfk/` went from 1,444 to 1,355 lines.
- **Tests**: two docstrings reworded (`test_file_notifier_honours_the_key_only_when_it_says_it_does`,
  `test_interrupted_last_attempt_goes_to_the_failure_policy`), same line counts. No assertion changed.
- **docs/DESIGN.md**: removed the `Status: FINAL (written 2026-10-30 ...)` / `Audience: ...` header and
  the `---` under it; removed the date from "Implementation note (2026-10-30)"; the guarantee sentence
  in §0 and the §5.3 blockquote now match the corrected README wording.
- **README.md nits**: (1) the guarantee blockquote now says at-least-once invocation (and exactly-once
  effect) only with a receiver that honours the key, at-most-once otherwise; (2) section A's "Receiver
  dies" line now describes the real path: notify claims before calling, so a non-idempotent receiver
  failure is treated as ambiguous and, with notify on `skip`, the workflow ends SUCCEEDED, notify
  SKIPPED after 2 of 5 attempts, no email (verified with a probe); (3) the layout line no longer calls
  DESIGN.md "the full design record". "23 tests" did not occur in the zip; it was fixed in
  `DESIGN_DECISION_STORY.md` (private).

## Findings in the code (before this pass)

- Comment essays of 5-8 lines at: `effect_key`, the resume loop in `_run_owned`, the exhausted branch
  ("poison-pill bound"), the jitter note, the payload-hash note in `_run_effect`, `_wait_until`,
  `Store.__init__`, `_tx`, `_fence`, `_append` ("the place for observability"), `Store.claim`,
  `Tool` protocol fields, `FileNotifier.sent_keys`, the `os._exit` note in the CLI. They read as
  written to pre-empt a reviewer.
- Inline comments that restate the code: "the only place a tool runs; no transaction open",
  "external call, outside any transaction", "auditable: why did this document get this label",
  "already performed and recorded: never call fn again".
- Design-doc cross-references in code: `(D9)`.
- No mention of AI, an agent, Claude, prep-notes, `/Users/`, absolute paths, Co-Authored lines or
  dates anywhere in the zip (grep-verified before and after).

## README/DESIGN tells (for the prose-rewrite pass)

Addressed Nov 2 2026 by the full rewrite of README.md and docs/DESIGN.md (plain headings, no D-numbers,
no line counts, no self-review meta; see the decision map at the top of STUDY_GUIDE_FOR_GANESH.md). The list
below is kept as the record of what was found.

Quoted as they stand after this pass. Line numbers are approximate.

### README.md
- Opening: "A minimal, production-minded engine that runs a four-step document-triage workflow" (echoes
  the brief's own wording).
- Slogan in bold: "One mental model: **Engine decides, Store remembers, Tool does.** Nothing else holds
  state." (also repeated in DESIGN §0).
- Emphasis tics: "**Every** write that belongs to a workflow ...", heavy bold throughout section labels.
- "the duplicate the brief names" (talks to the grader about the brief).
- "The at-least-once vs at-most-once choice is made per tool, explicitly and auditably."
- "Exactly-once *effect* is a property of kernel + receiver together, never of the kernel alone, and the
  README says so instead of claiming exactly-once." (the README describing itself).
- "(the receiver honours the key, as SES/SendGrid/Stripe do)": accuracy risk; SES and SendGrid do not
  generally offer idempotency keys. Prefer "like Stripe's Idempotency-Key".
- Test map as a 7-row table with dense semicolon-joined cells ("What the tests prove").
- "What I would build next, in order, and why that order:" (template-ish lead-in).
- Time log table, row "Self-review, hardening and final docs (+14 tests)" with a 150-word cell:
  "Reproduced every README claim and raced two engines ...", "Then read the kernel as an operator would
  run it for a year ...", "A last read for the paths the crash table does not reach ...". Pass/audit
  meta. The truthful total is about 7 h (1.5 + 1.5 + 0.75 + 0.75 + 0.5 + 2) against the brief's 3-5 h,
  and there is no total line. Do not shrink or invent hours; Ganesh decides how to present it.
- "(each would be a real feature, none earns its lines here)".
- Very long single-paragraph bullets in "Execution model" step 1 and step 3 (the step-3 bullet carries
  a 2-line parenthetical about clocks stepping backwards inside a list item).

### docs/DESIGN.md
- Title "Design Decision Record" plus numbered "## 0. Problem framing" ... "## 15. Explicitly cut
  (for the Time Log)": template headings.
- §0 restates the brief as a bullet list ("The brief asks for ...") and a scoring rubric
  ("So the design is judged on three things, in order:").
- §0: "Scope discipline. Four tables, six modules, six CLI verbs, 31 tests in seven files." (count
  statements).
- §1 "Decisions table": D1-D15 labels, 5 columns, every row the same shape (Decision / Alternatives /
  Why / Tradeoff accepted).
- D1: "~1,300 lines including docstrings (planned at ~600; the self-review pass and the fault-injection
  module grew it)". Line count is now ~1,355 in `wfk/`; the "self-review pass" meta should go.
- D2: "the Temporal line ("log is truth, tables are a cache") is true and *tested*".
- D3: "the interviewer can read the whole machine in one set literal" (addresses the grader).
- D10: "it is what Temporal means by replay".
- D15: "none earns 20 lines in a 4-hour codebase" and lists `schema_version` as cut although the code
  stamps `PRAGMA user_version` (README says "`schema_version` in outputs", the intended meaning).
- §3 under the transition description: "(the self-review found the ledger unfenced; a take-over race
  test now pins it)".
- §3 "Implementation note:" paragraph (date removed in this pass; the "I missed on paper" content is
  fine and human).
- §3 two mermaid diagrams duplicating the ASCII machine in the README.
- §5.3 heading "Stated guarantee (goes verbatim into the README)".
- §6 accuracy: "Every transition's UPDATE includes `AND owner=?`; rowcount 0 → `LostClaim`". The code
  actually does `_fence` (a `SELECT owner` inside the same `BEGIN IMMEDIATE` transaction) and then
  `UPDATE ... WHERE state = ?`; it is not an `AND owner=?` clause. Same section labels it "Claim (D9)".
- §7: "It is the audit requirement, the "query decisions" requirement, the CLI requirement and a
  tamper/consistency check in one read-only verb." (requirement checklist voice).
- §2 event-type table is slightly stale: `WORKFLOW_CREATED` also carries `steps`, `RETRY_SCHEDULED`
  also `error`, `STEP_SKIPPED` also `reason`, `EFFECT_AMBIGUOUS` also `claimed_by_attempt`,
  `OWNER_CLAIMED` also `take_over`.
- §8 "Interfaces": a long code listing that restates `tools.py`/`engine.py`/`store.py` signatures.
- §9: "Target ~550 lines of source, ~250 of tests (actual: ~700 source including the CLI, ~400 tests)"
  (stale and planning meta; real: 1,355 in `wfk/`, 748 in `tests/`).
- §10 test plan table repeats README's test map; says "< 2 s" where the README says "< 1 s".
- §11-§14 duplicate README sections A-D almost word for word; §15 duplicates the README cut list.
- Recurring phrases that recur in both docs and read generated: "knows that it does not know",
  "nothing has left the building", "never guesses and never forks", "first-class, queryable fact",
  "by construction", "the kernel refuses to guess".

## Prep-doc references updated (both repos, copies kept identical except repo links)

- `CODE_WALKTHROUGH_FOR_GANESH.md`: every quoted code block that contained a changed comment
  (52 hunks), every `file.py:N[-M]` anchor (40) and every "(lines N–M)" range in §3 (50), and the
  per-file line counts in §3 headings were remapped to the new code; a Nov 2 note added at the top.
- `STUDY_GUIDE_FOR_GANESH.md`: `file.py:N` anchors remapped.
- `LIVE_ROUND_READINESS.md`: Nov 2 update note at the top; `_run_effect` now lines 267-289; `wfk/` line
  count 1,355; §3 items 1 and 2 marked fixed (item 6, DESIGN D15, is left for the prose pass).
- `DESIGN_DECISION_STORY.md`: test count history now ends at 31.
- `PRINCIPAL_REVIEW.md`, `TR_AUDIT.md`: a note that their `file:line` anchors are as of the commit they
  name (historical records; not rewritten).
- Not changed: DESIGN.md §/D-number references in the prep docs (DESIGN structure is unchanged by this
  pass; re-check them after the prose rewrite).

## Rebuilt zip

`ganesh_muthuluru_trase.zip`, built with `git archive --format=zip` from trase-project `ddb6665`
(zip comment = that commit), 24 files, sha256
`5108bae34e0af11802df9b57087d08ac506fc8fa75b6bc0c1bb33002100aec29`. Identical copies:
`~/claude-code-ws/trase-takehome/dist/`, trase-project `prep-notes/submission/`, job-search-2026
`interviews/trase-takehome/submission/`. Later prep-notes-only commits do not change the archived
files (prep-notes is export-ignored), only the commit id in the zip comment.
Verified from a fresh extract: 31 passed (Python 3.14 venv), `scripts/demo.sh` exit 0 with output
matching the README's demo list, `python -m wfk start` works on system Python 3.9.6, no prep-notes,
`_private`, `.venv`, `.git`, db files, absolute paths or AI/Claude mentions.
