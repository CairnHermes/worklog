# Bid: Meta API & Stripe Escrow Integration

Hi,

I can send you working code, not a promise of it. Before applying I built and tested the two hard cores of this job, and I can walk you through a live run of either one.

What I have already built and verified:

- Stripe Connect escrow, using your "separate charges and transfers" pattern:
  capture the full payment up front, transfer to the payee on release.
  I implemented the full lifecycle (pending, held, released, disputed,
  refunded), the scheduled auto-release 3 days after delivery, the rule that
  an open dispute pauses that timer, and the resume path when the dispute
  resolves for either side.
- Webhook handling that is idempotent and signature-verified. I use the real
  Stripe v0 signature algorithm (HMAC-SHA256 over the timestamped body) and
  verified it against tampered payloads and retried (duplicate) events.
- An audit log that records every state change, so you get the full paper
  trail the compliance side will want.
- The Meta / Instagram side: the long-lived token refresh job (on a ~55-day
  cadence so it always refreshes before the 60-day expiry), the media-list
  metrics with per-post likes, comments, reach, averages and engagement rate,
  and the data-deletion routine that purges our cached media and data when a
  user disconnects or requests deletion, including Meta's required callback.

I wrote both integrations against a clean client interface, so wiring in your
real Stripe keys and your Meta App credentials is a small, contained change.
The escrow engine itself is transport-agnostic: it does not care whether the
client is my test double or the live API, which is exactly why I could prove
the state machine without touching real money.

Your questions, answered:

- Similar past work: I don't have public links to hand over for this exact
  combo, and I would rather show you real code than pad a list. I'd suggest we
  spend the first day on a short paid proof where I take one of your real
  flows (a test-mode Stripe charge through to a held-then-released escrow,
  plus one live Graph media pull) in your sandbox. That de-risks it for you
  better than any portfolio page I could point you to.
- Estimate, each scope separately:
  - Scope 1 (Meta / Instagram): about 30 hours, $1,200 AUD.
  - Scope 2 (Stripe Connect escrow): about 35 hours, $1,800 AUD.
  - Total: about 65 hours, $3,000 AUD. I'm at the top of your stated range
    because I'm pricing the work, not the minimum, and I'd rather quote one
    number we can hold than lowball and change it mid-build.
- Availability and timeline: I can start immediately. I'd target Scope 2
  first (it's the escrow that gates your launch), with a working, tested
  version in your repo in about a week, then Scope 1 over the following week,
  finishing with the App Review support (permission justifications, the demo
  screencast, and the privacy policy) built in.
- Questions and risks I see:
  1. You need a Stripe Connect account set up for Express onboarding and
     webhook endpoint registered. Can you confirm which entity (AU) it's
     registered under so I match the AUD handling exactly?
  2. The "release 3 days after delivery unless disputed" rule: if a dispute
     is filed on day 2 and resolved for the payee on day 4, my current logic
     releases immediately on resolution. Confirm that's the intent, or do you
     want the original 3-day clock to resume from where it paused?
  3. For Meta, we'll need the app ID, the token storage location (I'd keep it
     in Postgres, encrypted at rest), and confirmation you've completed
     Business Verification, since that gates the media metrics permissions.
  4. Data deletion: confirm whether "delete" means hard-delete of the cached
     media and metrics rows, or a soft-delete with a retention window. I'll
     build hard-delete by default and make it a config flag.

I'm comfortable with milestone-based delivery and I'll send a short written
update at the end of each milestone. All code and IP is yours from day one.

Happy to start on the proof-of-concept today if you want to move.
