# Bid - Automated Data-Extraction & Website Integration Developer

Job: https://www.freelancer.com/projects/api-developmet/automated-data-extraction-website
Avg bid $728, 0 bids at scan time. Scope: a reliable automated data-extraction
system for 8 third-party websites, results exposed through an API so the
client's website / plugin can consume them.

## Bid text

Hi,

This is a job I do a lot, and the part that usually trips other people up is
not the scraping, it's the integration, the piece where your website actually
gets clean data to work with. So that's where I'd start, and here's the shape
I'd build.

One config file drives the whole thing. Each of the 8 sites gets a block:
its name, the pages to hit, and a small map saying where each field lives
(product name, price, an ID, availability, whatever you care about). Because
it's selector-driven and not hard-coded to one layout, adding a 9th site or
repointing a page that moves is a config edit, not a rewrite. That's how it
stays reliable instead of breaking the moment a site tweaks its markup.

Under the hood I keep it boring on purpose:
- Real fetching with retries and a polite rate limit, so you're not hammering
  a source into blocking you.
- A de-duplication pass, so the same product on two pages doesn't become two
  rows.
- A small data store that is the single source of truth. The crawler writes
  to it, the API reads from it. That decoupling matters: you can run the
  crawl on a schedule and have the API serve fresh data instantly in between.
- A clean JSON API for your frontend or plugin to call: list of sources,
  latest records per source, all records, and a refresh endpoint if you ever
  want to trigger a pull on demand.

I already have a working prototype of this exact pipeline running on my
machine, including the API side. I proved it end to end: I point it at a set
of sample sources with different layouts, it extracts and de-dupes correctly
(reading fields from both text and data attributes), writes the store, and
serves everything back over the JSON API, including the refresh call. I can
walk you through it or adapt the field maps to your 8 sites in the first day.

A couple of practical notes that will save you time:
- I'd give each source its own retry + fallback, so one flaky site never
  takes the whole batch down.
- If any of the 8 renders its data with JavaScript, I'll add a browser step
  just for that one rather than slowing everything else down.
- I'd include a dry run that logs exactly what would be pulled so you can
  sanity-check a tricky source before I flip it on.

If you send me the 8 URLs and the fields you actually need out of each, I can
have a first clean pull in your hands quickly, then we tune the mappings until
the data is exactly what your site wants.

Fast replies, same-day fixes. I'd rather show you a working pull than describe
one.

Best,
Jake

## Notes for Jake (not client-facing)
- PoC.py verified: `python PoC.py --selftest` exits 0. It serves two FAKE
  third-party sites locally (different layouts), runs the REAL extractor
  (config-driven selectors, text + data-attribute field reads, retries,
  dedupe, JSON store write), then starts the REAL JSON API (healthz,
  /api/sites, /api/data?site=, /api/data, POST /api/refresh) and drives it
  over HTTP with `requests`. Asserts: 5 records across 2 sites, correct field
  values, refresh works, unknown site returns graceful empty.
- Honest scope note for the client: the client's 8 sites are unknown to us;
  the PoC proves the pipeline (extraction + store + API), not their live
  pages. Bid says "I can have a first clean pull ... then we tune mappings"
  and "if a source renders with JS I add a browser step for that one" rather
  than claiming all 8 already scraped.
- Dependency-light: only `requests` + `bs4` + stdlib (http.server). No Flask
  needed. Runs on Python 3.14 here.
- One real defect caught + fixed this run: the JSON store's _save() re-acquired
  a non-reentrant lock inside put() and deadlocked the whole batch. Now
  lock-free (callers hold the lock). Selftest would have hung without the fix.
- No Freelancer.com account on file -> save for Jake to submit.
- Bid sizing: $728 avg, 0 bids. Scope is real (8 sites + API + maintenance).
  Suggest a fixed price in the $600-$900 band, or $35-$55/hr if they prefer
  hourly; the 8 sources + config + API is well within that for one dev.