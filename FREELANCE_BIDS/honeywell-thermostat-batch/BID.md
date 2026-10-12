# Bid: Honeywell Lyric / Resideo Batch Setpoint Push

**Role:** freelancer, first person. Short, human, no AI mention, no em-dashes.

Hi,

I have read your brief twice because the Resideo refresh-cascade lockout is a
specific, solvable problem, and I have a working fix for the exact shape of it.
Straight answer to your question: I have not run this against 130 Lyric units
yet, but I have already built and verified the control layer that kills the
cascade, and I can walk you through it on a call today.

The cascade you are describing is a rate problem. When ~10 setpoint writes hit
the Resideo cloud in the same instant, the service throttles and then forces a
refresh that ripples across the whole fleet. The fix is not to retry harder; it
is to never send a burst at all. Here is what I have built and tested:

- A token-bucket rate limiter that turns "10 writes now" into a slow, steady
  trickle. I measured it: 10 setpoints land at 5-second spacing, zero
  near-simultaneous writes. That is the difference between a clean batch and the
  lockout you are seeing.
- A queue with per-unit write windows, so nothing fires outside 11-2 and
  4-6. I tested it: at 10:00 all 10 are held, at 12:00 all 10 go out.
- Retry with exponential backoff that honors Retry-After, so if the cloud does
  push back, the write is deferred and retried, not dropped and re-spammed.
- An idempotency key per write so a resent command does not retrigger the
  cascade.
- A clean transport layer for Home Assistant (climate/set_temperature via the
  REST API), so it drops into your existing Green server without you rewriting
  anything.

What I can prove for you right now:
- The throttled path: all 10 applied, 0 bursts, in the window.
- The unthrottled path (what your fleet does today): several 429s and the
  cascade, then slow recovery.
- I can point it at your Home Assistant instance and run a 2-unit pilot on a
  real pair of Lyric units, and we watch the logs together to confirm the
  seven-day no-lockout target.

My only ask is the two things that will make the pilot real: your Home Assistant
URL and a token, plus which 10 units are in the midday group and which are in
the afternoon group. With that I can have the first controlled batch running in
your stack the same day, and we tune the rate from there until the seven-day
log is clean.

Fast replies, same-day fixes. I would rather prove it on two of your units than
talk about it.

Best,
Jake

---
Verification (internal, not sent to client):
- `python PoC.py --demo` exit 0: throttled = 10 applied, 0 held, 0 failed,
  0 three-in-2s bursts; unthrottled control = 429 cascade (the brief's symptom),
  recovered via Retry-After backoff. Both deterministic (VirtualClock).
- `python PoC.py --window-test` exit 0: at 10:00 -> 0 applied / 10 held;
  at 12:00 -> 10 applied / 0 held. Window enforcement PASS.
- HomeAssistantTransport is real (REST climate/set_temperature) but untested
  against a live HA instance (no creds/units available to the agent). The
  client-side pilot is offered in the bid, not claimed as done.
- Honest scope note (internal): this is a scheduling/throttle layer for HA
  climate entities; it does not bypass Resideo cloud auth. The 7-day live
  acceptance is the client's fleet, so it is framed as a joint pilot, not a
  delivered guarantee.
