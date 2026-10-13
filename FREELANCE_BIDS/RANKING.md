# Freelance Job RANKING (all jobs in FREELANCE_JOBS.md, as of 2026-10-13)

Verdicts per job, using only what the job itself states (scope clarity,
budget, time left, skills, bid count). Full-time / salary posts are WATCH
for Jake (not gig bids). "READY" = PoC verified + bid written, saved for
Jake to submit (no Freelancer.com account exists yet; see CREDENTIALS.md).

## READY this run (2026-10-13, new)
- **BID** Junior Python LLM Bot Developer ($72 avg, 0 bids) -- squarely in
  lane (Python, automation, online + offline model routing). Built and ran
  the core: one LLMProvider interface, online (chat-completions wire
  format, key from env) + offline (deterministic intent engine), a 5-intent
  task router, guardrails (length cap, blocked-topic refusal, confidence,
  trace id), and a 24-check selftest that verifies the online wire format
  against an in-process mock. Found and fixed one real routing bug (ship vs
  shipping) during selftest. All 24 pass, exit 0. Budget is junior-level so
  the bid scopes to 5-10 tasks + a paid 1-day de-risk rather than inflating.
  READY.
- **BID** Physical Goods Stock Automation System ($250-750 AUD, 25 proposals,
  ends in 6 days) -- clear 3-workflow scope, mainstream stack, both routing
  paths (API + email) already proven in PoC (34/34 checks). READY.
- **BID** WhatsApp Auto-Responder Keyword System -- 2 ($250-750 USD, 168
  proposals, ends in 4 days) -- precise, testable acceptance criterion
  (reply within 2s, no restart) and it is measured in the PoC (21/21
  checks). 168 bidders is real competition; the verified-code angle is the
  differentiator. READY.

## READY in prior runs (bids saved for Jake)
- **BID** AI Web App Completion Agent Creation ($452 avg) -- READY, 10-12.
- **BID** Meta API & Stripe Escrow Integration ($1440 avg, AUD range) --
  READY, 10-12, strongest fit of the batch.
- **BID** Grocery Price Scraper Creation (Coles/Woolworths, $302 avg) --
  READY, 10-12.
- **BID** Matrimony Website Development ($268 avg) -- READY, 10-12 (PoC.js).
- **BID** One-Time Website Data Scrape ($144 avg) -- READY, 10-12.
- **BID** Automated Data-Extraction & Website Integration Developer ($728
  avg) -- READY, 10-12.
- **BID** Website Phone Number Scraper ($209 avg) -- READY, 10-12.
- **BID** Needoh Stock Watchlist Builder ($17 avg) -- READY, 10-12 (small,
  but proven).
- **BID** Wayfair Closeout Scraping System ($213 avg) -- READY, 10-12
  (honest note: page is client-rendered, Playwright variant required).
- **BID** MERN Real-Time Communication App ($253 avg) -- READY, 10-12
  (PoC.js, live socket layer).
- **BID** Honeywell Thermostat Batch Integration ($426 avg) -- READY, 10-12.

## WATCH (not bid this run)
- **WATCH** Responsive Web App Development ($273 avg) -- scope is a
  checklist of "modern, responsive, fast, SEO, admin, API, deployment";
  nothing specific to build against. Would become BID-able if the client
  scopes it; otherwise it is a generic build I'd be guessing at.
- **WATCH** WhatsApp & Messenger Reply Automation ($1037 avg) -- good
  budget and my lane, but it asks for AI-generated answers + a knowledge
  base on top of the keyword engine (bigger than the proven PoC). Worth a
  look if it stays open and unclaimed; flag to Jake.
- **WATCH** Crypto Trading Bot: Strategy Developer ($2017 avg) -- technically
  in-lane (Python/React backtesting), but the scope is strategy research on
  a running bot; needs a scoping conversation before a bid is honest.
  Flag to Jake (also touches the trading project lane).
- **WATCH** Python Developer: Multi-User Scraping & Lead-Gen Platform ($172
  avg) -- Django/Celery/Postgres multi-user platform is a real build;
  doable but a larger engagement than a one-week gig and the budget reads
  low for it.
- **WATCH** Toters App Data Scraping ($135 avg) -- mobile app, not a public
  web page; would need the app's API or device, not clean HTTP scraping.
  Out of reach without access the job does not provide.
- **WATCH** IT Engineers for E-commerce Automation (Japan) ($1030 avg) --
  this is an employment post ("we're hiring IT engineers to join our team"),
  not a gig bid. Salary-style; not a Freelancer bid target.

## SKIP (out of lane or out of principles)
- **SKIP** SaaS Document Automation Platform ($462 avg) -- InDesign + Azure
  SaaS build, far beyond a one-week gig; wrong stack for me.
- **SKIP** Automated OSINT Due-Diligence Tool ($227 avg) -- background
  reports on named individuals (PAN etc.); I don't take that kind of work.
- **SKIP** Codex Integration Training for Website ($42 avg) -- a
  tutoring/consulting ask, not a build; budget is below the effort.
- **SKIP** Email Verifier Software Development ($134 avg) -- asks for
  fingerprint spoofing + SOCKS5 to check registration on a site; that is
  ToS-violating automation, not a job I take.
- **SKIP** Quotex Real-Time Binary Price Feed ($162 avg) -- real-time
  scraping of a binary-options platform without its API; gray market and
  ToS-violating, and the client wants "no throttling" i.e. at the platform's
  expense.
- **SKIP** AI Agent Developer - Multi-Outlook Automation ($207 avg) -- the
  post demands demonstrated AI-agent track record; I don't claim AI work I
  haven't done for a client, and Outlook COM automation is off-platform.

## Run log
- 2026-10-13 03:33 CT (cron): monitor re-fired on the same 6-job set
  (hf_new=6, sig unchanged in content; seen-list reset by a scanner
  re-run). All 6 already have verified PoCs + bids from earlier runs
  today; no new job added, nothing to re-bid. Cleaned a stray
  selftest_out/ artifact from the worklog clone. Bids remain saved
  for Jake (no Freelancer.com account in CREDENTIALS.md).

## Process notes
- Bid discipline: BID only where scope is clear AND a working PoC is
  verified this run or in a prior one; everything else is WATCH (Jake
  decides) or SKIP (with a reason).
- Bids are saved, not sent: no Freelancer.com account exists (Contra
  signup is PARTIAL, blocked at the password step; details in
  CREDENTIALS.md). Jake submits or completes a signup to unlock live bids.
- Competition check (live pages, 2026-10-13): stock-automation = 25
  proposals; keyword-responder = 168 proposals at $437 avg. Both remain
  free-to-bid; verified-code pitches are the differentiator.
