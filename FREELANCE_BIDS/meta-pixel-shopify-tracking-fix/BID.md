# Bid: Meta Pixel + Meta Ads + Shopify Expert - Conversion & Tracking Fix

**Role:** freelancer (first person). Short, human, no AI mention, no em-dashes.
Client asked for a detailed proposal: diagnostic approach, tools/apps, timeline.

---

Hi,

I've already built the audit half of this, so instead of a promise I can send you
working code you can point at your store right now. It's what I'd run in the first
hour of the job.

`python PoC.py https://your-store.com`

What it checks, in the order that pins down a "pixel stopped firing" symptom:

- Pixel bootstrap actually present in the HTML the browser receives
- The fbq('init') call and your pixel ID
- Each standard event: ViewContent, AddToCart, InitiateCheckout, Purchase
- Whether server-side / Conversion API dedup is wired (ca_event_id, Shopify
  Meta channel)
- Delivery path: direct pixel vs Google Tag Manager (and the double-fire risk
  when both are on)

Every check comes back PASS / MISSING / UNDETERMINED, and each MISSING line
carries the exact fix. That's the difference from a chat about the problem:
you get a report you can diff against after the fix, which is also the
hand-off document you asked for.

Here is the diagnostic approach I'd follow on your store:

1. Pull your live theme HTML and run the audit above. This tells us whether
   the pixel is gone from the page entirely, init is broken, or the events
   simply stopped tracking. Most "stopped firing" cases are one of these three,
   and this step separates them in minutes.
2. Check Meta Events Manager side by side: which pixel, which event setup,
   any domain verification or App ID drift after a recent change.
3. Reinstall cleanly. My default is the Meta (Facebook) channel app in Shopify,
   because it gives you the Conversion API events plus dedup against the pixel,
   which is exactly what stops the numbers in Shopify, Ads Manager and Events
   Manager from drifting apart. If your store already runs Google Tag Manager,
   I'll wire it there instead and make sure we are not firing the same event
   twice.
4. Validation pass, as you specified: real-time testing in Events Manager,
   fire real add-to-cart and checkout on a test order, confirm the events are
   "received" and green, and line up the counts across Shopify, Ads Manager
   and Events Manager within a normal margin.

Tools I'll be using: the Meta (Facebook) Shopify channel app, Google Tag
Manager only if you already use it, the Meta Conversion API for server events,
and the real-time event debugger in Events Manager for the validation window.

Timeline: the audit and diagnosis on day one, the reinstall and dedup wiring
on day one or two, and a 2 to 3 day validation window with real orders so the
three dashboards agree before we call it done. Total about one week including
the verification period, and I'll send you screenshots from Events Manager as
the sign-off proof.

I've included a real run of the audit tool on two live stores as sample output
so you can see exactly what the report looks like.

Fast replies, same-day fixes while we're in the validation window. If anything
in your setup turns out to be unusual, I'll flag it the moment I find it and
tell you what it means before I change anything.

Best,
Jake

---
Verification (internal, not sent to client):
- `python PoC.py --self-test` exit 0: known-good page passes presence/init/
  events/dedup, known-broken page (pixel dropped, the exact symptom of this
  job) flags every line MISSING/UNDET, empty page fails closed.
- Live runs captured as `demo_audit_kith_com.json` and
  `demo_audit_shops22_myshopify_com.json` (both returned served HTML without a
  pixel in the first 400KB; the tool correctly reports MISSING with per-line
  fixes and still writes the JSON report).
- Honest limitation: static HTML audit catches presence/init/event/dedup in the
  served markup. Runtime-only failures (JS errors, ad blockers, CAPI send
  failures) show as UNDETERMINED and the report points to the Events Manager
  real-time debug next step rather than guessing.
- CREDENTIALS check: no Freelancer.com account exists (only a PARTIAL Contra
  signup blocked on Turnstile, pending Jake). No live bid submitted; saved for
  Jake to review and submit manually.
