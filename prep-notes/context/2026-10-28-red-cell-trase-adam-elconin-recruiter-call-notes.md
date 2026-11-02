# Red Cell Partners / Trase.ai — Exploratory recruiter chat — Adam Elconin — Trase Platform / Origin (agent OS) — Call Notes

## Call 1 of the Trase process. Result: **interested on both sides; take-home project to be sent Fri Nov 6**; staff-level base band $220–240K, equity weak, no Bay Area uplift. Remote OK.

**Capture status:** `complete` (Wispr Flow text from Ganesh's device is primary; local Whisper `small` on the original audio is the cross-check; Wispr wins on conflicts).
- **Date:** Wed Oct 28 2026, 11:30 AM PT, Zoom, about 52 minutes of audio (scheduled 30 min; ran about 20 over).
- **Interviewer:** **Adam Elconin**, recruiter for Red Cell Partners (incubator/VC) portfolio companies (Wispr labels him "Speaker 1"; Ganesh's greeting in the text, "Erdem", is a Wispr mishearing). The Wispr text does not name him; identity is from the calendar invite and the Oct 22 email thread.
- **Role:** the **Platform / Operating System ("Origin")** role, [Greenhouse posting](https://job-boards.greenhouse.io/trase/jobs/5255658007) (Senior/Staff Software Engineer, Platform and Execution Model; posted band $180–240K; Seattle preferred, McLean VA or **Remote (USA)**). The exact posting link was in the 11:30 calendar invite (per Ganesh: always read the invite first).
- **Files:**
  - Recording: [`interviews/recordings/2026-10-28-red-cell-trase-adam-elconin-recruiter-call.m4a`](https://github.com/discordcreater/job-search-2026/blob/main/interviews/recordings/2026-10-28-red-cell-trase-adam-elconin-recruiter-call.m4a) (96 kbps mono; original kept outside git)
  - Wispr transcript (verbatim, primary, includes Ganesh's private post-call dictation): [`.wispr.txt`](https://github.com/discordcreater/job-search-2026/blob/main/interviews/transcripts/2026-10-28-red-cell-trase-adam-elconin-recruiter-call.wispr.txt)
  - Whisper audio transcript: [`.audio-whisper.txt`](https://github.com/discordcreater/job-search-2026/blob/main/interviews/transcripts/2026-10-28-red-cell-trase-adam-elconin-recruiter-call.audio-whisper.txt)
  - **Intelligence:** [`interviews/intelligence/2026-10-28-trase-adam-elconin-intel.md`](https://github.com/discordcreater/job-search-2026/blob/main/interviews/intelligence/2026-10-28-trase-adam-elconin-intel.md)
  - **Master file + dossier:** [`interviews/trase-interview-process.md`](https://github.com/discordcreater/job-search-2026/blob/main/interviews/trase-interview-process.md) · **Postmortem:** [`interviews/trase-postmortem.md`](https://github.com/discordcreater/job-search-2026/blob/main/interviews/trase-postmortem.md) · **Company file:** [`knowledge/companies/trase.md`](https://github.com/discordcreater/job-search-2026/blob/main/knowledge/companies/trase.md)
  - Prep doc: [`2026-10-28-red-cell-partners-trase-adam-elconin-prep.md`](https://github.com/discordcreater/job-search-2026/blob/main/interviews/2026-10-28-red-cell-partners-trase-adam-elconin-prep.md)
  - History: [LinkedIn thread](https://www.linkedin.com/in/adamelconin) · [Zoom invite email](https://mail.google.com/mail/#all/1a165ed7db53d95d) · Greenhouse application confirmation (Oct 22) · [ACTION_LOG Oct 22 4:39 PM](https://github.com/discordcreater/job-search-2026/blob/main/ACTION_LOG.md)

## How Ganesh knows Adam (the "we connected 10 years ago" question)
Dug from the LinkedIn export (`scripts/linkedin_msgs.py`, `scripts/linkedin_connections.py`), Gmail, and the contacts store. **Adam is right, and it is documented:**
- **Jun 25 2015:** LinkedIn connection (the connections export lists "connected 25 Jun 2015"). Adam was then a recruiter at an agency; the contacts store has `adam.elconin@jobspringpartners.com` (**Jobspring Partners**, a staffing agency), which is his "agency days".
- **Oct 19 2023:** an email from Ganesh to the old Jobspring address bounced (job search 2023); the store wrongly flags him do-not-contact for that bounce (fixed today in staging).
- **May 18 2024:** after the Cisco layoff, Ganesh messaged him on LinkedIn (H-1B timeline, "any SWE role"); Adam answered kindly, said he was freelancing for early startups and would pass along any role. No role followed.
- **Jan 8 2025:** Adam wrote that he joined a VC/incubator (Red Cell Partners) to recruit for portfolio companies in healthcare, cyber and defense, and invited Ganesh to look at open roles.
- **Aug 10 2026:** Ganesh wrote again (principal/senior-staff target, Agentforce orchestration, 50+ PB at Cisco). Adam replied the same day pointing to Trase.ai and a Greenhouse role ([5195313007](https://job-boards.greenhouse.io/trase/jobs/5195313007)), warning 7 technical loops were already running and it would close in about two weeks.
- **Oct 21 2026:** Ganesh followed up (the role had probably closed; same problem space). **Oct 22:** Adam: the full-stack role was filled, but there are openings for the **Platform/OS** and "12 more openings for various roles coming soon"; sent the Platform/OS link, a levels note (Trase levels map to Google levels; Staff = Principal at Salesforce; Senior Staff = Architect; Principal = Principal Architect; above Staff is "challenging but not impossible"), and a Zoom invite for Oct 28. **Same day Ganesh applied** on Greenhouse (confirmation "Thank you for applying to Trase!").
- No other prior Gmail threads; no other channels found.

## Summary by phase

### 00:00–03:00 — Reconnect
- Adam: "Long time no talk… 10 years… we originally got connected back in my agency days." Ganesh said "nice to catch up" (he did not recall the connection; the record above backs Adam).
- Adam: exploratory call; many more Trase roles coming; **platform is likely the best fit** versus forward-deployed work.

### 03:00–18:00 — "What's new?" (Ganesh's long answers)
- Ganesh: leads an important, mission-critical platform team at Salesforce Marketing Cloud (about $5B a year), the whole CI/CD process, and the **SFCA/SFCI** continuous-integration internal product.
- 🔴 **Why he is looking (volunteered):** compensation tied to RSUs, **stock down "almost half"**, no special refreshers, slowing growth, "Agentforce has limited adoption", "good time to look; if I find an opportunity I'll move, otherwise I'll stay". This breaks card #9 and P12 (never volunteer why you are leaving or the employer's problems).
- Strong parts: made the SDLC "more or less agentic" across three scrum teams with **rules, skills and guardrails** for the agents; how he would start a greenfield project (design with Claude, sub-agents build v1, review) versus a thick legacy stack (agents won't admit they're lost; **tens of millions of tests, about 26 years old**, "FIT tests", hard to trace a failure to an owning team).
- Tangents (about 6 minutes): Amazon rewrote legacy; Salesforce "core" is a Java "monolith of monolith", **150M+ lines**; Mark Benioff's old commits in the history; open-source vetting and a private vetted repository; traffic never leaves the data center. Adam enjoyed it but it ate the call's clock.

### 18:00–24:00 — Platform and ML-platform positioning
- Ganesh's market take (few build models; everyone calls Anthropic/OpenAI/Gemini; ML platform and infrastructure orchestration engineers are the need). **Honest gap:** "ML is something I have some experience with, but not at production; never had a model-building opportunity" (open-source models on managed infrastructure for internal apps). Adam: no problem; it isn't required.

### 24:00–32:00 — Adam on Trase and the role
- **Strategy shift this year:** last year agents for healthcare then government and regulated industries; this year the focus is the **platform**: **Origin**, "an operating system for agents": orchestration, governance, reliability, security so regulated customers can trust agents. Also an **SDK** for third parties (model- and infrastructure-agnostic), and the **forward-deployed "leagues"** team (many former ML engineers) that builds agents for customers.
- **Red Cell Federal** (new): owns federal sales, forward-deployed work and deployments; Trase stays commercial; separate federal infrastructure.
- **Platform team:** about **15 people**, two more openings; org is splitting into **product engineering** (agent building) and **platform** (OS + SDK). This role is **OS-focused**, "an orchestration-heavy system for long-lived agent workflows". Two recent platform hires named (John and "Zeckey/Mobean", unclear in Wispr). They are also hiring dedicated DevOps and a Director of DevOps.
- **Stack:** Python, Java, TypeScript (front end); **all three clouds, mostly GCP**; Kubernetes, Terraform, GitLab; "AI coding is heavy but you still bring judgment".
- **Leveling:** what moves someone from senior to staff or senior staff is **impact and architecture input into decisions and roadmaps**, reusable work, not just executing for one team.

### 32:00–38:00 — Company facts
- Trase went from about **10 to about 70** people since Adam started at Red Cell (Jan 2025); about **45 engineers** (he has hired more than half); they plan **about 30 new roles** in the next weeks across engineering, product and sales; a new customer is driving forward-deployed work.
- **$107M seed** led by **ARCH Venture Partners**; the **CEO of Trase is the CEO of Red Cell ("Grant")**, who drove the fundraising through his relationships and is "not in the day-to-day". Red Cell's two big ecosystem plays are Trase (AI agent delivery mechanism) and Red Cell Federal.
- Ganesh: his earlier startup, **OceanX, about 55th employee, scaled to a little over 200**, so he has lived the "dividing into teams" phase. (Claims ledger: ~50–55th employee, later acquired; the "200" is new.)

### 38:00–41:00 — Immigration and remote
- **H-1B transfer: yes, Trase has done it.** Ganesh has had an **I-140 for 8 years**. National-security-flavored Red Cell companies may need citizens; **Trase is fine**; federal is separate.
- Ganesh asked about GovCloud-style lag (he compared Salesforce's government instance, slower releases); Adam agreed and added restrictions on federal models and AI tools. Both noted "deploy us last" customers.

### 41:00–48:00 — Process (practical, not studied for)
1. **Take-home project:** PDF instructions, submit a zip; **2 days, scoped 4–6 hours; AI coding expected ("100%")**; it builds orchestration-flavored work, not generic full-stack.
2. **Follow-up discussion** of the take-home: decisions, how AI was used, a new requirement. Adam has seen good projects whose authors "don't own the code".
3. **Three technicals (one engineer each, 1:1):** system design, technical deep dives (a project or two you owned; depth, trade-offs, consequences, how you learn).
4. **Two non-technicals** (30 minutes each, cross-functional) and the **hiring manager**.
- "Nothing you have to study for." Candidates can be redirected to another role mid-process (it has happened).
- **Send date:** Adam will schedule the project for **next Friday (Nov 6)**; not a hard timer; Ganesh may ask to move it earlier.
- Two **ex-Salesforce** engineers are at Trase ("Constantine"/Steve Const., developer productivity; "Alex"): a possible warm connection, not yet verified.

### 48:00–end — Compensation (Adam raised it unprompted)
- Posted band **$180–240K base**. **Staff realistically $220–240K** ("never hired a staff below $220K"); **senior $200–210K**.
- **Senior Staff (L7): about $240K to $260–270K base ("265 maybe")**; hard but "not impossible"; a few outside hires at that level and at **Principal** (above it, rare). **Trase levels map to Google levels.**
- **Target bonus 10%** (senior and staff), **15%** at senior staff and principal.
- **Equity is weak:** Red Cell holds a significant stake; peer group (Carta, $100–200M raised): senior equity about **50th percentile (just under), base about 90th percentile; staff equity about 35th**; "if equity is a significant part of your comp, don't bother".
- **Location:** remote, no offices (Red Cell office in McLean VA; a Trase Seattle-area office later, "a third of the team up there"). **Comp is benchmarked to Seattle/DC (95–96% of Bay Area; Bay Area and New York = 100%).** Adam raised a Bay Area benchmark with the engineering director (Lindsay) and head of people (Cameron): **"100% no" for now, unlikely before next year** (a new raise will reset the benchmark). He added: **"you should negotiate… advocate for yourself"**.
- 🔴 **Ganesh stated a number (against card #13):** "If the equity isn't significant, probably around **280, 300 base**… 10%." (Adam asked "is there a target?"; Adam joked "half a million in cash and I'd be happy".) Adam: "makes sense, that's pretty normal."

### Close
- Ganesh asked the right questions (remote status, equity, H-1B, process and role flexibility). He ended with interest ("definitely send the project") but **did not ask "where do you see gaps"**. Adam: not trying to trick him on the project; ask questions if any.

## Ganesh's own post-call notes (private; never into any outbound)
- Plans to **push for $300–320K base at senior staff**; wants Senior Staff / Principal-level treatment; sees it as a **stable, fully remote, SumoLogic-style loop** with low risk and low reward.
- Considers it a **safety-net offer** ("get an offer here"), and noted he could, if he spent extended time in India, keep this job as a back burner while finding a better one (flagged in chat: H-1B and the employer's remote-work terms need checking first).
- Sees Adam as a **long-term network contact** (incubators, VC, funding for a future startup of his own) and wants everything in the repository for future searches and networking. See the [contacts record](https://github.com/discordcreater/job-search-2026/blob/main/contacts/staging/20261028-1500-adam-elconin-red-cell.jsonl).

## Next steps
1. **Adam sends the take-home on Fri Nov 6** (Ganesh can ask for it earlier). Due about Sun Nov 8 to Mon Nov 9, no hard clock. [OPEN_LOOPS](https://github.com/discordcreater/job-search-2026/blob/main/OPEN_LOOPS.md).
2. **Post-call note to Adam:** one short thank-you, plus confirm Friday; no comp, no other processes.
3. Prep: [take-home plan](https://github.com/discordcreater/job-search-2026/blob/main/interviews/trase-interview-process.md#take-home-plan).

## Audio cross-check (Whisper small vs Wispr)
- Audio about 52 minutes; Whisper 8,849 words vs Wispr's longer, cleaner text. Substance matches throughout (comp bands, process, org facts).
- Conflicts (Wispr wins): OceanX **"55th employee" (Wispr) vs "50th" (audio)**, so the ledger's "~50th–55th" stands; Ganesh's base target is audible as "280, 300 base... a 10% bonus" in both; Adam's "half a million in cash" joke is in both.
- The audio does not name Adam either; identity rests on the calendar invite and the Oct 22 thread.
