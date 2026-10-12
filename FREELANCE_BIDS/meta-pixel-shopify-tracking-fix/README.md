# Meta Pixel + Meta Ads + Shopify: Conversion & Tracking Fix (PoC)

Freelancer job: "Meta Pixel + Meta Ads + Shopify Expert - Conversion & Tracking Fix"
($30-250, posted 2026-10-12). The client's Meta Pixel stopped firing on a
Shopify store; they want audit, clean reinstall/dedup, and a validation period
with screenshots.

This folder holds the diagnostic half of that job, working and tested.

## Run

    python PoC.py --self-test
    python PoC.py https://your-store.com
    python PoC.py https://your-store.com --pages 2 -o report.json

Stdlib only (Python 3.8+), no browser needed. Exit 0 = audit done, 2 = nothing
fetchable (bot wall), 1 = usage error.

## What it proves

- `--self-test`: known-good page passes every check; the "pixel dropped during
  theme migration" page (the exact symptom in the job post) is flagged
  MISSING/UNDETERMINED on every line; an empty page fails closed.
- Live runs against `shops-22.myshopify.com` (Shopify's demo store, no pixel
  configured) and `www.kith.com` (live Shopify storefront) are saved as
  `demo_audit_*.json` as sample outputs for the client.

## Honest scope

Static audit of the served HTML: pixel presence, init + ID, standard events
(ViewContent, AddToCart, InitiateCheckout, Purchase), Conversion API / dedup
wiring, GTM vs direct delivery. Runtime-only failures (JS errors, ad blockers,
CAPI send failures) report UNDETERMINED and point to Events Manager real-time
debug as the next step. The fix work itself (channel app config, GTM wiring,
real-order validation window) happens on the client's store after the audit
pins down the failure.

See BID.md for the client-facing proposal (human, first person, no AI mention).
