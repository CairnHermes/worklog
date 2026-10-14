# Bid - Hourly News Scraper Portal

Job: https://www.freelancer.com/projects/... (Hourly News Scraper Portal)
105 proposals, 6 days left at scan time. Scope (from the posting): a Python
app that checks a chosen info website every hour, captures the freshly posted
content, works out what is new since the previous run (so nothing repeats),
and publishes the findings in a password-protected web portal. The exact site
and which data points to pull will be finalised with the client.

## Bid text

Hi,

This is a clean, well-scoped job and I have the working system built and tested
already, so I can show you real output quickly instead of a long pitch.

What I'd deliver, end to end:

- A configurable scraper: you give it the site URL and I point it at the exact
  elements to pull (title, summary, link, publish time). It handles both a
  plain HTML page and, where the site offers one, an RSS/Atom feed, which is
  the more reliable route when available.
- Hourly checks with smart de-duplication: every item is fingerprinted, and the
  app remembers what it has already seen. Only genuinely new items since the
  last run are surfaced, so your portal never shows the same story twice.
- A password-protected web portal: a simple, readable dashboard that lists the
  latest new items with their title, a short snippet, the publish time, and a
  link to the original. Login is HTTP Basic auth (user + password you set),
  and there's a clean JSON endpoint behind it too, in case you want to pull the
  data into another tool.
- It runs as one small Python process: `python app.py` starts the portal and
  the hourly loop, so it sits easily on any server or a small VPS.

I verified all of this live this morning: it pulled a real news feed, flagged
the new items on the first run and zero on the second (de-dup confirmed), and
the portal correctly returned 401 with no/incorrect credentials and the full
dashboard with the right ones.

A couple of things I'd lock down before building: which site we're targeting,
exactly which fields you want on each item, and where you'd like the portal to
live (your server, or I can stand one up). I reply fast, and I only hand over
code I've actually run.

Thanks,
Cairn

## Notes for Jake (not client-facing)
- PoC.py verified this run:
  - `python PoC.py --selftest` -> all checks passed (deterministic hashing, RSS
    parse, de-dup 3-new-then-0, state save/load roundtrip, HTML parse, Basic
    auth encoding), exit 0.
  - `python PoC.py --once` (live, BBC World RSS) -> 23 items, 23 new on first
    run; second `--once` -> 23 total, 0 new (de-dup confirmed).
  - Portal: 401 with no auth, 401 with wrong creds, 200 + HTML dashboard with
    correct creds, and /api/items returns JSON. All confirmed via curl.
- Stack: requests + BeautifulSoup4 + stdlib http.server (Basic auth). No
  framework needed; runs as one process. Verified with requests + bs4 already
  installed on the host.
- 105 proposals / 6 days left = HIGH competition. This is the weaker of the two
  picks on odds, but the scope is exactly in-lane (Python, scraping, API,
  small web portal) and the client's asks map 1:1 to what the PoC already does.
  If we bid, lead with the "already built and verified, show you output" angle
  and a same-week delivery. Suggest ~$300-$600 fixed (hourly loop + portal is
  a 2-3 day job; don't race to the bottom against 105 bidders).
- Ethical note: this is a legitimate personal news aggregator (public content,
  polite single fetches, the client picks the site). NOT a mass reseller
  scraping or ToS-evasion bot. Fine to take.
- No Freelancer.com account in CREDENTIALS.md -> NOT submitted live, saved for
  Jake to submit. (Only account on file is Contra, partial, needs Jake signup.)
