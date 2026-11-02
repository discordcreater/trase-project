# Red Cell Partners / Trase.ai: exploratory chat prep (Adam Elconin), Wed Oct 28 2026, 11:30 AM-12:00 PM PT

<!-- COACH-CARD-COPY-START -->
### 🎓 Coach's card: read this before EVERY call (updated Oct 27 2026 after the Apple AIML Data Ops HM round; previous: PANW Cortex recruiter screen, Oct 26 Archer HM, EarnIn)

1. **Intro in 75 seconds, three parts (even when the interviewer lets you run: Archer's HM overview went 18.6 minutes):** who you are in one line; one result per company, with the exact number; then "what I'm looking for is ___, which is why this role." Never skip the third part.
2. **Link to AI whenever the role touches AI:** "I architected the backend runtime for Agentforce workflow execution, and an on-call bot that retrieves, answers, files tickets and pages."
3. **Pick a story and go.** Never open with "I don't have one project" or "a small initiative." Say: "The best example is ___."
4. **Shape every story:** one-sentence situation, what you decided, how you aligned people, the number, one lesson. Stop after the lesson.
5. **Plain words, not Salesforce words.** Say "our main monolith", "our CI platform", "integration tests", "our own data centers" instead of core, SFCI, FIT, Hyperforce. Skip internal anecdotes. Never name a customer.
6. **Say numbers exactly as in the [claims ledger](https://github.com/discordcreater/job-search-2026/blob/main/knowledge/candidate-claims-ledger.md).** If unsure, round down ("over a hundred teams"); never round up.
7. **If asked "technically, what was hardest?", name three concrete problems,** not "experimentation" or "no textbook solution."
8. **Answer in 30–45 seconds, then offer more:** "I can go deeper on ___ if useful."
9. **Never volunteer:** a down-level (PMTS, "I took LMTS"), promotions, "slow" or "stagnating", why you are leaving, "family reasons", other interviews (Apple, Agentforce, Slack), or your current employer's problems. Say what you want next, not what happened. Say once: "I'm selective, and this one stood out."
10. **Do the arithmetic out loud** when given a scale ("300k installs times two heartbeats an hour is about 170 a second").
11. **End every call with three things, and the interviewer's name said correctly ("Thanks, Landon", not "Leonard"):** one sentence of interest, "what are the next steps and the timing?", and "where do you see gaps?" (you did the last one well on Scribe).
12. **Ask three questions about the work:** the biggest current failure mode, what the first 90 days look like, and who decides.
13. **On a recruiter call, hear the terms but don't name your number (rule since Oct 23).** Ask the level and the band. If they ask your expectation, say: "I'd like to understand the level and scope through the interviews first; I'm confident we'll land on a number that works." Give a number only if they say the next round depends on it, and then give the top of your range ($550–600K total). Never say "that works", "we can work with that number", "no target to beat" or "good for this opportunity" to their band or ceiling, and never read levels.fyi line items to a recruiter (PANW, Oct 27). Never "sounds okay" to a band, and never "haven't really given it thought" (Atlassian, Oct 27): say "Thanks, that's helpful; I'd like the loop to settle the level, and for that scope I'd expect to be toward the top, especially on equity." Never concede on-site days before you know the level and band.
14. **Keep every recruiter-call answer to 30–45 seconds** (a 75-second intro at most). Two sentences are what the recruiter will repeat to the hiring manager; give her those two.
15. **In a coding or refactoring round, keep the build green:** say the plan in one breath, then one small change → run all tests → next. Never comment out working code without its replacement in place. Intro and project story under 3 minutes total.
16. **Incident walkthrough = six beats with "I" in each, about 2 minutes:** detect (what signal, when), triage (what I isolated), decide (the call I made with incomplete information), mitigate, communicate (cadence, war room), prevent (what I built, and its measured effect). End on prevention. No process complaints.
17. **On any platform, DevOps or SRE seat, observability is a reflex answer (60 seconds), never a question back:** SLIs first (freshness from the event timestamp, queue/consumer lag, error and dead-letter rate, latency on the real-time path) with SLOs and burn-rate paging; then the stack (Prometheus + Grafana or CloudWatch, OpenTelemetry, a synthetic canary through the whole path); then Kubernetes signals (OOM kills, restarts, CPU throttling, pending pods, autoscaler at max). **Never disclaim Kubernetes: Cisco ran on EKS; you own Kubernetes IaC at Salesforce.** EKS *is* managed Kubernetes: say "AWS ran the control plane; I owned everything above it", never "not a managed instance" (EarnIn R0, P27).
18. **"Would you build it the same way?" = three concrete changes, each with the cost it fixes.** Cisco version: Iceberg on S3 instead of warehouse-centric storage; stream ingestion instead of per-file S3 notifications; a real-time OLAP store (ClickHouse/Pinot) for the 5-minute rollups; cost per device on the dashboard from day one. Use the interviewer's own word (e.g. "fidelity") and design for it.
19. **"Was it production?" → "Yes" first.** Internal users are production: "Yes, in production, used daily by our engineers", then one usage number. Never "this is not production" (EarnIn R0). On "code vs configure", lead with what you build (services, IaC, agent tooling); never say "approving things".
20. **Behavioural HM rounds: first sentence = "I ___" plus the outcome number; a known gap is said once, as a ramp plan, never as "will I still be considered?"** (Apple AIML R1, Oct 27).

More: [performance patterns](https://github.com/discordcreater/job-search-2026/blob/main/knowledge/performance-patterns.md) · [claims ledger](https://github.com/discordcreater/job-search-2026/blob/main/knowledge/candidate-claims-ledger.md)
<!-- COACH-CARD-COPY-END -->

**Remote (Zoom).** Role location for Trase.ai to confirm on the call. **Join:** https://redcellpartners.zoom.us/j/85191174082?pwd=pz7fbGOhasq9Td129jtbfObVdpSbha.1 · ID 851 9117 4082 · passcode 419946. Light call; **Apple SRE final round 1 is at 1:00 PM**, so end on time and keep your head free after.

## Who and why
Adam Elconin (Adam.Elconin@redcellpartners.com), recruiter for Red Cell Partners portfolio companies. Exploratory chat about **Trase.ai** (agentic platform/OS; recently closed a $100M+ seed per Adam). Source: [Adam's Zoom invite](https://mail.google.com/mail/#all/1a165ed7db53d95d) and the LinkedIn thread (Aug 10 outreach, Oct 21 reply, Oct 22).
**Levels (Adam):** Trase levels map to Google levels; Staff maps to Principal at Salesforce; Senior Staff is Architect; Principal is Principal Architect; getting above Staff is possible but challenging. Role he pointed to: https://job-boards.greenhouse.io/trase/jobs/5255658007 (he asked you to apply).

## Your 60-second opening (numbers exactly as the claims ledger)
"I'm a backend and data-platform engineer, 15 years in, tech lead for the last six. At Cisco I built the platform that more than a hundred teams ran on, 50+ PB per year, and cut the AWS bill by $1.35M a month. At Salesforce I lead the technical direction of the CI/CD platform and set up AI agents that review every pull request against our rules. I'm looking for hands-on principal-scope work on agentic infrastructure, which is why Trase caught my eye."

## Questions for Adam (pick four)
1. What does Trase actually build and what stage is the agentic platform at (customers, revenue, team size, hiring plan)?
2. How is the engineering org leveled, and where would my scope land (Staff vs Senior Staff vs Principal Architect)?
3. Remote or hybrid, and which hub? Does Trase sponsor H-1B transfers?
4. What is the interview loop and the timeline? Who is the hiring manager?
5. Which problems are hardest right now (reliability, evals, multi-tenant agent execution)?

## Rules for this call
- No comp number in writing; on a call use the deflection ("I'd like to understand the level and scope through the interviews first; I'm confident we'll land on a number that works").
- Don't mention other processes or Salesforce internals beyond scope; never call Salesforce "legacy" or "stagnating" (reframe: "I want more agentic scope").
- Confidence from specifics; no apology; if he asks about level say "Staff or Principal scope".
- Intel to gather: stage/funding facts, team, remote policy, H-1B stance.
