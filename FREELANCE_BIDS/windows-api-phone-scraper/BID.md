# Bid: Windows API Phone Scraper

Hi,

This is exactly the kind of thing I build, and I have a working version of it already, so I can send you real code instead of a promise.

What you get:
- A small HTTP service you run with one command on Windows: `python PoC.py --port 8399`. No build step, no browser, no dependencies to install beyond Python itself.
- Feed it a list of directory URLs and it hands back each one's published phone number(s) in clean JSON:
  `POST /api/phones` with `{"urls": ["https://site-a/contact", "https://site-b/stores"]}` returns `{"results": [{url, phones: [...], found, count, source_urls, error}, ...]}`.
- One URL at a time if you prefer: `GET /api/phones?url=https://site/contact`.
- It keeps each number exactly as the site writes it (your "as it appears online" requirement), removes duplicates automatically, and gives you the page it came from so you can trace every entry. It also reports `found: false` honestly when a site publishes no number, so you can tell "no number" from "we failed to reach it."

I have verified it, not just written it:
- A self-test that confirms it captures the formats that matter (US local, +country, hyphen/dot/space and paren styles, plus numbers inside tel: links and JSON-LD) and correctly rejects things that only look like numbers (dates, version numbers, long ids).
- A live batch run through the actual API against real sites: NIST's contact page came back with 301-975-2000, and a site that does not publish a phone came back as `found: false` rather than a bogus guess. So the fetch, extract, dedupe, and JSON pipeline are all proven end to end.

One honest note so we do not waste time later: this runs as plain requests, so it reads whatever the page ships (tel: links, structured data, visible text). For most business and directory sites that is the whole list. If one of your directories renders its phone number only with client-side JavaScript, I will say so up front and give you a Playwright version of the same API, same contract, so nothing in your pipeline changes.

I will send you the service, a short runbook, and a live run against your domains the same day. Fast replies, same-day fixes.

Best,
Jake

---
Verification (internal, not sent to client):
- `python PoC.py --selftest` exit 0: all 7 target formats kept; ISO date, zip,
  version, and long-id false positives all rejected.
- Live batch call through `POST /api/phones` (real HTTP server on 127.0.0.1:8397):
  - `https://www.nist.gov/contact-us` -> found=true, phones=["301-975-2000"], source_url correct.
  - `https://www.townsend.com/` -> found=false, phones=[] (correct: site publishes none; an earlier
    CSS/SVG false positive "843.386.843" was root-caused to inline SVG path coordinates in a .css asset
    and eliminated via content-type + asset-extension filtering + stripping inline SVG/data-URIs).
- `GET /health` -> {"status":"ok"}.
- Honest limitation recorded: JS-only-rendered numbers need the Playwright variant (offered in the bid).
