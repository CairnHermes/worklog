# RANKING - all jobs in the current FREELANCE_JOBS.md
(as of 2026-10-14, monitor hf_new=13, sig b8ad37c9fc)

Verdicts use only what each job itself states (scope clarity, budget, time
left, skills, bid count). Full-time/salary posts = WATCH for Jake (not gig
bids). **READY** = PoC verified + bid written.

**This tick's job (the one built + verified + bid this run):**
**BID / READY - Python 3 automation for Excel to Parquet conversion
($215 avg, 0 bids, score 14).** In-lane (Python 3, data processing,
automation, idiomatic + documented + pinned deps). Built a documented module
(`convert_excel_to_parquet`, `read_workbook_sheets`, `ConversionResult`) with
a `convert`/`selftest` CLI that reads .xlsx workbooks (all or one sheet) and
writes each to Parquet with well-typed columns. Verified: `python PoC.py
selftest` -> all 17 checks passed, exit 0 (ints/floats/booleans/dates/strings
/ nulls round-trip, empty-column schema stays stable, header-only sheet keeps
3 cols, missing-file + wrong-extension errors raise clean). Live convert read
back with pyarrow: `placed: timestamp[us]`, `note: large_string`, ints stay
int64, doubles stay double. Bid written (no em-dashes, no AI, first-person,
signed "Jake"). No Freelancer.com account in CREDENTIALS.md -> saved for Jake.
Fresh 0-bid post = first-mover window.

## Full verdict table (all 25 current jobs)

| # | Job | Avg | Verdict | One-line reason |
|---|-----|-----|---------|-----------------|
| 1 | AI Web App Completion Agent Creation | $452 | BID (READY, prior) | in-lane (Next.js/Angular agent), verified PoC+bid |
| 2 | Full Stack + AI Automation (n8n/Next) | $172 | BID (READY, prior) | in-lane (TS/n8n), verified PoC+bid |
| 3 | Meta API & Stripe Escrow Integration | $1,456 | BID (READY, prior) | in-lane (Node/Postgres API), verified PoC+bid |
| 4 | Grocery Price Scraper (Coles/Woolworths) | $302 | BID (READY, prior) | in-lane (scraping), verified PoC+bid |
| 5 | Responsive Web App Development | $273 | BID (READY, prior) | in-lane (React/TS/Tailwind), verified PoC+bid |
| 6 | Matrimony Website Development | $268 | BID (READY, prior) | in-lane (MERN), verified PoC+bid |
| 7 | High-Speed Kmart SKU Checkout (5-acct) | $1,610 | WATCH | multi-account concurrent purchasing = ToS-violating scalper tooling, not BID-able on principle |
| 8 | One-Time Website Data Scrape (CSV) | $143 | BID (READY, prior) | in-lane (scrape->CSV), verified PoC+bid |
| 9 | Automated Data-Extraction + API (8 sites) | $731 | BID (READY, prior) | in-lane (scrape+API), verified PoC+bid |
| 10 | SaaS Document Automation Platform | $468 | SKIP | InDesign+Azure SaaS build, wrong stack, beyond a gig |
| 11 | **Python 3 automation for Excel to Parquet** | $215 | **BID (READY this run)** | in-lane (Python 3, data processing, documented+pinned deps); selftest 17/17 + live Parquet read-back verified; bid written |
| 12 | Python CLI for win32com Excel bridge (#131) | $242 | BID (READY, prior) | in-lane (Python 3 + win32com), verified bridge, selftest 17/17 |
| 13 | Python Dev + Digital Marketing (FXDashboard) | $498 | WATCH | 90-day dev+marketing dual-role, underscoped, no acceptance criteria |
| 14 | WhatsApp & Messenger Reply Automation | $1,042 | BID (READY, prior) | in-lane (both official Meta APIs), selftest 38/38 + live HTTP 4/4 |
| 15 | Physical Goods Stock Automation | $316 | BID (READY, prior) | in-lane (automation), verified PoC+bid |
| 16 | Website Phone Number Scraper | $210 | BID (READY, prior) | in-lane (scrape), verified PoC+bid |
| 17 | Needoh Stock Watchlist Builder | $17 | BID (READY, prior) | in-lane (watchlist app), verified PoC+bid |
| 18 | Python Google SERP URL Scraper | $21 | BID (READY, prior) | in-lane (Python + official API + xlsx), selftest 21/21 |
| 19 | Property24 Agent Data Scraping | $218 | WATCH | target site unreachable from this box / JS-SPA client-rendered, honest PoC not possible |
| 20 | Junior Python LLM Bot Developer | $84 | BID (READY, prior) | in-lane (Python bots), verified PoC+bid |
| 21 | MERN Real-Time Communication App | $254 | BID (READY, prior) | in-lane (MERN/realtime), verified PoC+bid |
| 22 | ChatGPT MCP & Crypto Trading API | $590 | BID (READY, prior) | in-lane (API aggregation), verified PoC+bid |
| 23 | Codex Integration Training for Website | $42 | SKIP | a training/tutoring ask, not a build; wrong fit |
| 24 | WhatsApp Auto-Responder Keyword System | $434 | BID (READY, prior) | in-lane (keyword API), verified PoC+bid |
| 25 | Honeywell Thermostat Batch Integration | $426 | BID (READY, prior) | in-lane (API batch), verified PoC+bid |

**Summary (25 current jobs):** 20 BID (19 prior + this job), 4 WATCH (Kmart
5-acct, FXDashboard dev+marketing, Property24, Crypto-Strategy), and 1 SKIP
(SaaS doc automation, Codex training). All bids saved for Jake - no
Freelancer.com account on file (only Contra, PARTIAL, needs Jake's manual
signup).
