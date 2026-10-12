# Freelance lane: proven work, ready to bid

Lane status (2026-10-12): three high-fit jobs are built, verified, and have a
ready bid. They are saved for Jake to submit (no Freelancer.com account exists
yet; Contra signup is PARTIAL and blocked at the password step).

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
