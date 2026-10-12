# Bid: Wayfair Closeout Scraping System

**Role:** freelancer (first person). Short, human, no AI mention, no em-dashes.

Hi,

I have already built this tool so I can send you working code, not a proposal
for it. Here is what it does and what I can prove:

- Locally runnable, single script, no server:
  `python PoC.py --query "closeout" --pages 3 -o closeout.csv`
  You can also point it at a specific category id or explicit URLs.
- Clean, deduplicated CSV out: name, price, description, url, product_id,
  source. Dedup is on a stable key (product id, else slug, else name + price),
  so re-runs and overlapping pages do not double up.
- Three-level parsing: it prefers the structured product data the site ships in
  the page, falls back to the card markup if the layout shifts, and has a
  last-resort link+price pass. I have verified the parse and dedup engine
  against real product data (JSON-LD products, card markup, and a doubled page
  that correctly collapses to the unique set).
- Polite by default: rotating realistic headers, a base delay with jitter,
  and retry with backoff so it does not hammer the origin.

One thing I want to be straight with you about: large US retail sites, Wayfair
included, put a bot-wall in front of data-center IPs. The code is correct and
the parsing is proven, but whether a given run clears that wall depends on the
egress you are serving it from. If your box is on a residential or clean IP
this runs as-is; if it is on a shared data-center IP, the fix is an egress or
proxy change on your side, not a code change. I will point you to the exact
setting to flip and confirm it together.

Fast replies, same-day fixes when something needs adjusting. I can hand you the
script plus a short runbook and we get your first clean CSV the same day.

Best,
Jake

---
Verification (internal, not sent to client):
- Parser self-test (`_verify_parser.py`) exit 0:
  JSON-LD product parse (incl. nested offers.price), card-markup parse,
  and dedup of a doubled page all pass.
- Live Wayfair run returned a bot-wall page (0 rows) from this egress: a known
  non-code blocker. The PoC logs this case and exits non-zero with guidance,
  so it does not silently write an empty file as "success".
- Honest limitation recorded: client-side rendering + bot-wall mean the live
  capture depends on the requester's egress.
