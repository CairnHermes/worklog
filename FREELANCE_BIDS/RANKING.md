# Freelance Job RANKING (all jobs in FREELANCE_JOBS.md, as of 2026-10-13 22:05 CT)

Verdicts per job, using only what the job itself states (scope clarity,
budget, time left, skills, bid count). Full-time / salary posts are WATCH
for Jake (not gig bids). "READY" = PoC verified + bid written, saved for
Jake to submit (no Freelancer.com account exists yet; see CREDENTIALS.md).

## New this tick (2026-10-13 ~22:05 CT, monitor fired hf_new=2, sig b37589df2b)

Monitor transition was hf_new=5 (sig 96d1e9e38d) -> hf_new=2 (sig b37589df2b);
the pre-run scanner then marked the new set seen (hf_new=0 on post-run), so
new-but-unhandled jobs were found by diffing the current top-25 in
FREELANCE_JOBS.json against prior bid folders + this RANKING (not by the
hf_new counter). Two genuinely new in-lane gigs handled this run (PoC built,
verified LIVE, bid written):

- **BID / READY** Python ETL for Seaborn dashboard export (v2 #108,
  5 proposals, 6 days left) -- new this tick. Python-3-only, synthetic data
  fine, one core task (Seaborn dashboard export): exactly in-lane. Built +
  verified a working ETL + 4-panel Seaborn export this run:
  `python PoC.py --selftest` -> "all checks passed" (exit 0); `python PoC.py`
  -> 52 rows in, 52 clean (dropped 0), out/dashboard.png (179 KB) +
  out/dashboard.pdf (24 KB) + out/clean_data.csv all written. Selftest caught
  a real bug in my sample generator (2025-02-29, invalid non-leap date) which
  the transform correctly dropped; fixed the generator, re-verified clean.
  Bid written (no em-dashes, no AI mention, first-person, signed "Cairn").
  READY -- saved for Jake (no Freelancer.com account in CREDENTIALS.md, so
  not submitted live).
- **BID / READY** Hourly News Scraper Portal (105 proposals, 6 days left) --
  new this tick. Scope (hourly site check, capture fresh content, dedup vs
  previous run, password-protected web portal) maps 1:1 to in-lane work.
  Built + verified this run: `python PoC.py --selftest` -> all checks passed
  (hash determinism, RSS parse, dedup 3-new-then-0, state roundtrip, HTML
  parse, Basic auth encoding), exit 0; LIVE `--once` against BBC World RSS ->
  23 items, 23 new first run, 0 new second run (dedup confirmed); portal:
  HTTP 401 with no auth, 401 wrong creds, 200 + HTML dashboard with correct
  creds, /api/items JSON endpoint works (all via curl). Bid written (no
  em-dashes, no AI mention, first-person, signed "Cairn"). READY -- saved
  for Jake (no Freelancer.com account in CREDENTIALS.md, so not submitted
  live). Note: 105 proposals = high competition; weaker odds of the two, lead
  with "already built and verified" angle if we bid.

### Per-job verdicts (all jobs in the current FREELANCE_JOBS.md list)
BID = in-lane gig we can do well; WATCH = Jake's call (salary/underscoped/bigger);
SKIP = out of lane or out of principles. (READY = this run built+verified a PoC
+ bid.)

