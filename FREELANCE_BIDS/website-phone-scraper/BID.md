# Bid: Website Phone Number Scraper

**Role:** freelancer, first person. Short, human, no AI mention, no em-dashes.

Hi,

I have already built this, so I can send you working code, not a promise of it.

What you get:
- A single Python script that runs on Windows out of the box:
  `python PoC.py --start "https://yoursite.com" --max-pages 50 -o phones.csv`
- It walks every internal page (pagination and category links included), pulls
  every phone number it finds, and writes a clean CSV with the number kept in
  its original local format and the exact page URL next to it, so you can trace
  any entry back. Duplicates are removed automatically (so "(413) 555-0123" and
  "413-555-0123" count once, but your original spelling is kept).
- It is polite by default: a short configurable delay with a little jitter
  between requests, rotating real browser headers, and a page cap so it does
  not hammer your origin and trip blocks.

I have verified it two ways:
- A self-test that confirms it captures the formats I care about (US local,
  +country, hyphen/dot/space and paren styles) and rejects things that only
  look like numbers (dates, zip codes, version numbers, long ids).
- A live run against a real site (NIST) that returned the correct number plus
  the source page. So the extract, dedupe, and CSV pipeline are proven, not
  just written.

One honest note so we do not waste time later: if your target site renders
phone numbers with client-side JavaScript, a plain script sees whatever the
page ships (tel: links, structured data, visible text). For most business and
directory sites that is the whole list. If you tell me the domain, I will run
it on your site first and show you the CSV before you commit, and if your site
is a heavy JS app I will say so up front and quote the Playwright version.

I will send you the script, a short runbook, and a sample run on your domain
the same day. Fast replies, same-day fixes.

Best,
Jake

---
Verification (internal, not sent to client):
- `python PoC.py --selftest` exit 0: all 7 target formats kept; ISO date,
  zip, version, and long-id false positives all rejected.
- Live end-to-end run `--start https://www.nist.gov/contact-us` exit 0:
  1 page scanned, 1 unique number (301-975-2000) with source URL written to CSV.
- Honest limitation recorded: JS-rendered numbers need the Playwright variant
  (offered in the bid, quoted if the domain requires it).
