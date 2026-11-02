# START HERE: Trase live-round prep (private; none of this is in the submission zip)

**What you submit:** only the zip (a committed copy is at `prep-notes/submission/ganesh_muthuluru_trase.zip`) `~/claude-code-ws/trase-takehome/dist/ganesh_muthuluru_trase.zip` (24 files: README.md, docs/DESIGN.md, examples/, pyproject.toml, requirements.txt, scripts/demo.sh, tests/, wfk/). It is built by `git archive` from this repo; `.gitattributes` marks `prep-notes/` and itself `export-ignore`, so nothing in this folder can ever enter the zip. Never zip the repo folder by hand.

## Read in this order (minimum 70 min; all links are in this repo)
1. [LIVE_ROUND_READINESS.md](https://github.com/discordcreater/trase-project/blob/main/prep-notes/LIVE_ROUND_READINESS.md): running notes (§7), likely questions (§4), live change drills (§5), debug drills (§6). 30 min.
2. [STUDY_GUIDE_FOR_GANESH.md](https://github.com/discordcreater/trase-project/blob/main/prep-notes/STUDY_GUIDE_FOR_GANESH.md) §0, §3, §4: numbers, control flow, every crash point. 30 min.
3. The code: `wfk/engine.py` (`_run_owned`, `_run_step`, `_run_effect`) and `wfk/store.py` (`_tx`, `_fence`, `claim`, `transition_step`). They will point at lines. 15 min.
4. [INTERVIEW_PREP.md](https://github.com/discordcreater/trase-project/blob/main/prep-notes/INTERVIEW_PREP.md) §4 pushback, §5 drills. 20 min.
5. [DESIGN_DECISION_STORY.md](https://github.com/discordcreater/trase-project/blob/main/prep-notes/DESIGN_DECISION_STORY.md): why this and not X. 25 min.
6. [CODE_WALKTHROUGH_FOR_GANESH.md](https://github.com/discordcreater/trase-project/blob/main/prep-notes/CODE_WALKTHROUGH_FOR_GANESH.md) §9-§10: the newest changes. 20 min.
7. [TR_AUDIT.md](https://github.com/discordcreater/trase-project/blob/main/prep-notes/TR_AUDIT.md) and [PRINCIPAL_REVIEW.md](https://github.com/discordcreater/trase-project/blob/main/prep-notes/PRINCIPAL_REVIEW.md): what reviewers found and fixed. 20 min.
8. Skim only if needed: [PRESENTATION.md](https://github.com/discordcreater/trase-project/blob/main/prep-notes/PRESENTATION.md) (they will likely read offline, not watch a deck), [REQUIREMENTS_ONE_PAGER.md](https://github.com/discordcreater/trase-project/blob/main/prep-notes/REQUIREMENTS_ONE_PAGER.md), [COMPLETENESS_AUDIT.md](https://github.com/discordcreater/trase-project/blob/main/prep-notes/COMPLETENESS_AUDIT.md), [DESIGN_DEBATE.md](https://github.com/discordcreater/trase-project/blob/main/prep-notes/DESIGN_DEBATE.md), [REVIEW_RESPONSE.md](https://github.com/discordcreater/trase-project/blob/main/prep-notes/REVIEW_RESPONSE.md).

## Context and process (copied from the job-search repo, Nov 2 2026), in `context/`
- [trase-interview-process.md](https://github.com/discordcreater/trase-project/blob/main/prep-notes/context/trase-interview-process.md): master file (rounds, people, status).
- [trase-postmortem.md](https://github.com/discordcreater/trase-project/blob/main/prep-notes/context/trase-postmortem.md)
- [2026-10-28 recruiter call notes (Adam Elconin)](https://github.com/discordcreater/trase-project/blob/main/prep-notes/context/2026-10-28-red-cell-trase-adam-elconin-recruiter-call-notes.md) and [prep doc](https://github.com/discordcreater/trase-project/blob/main/prep-notes/context/2026-10-28-red-cell-partners-trase-adam-elconin-prep.md)
- [08-coding-patterns.md](https://github.com/discordcreater/trase-project/blob/main/prep-notes/context/08-coding-patterns.md): the 45-minute coding protocol (finish a working baseline first, then optimize; talk while typing). Use it for the no-AI live coding.

## Live-round rules from Trase (assignment page)
Take-home allowed AI; the LIVE round does not (no AI, no code generators, no search engines). They will ask you to code, debug and explain, and may add a new requirement. Practise in a COPY, never in this repo: `cp -R ~/claude-code-ws/trase-project /tmp/wfk-drill`.

---
(Original index below.)

# Trase take-home: private prep docs (copied from ~/claude-code-ws/trase-takehome/docs, Oct 30 2026; refreshed Oct 31 after the review pass)

Confidential (Trase asked that the brief and solution never be posted publicly; this repo is private). The brief PDF/text is NOT here: it lives only at `~/claude-code-ws/trase-takehome/_private/brief.{pdf,txt}` on the Mac.

- Canonical code (also the submission): https://github.com/discordcreater/trase-project (PRIVATE), local `~/claude-code-ws/trase-project`.
- Submission zip: `~/claude-code-ws/trase-takehome/dist/ganesh_muthuluru_trase.zip` (built with `git archive` from trase-project HEAD).
- `REQUIREMENTS_ONE_PAGER.md`: the brief as explicit requirements, implicit expectations and evaluation criteria.
- `PRINCIPAL_REVIEW.md` (Oct 31): gap analysis, quality verdict per dimension, the inline notes added, commits, zip check, the one decision left to Ganesh.
- `TR_AUDIT.md` (Oct 31 evening): second principal pass, ten ranked findings (T1-T10), seven applied with tests, 27 → 31 tests, zip rebuilt and tested from the archive.
- `COMPLETENESS_AUDIT.md`: brief requirement -> where satisfied -> evidence; verdict COMPLETE.
- `CODE_WALKTHROUGH_FOR_GANESH.md`: annotated, line-anchored walkthrough (anchors as of commit `080c8f0`); §9 = what changed in the review pass, §10 = the evening pass, and how to talk about each.
- `DESIGN_DECISION_STORY.md`: the decision narrative and interview commentary.
- `STUDY_GUIDE_FOR_GANESH.md`: every function, number and crash point, for the no-AI live round.
- `PRESENTATION.md`: 12-slide deck with speaker notes, 2-minute opening, 30-second close.
- `INTERVIEW_PREP.md`: delivery, likely pushback with answers, live-round extension drills.
- `DESIGN_DEBATE.md`, `REVIEW_RESPONSE.md`: how the design was chosen and self-reviewed.

Any agent preparing a later Trase round reads BOTH repos (trase-project for the code, these docs for the narrative) before writing reports, prep docs or presentations. Master file: `interviews/trase-interview-process.md`.
