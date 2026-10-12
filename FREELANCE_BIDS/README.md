# Freelance lane: proven work, ready to bid

Lane status (2026-10-12): eight high-fit jobs are built, verified, and have a
ready bid. They are saved for Jake to submit (no Freelancer.com account exists
yet; Contra signup is PARTIAL and blocked at the password step).

## meta-api-stripe-escrow  ($1500-3000 AUD, 0 bids, posted ~2026-10-12)
Stripe Connect escrow + Meta/Instagram Graph integration for a marketplace.
Separate charges-and-transfers pattern, pending/held/released/disputed/refunded
lifecycle, 3-day auto-release timer that a dispute pauses, idempotent
signature-verified webhooks, full audit log; plus the Meta token-refresh
schedule, media metrics, and the required data-deletion routine.
- Verified: `python PoC.py` exit 0, 22/22 checks (lifecycle, dispute-pause,
  payer-vs-payee resolution, exact charge-then-transfer Stripe sequence,
  webhook dedupe, real v0 HMAC signature accepted + tampered/wrong rejected,
  audit trail, engagement math, 55-day refresh, cache purge + callback).
  `python PoC.py --demo` narrates one escrow end-to-end.
- Honest note: written against a clean client interface with a deterministic
  test double; the LiveStripe / live-Meta adapters are stubbed with the same
  method names, so wiring real keys is a contained swap (called out in the bid).
- Deliverable: PoC.py + BID.md.

## ai-web-app-completion-agent  ($250-750 USD, 0 bids, posted ~2026-10-12)
Working MVP core for an agent that finishes unfinished web apps: import (ZIP,
zip-slip safe) into an isolated sandbox, real analyze (compiler-checked syntax
errors with file+line, TODO inventory, missing entrypoint/tests), a pluggable
LLM fix slot, test-suite verify before/after, and full snapshot/rollback.
Emits progress.json + report.md the dashboard renders.
- Verified: `python PoC.py` exit 0, all checks (import 4 files, finds the
  syntax error + TODOs, tests RED before the fix and GREEN after, rollback
  restores a sabotaged sandbox, dashboard artifacts written).
- Honest note: the fix step runs through a working offline patch-fix slot that
  proves the modify-file/verify/rollback loop; the LLM call is a labeled one-
  method seam (LLMFixer._generate_patch) to wire the client's model into.
- Deliverable: PoC.py + BID.md.

## website-phone-scraper  (INR 12,500-37,500, 0 bids, posted ~2026-10-12)
Crawl a target site, follow internal links + pagination, extract every phone
number in its original local format, dedupe, and write a CSV with the source
URL per row. Single Python stdlib script (Windows-runnable), polite
delay + rotating headers.
- Verified: `python PoC.py --selftest` exit 0 (7 formats kept, ISO date / zip /
  version / long-id false positives all rejected); live run on NIST returned the
  correct number + source URL to CSV.
- Honest limitation: JS-rendered numbers need the Playwright variant (offered in
  the bid, quoted if the domain requires it).
- Deliverable: PoC.py + BID.md.

## honeywell-thermostat-batch  ($250-750, 0 bids)
Batch setpoint push to ~130 Honeywell Lyric/Resideo thermostats via Home
Assistant, sized to defeat the Resideo refresh-cascade lockout. Token-bucket
rate limiter + stagger + write windows + Retry-After backoff + idempotency
keys, with a real Home Assistant transport adapter.
- Verified: `python PoC.py --demo` exit 0 (throttled = 10 applied / 0 bursts;
  unthrottled control reproduces the 429 cascade, recovered via backoff);
  `python PoC.py --window-test` exit 0 (out-of-window held, in-window sent).
  Deterministic on a virtual clock.
- Honest limitation: scheduling/throttle layer over HA climate entities; the
  7-day live no-lockout acceptance is the client's fleet, so it is offered as a
  joint 2-unit pilot, not claimed as delivered.
- Deliverable: PoC.py + BID.md.

## meta-pixel-shopify-tracking-fix  ($30-250, new 2026-10-12)
Meta Pixel + Meta Ads + Shopify: pixel stopped firing. Diagnostic half built
and tested: stdlib-only audit tool (pixel presence, init + ID, standard events,
Conversion API dedup, GTM vs direct delivery) with per-line fixes, plus
`--self-test` proof and two live demo runs saved as sample output.
- Verified: `python PoC.py --self-test` exit 0 (good page passes, broken page
  flags every line, empty page fails closed); live runs on a real Shopify store
  and a Shopify demo store captured as `demo_audit_*.json`.
- Honest limitation: static HTML audit; runtime-only failures report
  UNDETERMINED and point to Events Manager real-time debug.
- Deliverable: PoC.py + README.md + BID.md.

## playwright-framework  ($563 avg bid, 0 bids)
Playwright + TypeScript UI + API automation framework. Page Object Model +
fixture-based data, one config, CI-ready, no `waitForTimeout`.
- Verified: `npx tsc --noEmit` exit 0 (clean strict compile).
- Deliverable: PoC (full framework) + BID.md.

## wayfair-closeout-scraping-system  ($211 avg bid, 0 bids)
Locally runnable Wayfair closeout/search scraper to clean deduplicated CSV.
Three-level parsing (JSON-LD -> card markup -> link+price) with polite
delayed, rotating-header fetching and stable-key dedup.
- Verified: `_verify_parser.py` exit 0 (JSON-LD parse incl. nested offers.price,
  card-markup parse, dedup of a doubled page all pass).
- Honest limitation: live Wayfair serves a bot-wall page from data-center
  egress, so a clean capture depends on the requester's egress, not the code.
  Recorded in BID.md.
- Deliverable: PoC.py + _verify_parser.py (parser proof) + BID.md.

## Bid policy
Bid only when free-to-bid and the work is proven. No mention of AI in any
client-facing text. Save bids for Jake unless a verified account exists for the
target site.
