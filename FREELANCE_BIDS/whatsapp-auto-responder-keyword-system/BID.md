# Bid: WhatsApp Auto-Responder Keyword System -- 2

Hi,

Before writing this I read the acceptance criteria line by line and built
the hard core of the job so I could prove it, not promise it. What I have
working and tested already:

- Inbound handling over the official WhatsApp Business (Meta Cloud) API
  webhook, including the subscription handshake (verify token +
  hub.challenge echo) and signature verification. I use the real
  X-Hub-Signature-256 check, HMAC-SHA256 over the raw body, and I have it
  rejecting tampered and missing signatures while accepting the correct
  one. Your bot should not be answering unsigned messages.
- Keyword matching with the exact behavior you asked for: a keyword added
  through the dashboard takes effect on the very next incoming message, no
  restart. I tested this directly: add a new keyword via the dashboard API,
  send a message containing it, get the right reply back. Measured well
  inside your two-second acceptance window.
- A browser-based dashboard (plain HTML/JSON, no build step, runs happily
  on a Linux VPS) to list, add, edit, enable/disable and delete keywords,
  plus a live log of every incoming message and which keyword it matched,
  including misses.
- Seed import of an initial keyword set from CSV or JSON at first launch
  (I include a starter CSV you can edit).
- Outbound sends to the Cloud API messages endpoint with the exact payload
  shape Meta expects. Messenger (Graph API) works through the same keyword
  engine if you ever want it.

Honest notes, so there are no surprises:

- The proof runs the send step against a verified request recorder instead
  of a live number, because I do not have your WABA credentials. Wiring
  your real token, phone number ID and app secret is a small configuration
  change on the same code path, not a rewrite.
- The proof is Python + SQLite, single service. If you would rather have
  Node.js or PHP, the architecture carries over one-to-one; the keyword
  engine and webhook validation are the parts that cost time, and those
  are done.

Timeline and scope: the service, dashboard, Dockerfile/install script,
README (setup, credentials, day-to-day maintenance) and the CSV/JSON seed
are one contained package. I can have a working, tested build on your VPS
in about 4-5 days, with the first week including a live test run against
your own number so you can verify the two-second rule yourselves.

Questions I have:
- Which provider are you on, Meta Cloud API directly or a reseller like
  360dialog/Twilio? The payload is the same, the base URL and auth differ.
- Do you want a fallback reply when no keyword matches, or silence (as
  written)? I would default to silence and make it a config flag.
- One keyword or many per SKU-style topic? I will build per your seed list
  and the dashboard makes changes painless either way.

I can start this week and walk you through a live run of the keyword flow
before you commit to the full build.