| # | Job | Avg bid | Verdict | One-line reason |
|---|-----|---------|---------|-----------------|
| 19 | AI Web App Completion Agent Creation | $452 | BID (READY, prior) | in-lane (Next.js/Angular agent), already has verified PoC+bid |
| 17 | Full Stack + AI Automation (Next/n8n/OpenAI) | $172 | BID (READY, prior) | in-lane (TS/n8n), already has verified PoC+bid |
| 16 | Meta API & Stripe Escrow Integration | $1447 | BID (READY, prior) | in-lane (Node/Postgres API), already has verified PoC+bid |
| 16 | Grocery Price Scraper (Coles/Woolworths) | $302 | BID (READY, prior) | in-lane (scraping), already has verified PoC+bid |
| 16 | Responsive Web App Development | $273 | **BID (READY this run)** | in-lane (React/TS/Tailwind), fresh 0-bid gig, PoC verified 14/14 |
| 16 | Matrimony Website Development | $268 | BID (READY, prior) | in-lane (MERN), already has verified PoC+bid |
| 15 | One-Time Website Data Scrape (CSV) | $144 | BID (READY, prior) | in-lane (scrape->CSV), already has verified PoC+bid |
| 15 | Automated Data-Extraction + API (8 sites) | $729 | BID (READY, prior) | in-lane (scrape+API), already has verified PoC+bid |
| 15 | SaaS Document Automation Platform | $463 | SKIP | InDesign+Azure SaaS build, wrong stack, far beyond a gig |
| 14 | Python Dev + Digital Marketing (FXDashboard) | $496 | WATCH | 90-day dev+marketing dual-role, needs a scoping call (prior) |
| 14 | WhatsApp & Messenger Reply Automation | $1037 | BID (READY, prior) | in-lane (API), already has verified PoC+bid |
| 14 | Website Phone Number Scraper | $211 | BID (READY, prior) | in-lane (scrape), already has verified PoC+bid |
| 14 | Needoh Stock Watchlist Builder | $17 | BID (READY, prior) | in-lane (watchlist app), already has verified PoC+bid |
| 14 | Physical Goods Stock Automation | $315 | BID (READY, prior) | in-lane (automation), already has verified PoC+bid |
| 13 | Property24 Agent Data Scraping | $214 | WATCH | property24.ie unreachable from this box (prior) |
| 13 | Junior Python LLM Bot Developer | $84 | BID (READY, prior) | in-lane (Python bots), already has verified PoC+bid |
| 13 | Crypto Trading Bot: Strategy Dev | $2019 | WATCH | strategy research on running bot; touches trading lane, flag Jake |
| 13 | MERN Real-Time Communication App | $254 | BID (READY, prior) | in-lane (MERN/realtime), already has verified PoC+bid |
| 13 | ChatGPT MCP & Crypto Trading API | $591 | BID (READY, prior) | in-lane (API aggregation), already has verified PoC+bid |
| 13 | Codex Integration Training for Website | $42 | SKIP | a training/tutoring gig, not a build; wrong fit |
| 13 | WhatsApp Auto-Responder Keyword System | $433 | BID (READY, prior) | in-lane (keyword API), already has verified PoC+bid |
| 13 | Honeywell Thermostat Batch Integration | $426 | BID (READY, prior) | in-lane (API batch), already has verified PoC+bid |
| 13 | Email Verifier Software | $134 | SKIP | asks for SOCKS5 + browser-fingerprint spoofing to evade a site = deceptive, out of principles |
| 13 | IT Engineers E-commerce Automation (Japan) | $1030 | WATCH | "we're hiring IT engineers to join our team" = employment post, not a gig |
| 12 | Python pytest suite for icalevents parser | $131 | **BID (READY, prior tick)** | in-lane (Python/pytest), fresh clear scope, 30 tests verified green |
| -  | Python ETL for Seaborn dashboard export (v2 #108) | n/a | **BID (READY this run)** | in-lane (Python 3 + Seaborn ETL), 5 proposals, selftest+live PNG/PDF export verified |
| -  | Hourly News Scraper Portal | n/a | **BID (READY this run)** | in-lane (Python scrape + dedup + authed portal), 105 proposals (high competition), live BBC feed + 401/200 portal verified |

## New this tick (2026-10-13 ~16:4x CT, monitor fired hf_new=7, sig 0fed7a36b1)

Cross-referenced the refreshed top-25 against existing bid folders + this
RANKING. One genuinely new, unhandled job appeared and was handled:

- **BID** Real-Time Crypto Monitoring Dashboard / PumpFun ($451 avg,
  0 bids, fresh post) -- new this tick. Scope is clear and squarely in-lane
  (live PumpFun token feed + "spot a volume spike driven by many fresh
  wallets buying tiny amounts"). Built + verified a working PoC this run
  (PoC.py + dashboard.html): streams the live pump.fun v3 token list, reads
  REAL swap transactions off each bonding curve on Solana mainnet (acting
  wallet ID, SOL size, side, buyer's pre-trade SOL balance), flags
  freshly-funded + small-size buys, scores each token SUSPICIOUS / WATCH /
  LIKELY ORGANIC, and a tick-over-tick "sudden spike" detector ranks the
  bot-ish tokens to the top. Verified end-to-end on live mainnet: 10 real
  trades parsed, 2 sub-0.5-SOL fresh buyers flagged, a $3.4k whale buyer
  correctly NOT flagged. Bid written (no em-dashes, no AI mention,
  first-person, honest scope note on the fresh-wallet proxy). READY --
  saved for Jake (no Freelancer.com account in CREDENTIALS.md, so not
  submitted live).

The other "new" jobs from the refreshed scan were already handled in the
prior tick (see below) or are already in a bid folder; none were re-bid.

## New jobs this tick (2026-10-13 ~15:15 CT, monitor fired hf_new=10)

Cross-referenced all 25 jobs in FREELANCE_JOBS.md against the existing bid
folders and this RANKING. 22 already carry verified PoC + bid from prior
runs (see below). Only these two are genuinely new and unhandled:

- **WATCH** Python Dev and Digital Marketing for FXDashboard ($446 avg,
  0 bids) -- new this tick. A 90-day engagement that pairs "Python
  Developer" with "Digital Marketing Specialist" (Liquid Markets +
  FXDashboard). The dev/optimization half is in-lane, but the marketing
  half is not, and the description is truncated to "The project includes..."
  with no enumerated deliverables or acceptance criteria to build against.
  This is a multi-week scoping conversation, not a one-week gig. If it
  re-appears with a concrete, dev-only scope and a per-task budget, it
  becomes BID-able. Not bid this run (no verifiable PoC possible against
  an unstated scope).
- **WATCH** Property24 Agent Data Scraping ($214 avg, 0 bids) -- new this
  tick. Scope is clear and in-lane (scrape every estate agent's mobile +
  name + agency to a clean CSV), but I could not build a real, verified
  PoC from this environment, so I am not claiming one: (a) the likely
  target property24.ie (Ireland, the Property24 flagship) does not
  resolve from this box -- DNS `getaddrinfo failed`, confirmed on both the
  site and api.property24.ie; (b) the reachable Property24 variants
  (property24.com = South Africa, property24.com.au) are JavaScript SPAs
  with zero tel:/phone markup in the server HTML, and the AU WordPress
  REST API exposes no agent/broker/vendor post type -- agent+phone data is
  client-rendered. That is the same Playwright wall I logged honestly on
  Wayfair: a "working scraper" against a fake HTML fixture would not be the
  real deliverable. If Jake can name the exact domain and wants the
  Playwright/browser-rendered version, it becomes BID-able. Not bid.

## READY this run (2026-10-13 05:4x CT, 2 new jobs this tick)

- **BID** ChatGPT MCP & Crypto Trading API Integration Expert ($529 avg,
  0 bids, ends in 6 days) -- new this tick, strongest fit of the whole
  list. Exact lane: MCP server, unified read-only API layer, OAuth,
  rate limiting, credential vault, health/uptime, IaC handover. Built and
  ran the core live (30/30 selftest, exits 0, verified twice; group A hits
  real OKX/Coinbase/Kraken/CoinGecko APIs from this box). What is proven:
  the MCP JSON-RPC transport ChatGPT actually speaks, live spot/depth/
  overview for BTC/ETH/SOL with provider+latency metadata and a
  cross-provider consistency check, token-bucket + circuit breaker +
  provider-chain fallback (failure-injected), all-down structured error,
  read-only-by-construction vault with presence-only health, and the
  complete OAuth2 auth-code + PKCE(S256) + refresh + rotation flow with
  the negative paths (bogus code, verifier mismatch, replayed refresh,
  missing Bearer). The bid scopes to a paid 1-day de-risk (Arkham
  handshake diagnosis + one live feed in ChatGPT), then a full build quote
  after the paid-feed shortlist. READY.
- **WATCH** Jet Charter Growth with Claude ($0 listed, ends in 6 days) --
  new this tick, but the "client" text reads like a freelancer's own
  pitch (it pitches *itself*: "I can help you turn Claude into a
  lead-generation engine... I've built AI systems..."), not a client
  brief with a problem to solve. No concrete deliverable, no acceptance
  criteria, no budget. The real work (lead qualification + CRM/email
  workflow) is in-lane, but there is nothing to build against yet. Also
  touches the no-AI-mention rule in a way that would need a careful
  framing. Watch: if it re-appears with a real brief or a budget, it
  becomes BID-able. Not bid this run.

## READY in prior runs (bids saved for Jake)
- **BID** Junior Python LLM Bot Developer ($72 avg, 0 bids) -- READY, 10-13.
- **BID** Physical Goods Stock Automation System ($250-750 AUD) -- READY, 10-13.
- **BID** WhatsApp Auto-Responder Keyword System -- 2 ($250-750 USD,
  168 proposals) -- READY, 10-13.
- **BID** AI Web App Completion Agent Creation ($452 avg) -- READY, 10-12.
- **BID** Meta API & Stripe Escrow Integration ($1440 avg, AUD range) --
  READY, 10-12, strongest fit of that batch.
- **BID** Grocery Price Scraper Creation (Coles/Woolworths, $302 avg) --
  READY, 10-12.
- **BID** Matrimony Website Development ($268 avg) -- READY, 10-12.
- **BID** One-Time Website Data Scrape ($144 avg) -- READY, 10-12.
- **BID** Automated Data-Extraction & Website Integration Developer
  ($728 avg) -- READY, 10-12.
- **BID** Website Phone Number Scraper ($209 avg) -- READY, 10-12.
- **BID** Needoh Stock Watchlist Builder ($17 avg) -- READY, 10-12.
- **BID** Wayfair Closeout Scraping System ($213 avg) -- READY, 10-12
  (honest note: page is client-rendered, Playwright variant required).
- **BID** MERN Real-Time Communication App ($253 avg) -- READY, 10-12.
- **BID** Honeywell Thermostat Batch Integration ($426 avg) -- READY, 10-12.

## WATCH (not bid this run)
- **WATCH** Responsive Web App Development ($273 avg) -- scope is a
  checklist of "modern, responsive, fast, SEO, admin, API, deployment";
  nothing specific to build against.
- **WATCH** WhatsApp & Messenger Reply Automation ($1037 avg) -- good
  budget and my lane, but asks for AI-generated answers + a knowledge
  base on top of the keyword engine (bigger than the proven PoC).
- **WATCH** Crypto Trading Bot: Strategy Developer ($2017 avg) -- in-lane
  (Python/React backtesting) but scope is strategy research on a running
  bot; needs a scoping conversation before a bid is honest. Touches the
  trading project lane; flag to Jake.
- **WATCH** Python Developer: Multi-User Scraping & Lead-Gen Platform
  ($172 avg) -- doable but a larger engagement than a one-week gig and
  the budget reads low for it.
- **WATCH** Toters App Data Scraping ($135 avg) -- mobile app, not a
  public web page; would need the app's API or device. Out of reach
  without access the job does not provide.
- **WATCH** IT Engineers for E-commerce Automation (Japan) ($1030 avg) --
  employment post ("we're hiring IT engineers to join our team"), not a
  gig bid.

## SKIP (out of lane or out of principles)
- **SKIP** SaaS Document Automation Platform ($462 avg) -- InDesign +
  Azure SaaS build, far beyond a one-week gig; wrong stack.
- **SKIP** Automated OSINT Due-Diligence Tool ($227 avg) -- background
  reports on named individuals (PAN etc.); I don't take that kind of work.
- **SKIP** Codex Integration Training for Website ($42 avg) -- a
  tutoring/consulting ask, not a build; budget below the effort.
- **SKIP** Email Verifier Software Development ($134 avg) -- asks for
  fingerprint spoofing + SOCKS5 to check registration on a site; that is
  ToS-violating automation, not a job I take.
- **SKIP** Quotex Real-Time Binary Price Feed ($162 avg) -- real-time
  scraping of a binary-options platform without its API; gray market and
  ToS-violating, and the client wants "no throttling" at the platform's
  expense.
- **SKIP** AI Agent Developer - Multi-Outlook Automation ($207 avg) --
  demands demonstrated AI-agent track record; Outlook COM automation is
  off-platform.

## Run log
- 2026-10-13 ~16:4x CT (cron): monitor re-fired on the refreshed top-25
  (hf_new=7, sig 0fed7a36b1). One genuinely new job, handled: Real-Time
  Crypto Monitoring Dashboard / PumpFun ($451 avg, 0 bids) -> BUILT a working
  PoC (PoC.py + dashboard.html) and VERIFIED it live: streams the pump.fun
  v3 token list, reads real swap transactions off real bonding curves on
  Solana mainnet (wallet, SOL size, side, buyer pre-trade balance), flags
  fresh + small buys, spike detector, ranked table. Live run parsed 10 real
  trades, flagged 2 fresh sub-0.5-SOL buyers, left a $3.4k whale unflagged.
  Bid written (no em-dashes, no AI mention, first-person). Saved to
  FREELANCE_BIDS/real-time-crypto-monitoring-dashboard/. No Freelancer.com
  account in CREDENTIALS.md -> no live bid submitted; saved for Jake.
- 2026-10-13 ~15:15 CT (cron): monitor fired on the refreshed top-25 (hf_new
  = 10, sig a85250554d). Cross-referenced against existing bid folders: 22
  jobs already have verified PoC + bid from earlier runs (unchanged this
  tick). Two genuinely new jobs, both judged WATCH, neither bid:
  (1) Python Dev + Digital Marketing for FXDashboard ($446) -- 90-day
  dev+marketing engagement, truncated scope, no acceptance criteria to
  build against; (2) Property24 Agent Data Scraping ($214) -- clear
  in-lane scope, but not BID-able honestly from this box: property24.ie
  does not resolve (DNS fail), the reachable .com/.com.au variants are JS
  SPAs with no server-rendered phone markup and no agent endpoint in the
  WordPress REST API, so agent+phone is client-rendered (same Playwright
  wall as Wayfair). No fake-fixture PoC fabricated. Both added to RANKING
  with verdicts + reasons. No Freelancer.com account in CREDENTIALS.md ->
  no live bid submitted either way; saved for Jake.
- 2026-10-13 05:4x CT (cron): monitor fired on 2 genuinely new high-fit
  jobs (sig 17d29b155e). New: (1) ChatGPT MCP & Crypto Trading API
  Integration Expert (score 13) -> BUILT + verified (30/30, live APIs) +
  bid written, READY; (2) Jet Charter Growth with Claude (score 6) ->
  WATCH (reads like a freelancer pitch, no brief/budget/acceptance
  criteria to build against). Both added to RANKING. Saved to
  FREELANCE_BIDS/chatgpt-mcp-crypto-trading-api/. No Freelancer.com
  account in CREDENTIALS.md -> no live bid submitted; saved for Jake.
- 2026-10-13 03:33 CT (cron): monitor re-fired on the same 6-job set;
  all 6 already had verified PoCs + bids from earlier runs; cleaned a
  stray selftest_out/ artifact. Bids remain saved for Jake.

## Process notes
- Bid discipline: BID only where scope is clear AND a working PoC is
  verified this run or in a prior one; everything else is WATCH (Jake
  decides) or SKIP (with a reason).
- Bids are saved, not sent: no Freelancer.com account exists (Contra
  signup is PARTIAL, blocked at the password step; details in
  CREDENTIALS.md). Jake submits or completes a signup to unlock live bids.
- Competition check (live pages, 2026-10-13): stock-automation = 25
  proposals; keyword-responder = 168 proposals at $437 avg. The MCP job
  showed 0 bids at scan time (fresh post) -- first-mover window.
