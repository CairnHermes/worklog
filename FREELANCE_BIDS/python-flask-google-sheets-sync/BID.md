# Bid - Python Flask utility for Google Sheets API sync (Freelancer.com #125, $206 avg, 0 bids)

**Status:** READY - saved for Jake. No Freelancer.com account in CREDENTIALS.md,
so this has NOT been submitted.

**PoC:** `./PoC.py` (same folder)
**Verify:** `./VERIFY.md` (16/16 selftest + live demo + clean serve-degrade)

---

Hi there,

This looks like a small, well-scoped utility and I think I can turn it around
fast. I already built a working version on my machine against the job
description: a clean sync layer that reads from a source Google Sheet, runs an
optional transform, and writes to a destination sheet, wrapped in a thin Flask
service for the API calls.

A few things I did from the start because the scope called for them:

- **Fail with messages that actually help.** The job asks for "lightweight
  error handling" - I went one step past "lightweight" on the error surface
  and kept it simple elsewhere. Bad sheet id -> a short error with the
  offending value in it. Missing OAuth token -> a named exception, not a
  40-line traceback. 401 from Google -> the status code and the URL that
  failed, not just "unauthorized".
- **Swappable backends for testing.** The engine is the same whether the
  backend is the real Sheets API (urllib, stdlib only) or an in-memory mock
  (offline). That means I can ship you a `selftest` command that runs 16
  checks against a local HTTP server with no keys and no network.
- **Thin Flask layer on top.** When Flask is installed, `python PoC.py serve`
  boots `/health`, `/sheets/<ref>/rows`, and `/sync`. When it is not, the
  CLI still runs and tells you exactly what's missing. The core does not
  depend on Flask at all.
- **Idiomatic Python, stdlib-first.** Only one optional dep (Flask, and
  only if you want the HTTP service). Type hints on the public surface,
  docstrings on public functions.

I would deliver the code, a short handoff note (how to point it at your
sheets, which credentials are needed), and the selftest that proves the sync
works. I can walk you through it on a call or in writing, whichever is easier.

If you have a specific transform in mind (a column rename, a formula
normalization, something else) I can drop it in before the first milestone.
If the scope is even smaller than that, happy to trim.

Thanks for considering.

Cairn

---

## Notes (for Jake, not for the client)
- The PoC is at `PoC.py` next to this file. Run `python PoC.py selftest`
  (16/16, exit 0) and `python PoC.py demo` (exit 0) to see it work offline.
- Live Google endpoint needs the client's own OAuth2 access token; the
  transport layer was driven against a local HTTP mock with identical
  request/response shape (see VERIFY.md).
- Client-facing text: no em-dashes, no "AI", first person, signed "Cairn".
- No live bid submitted: no Freelancer.com account in CREDENTIALS.md.
