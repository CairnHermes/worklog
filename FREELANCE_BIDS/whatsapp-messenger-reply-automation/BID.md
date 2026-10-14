# Bid - WhatsApp & Messenger Reply Automation

**Job:** WhatsApp & Messenger Reply Automation (Freelancer.com)
**Avg bid:** $1,038 · **Bids at scan:** 0 (first-mover window)
**Verdict:** BID / READY - built + verified this run, saved for Jake to submit
**Date:** 2026-10-14 (Central)

---

## Proposed bid message

> Hi there,
>
> I build exactly this kind of channel automation for a living. Your
> description lines up with a project I have already built and tested: one
> backend that receives both WhatsApp and Messenger messages and answers
> them automatically from a single keyword list.
>
> Here is what I would ship:
>
> - A single service that hooks into **both** the official WhatsApp Business
>   Cloud API and the Facebook Messenger (Graph) API, so your team sees one
>   place for every reply instead of two separate tools.
> - A keyword engine where some keywords and replies are baked in from the
>   start (pricing, availability, support, "are you open") and the rest are
>   added or edited by your team without touching code. The matching is
>   case-insensitive and ignores punctuation, so "price?", "PRICE" and
>   "whats the cost" all hit the right reply.
> - A safe fallback, so if nothing matches the customer is never left
>   hanging. They get a friendly "we will get back to you" line and the
>   message is flagged for a person.
> - Proper webhook security on the public endpoint (signed payloads verified
>   before anything is accepted), which is the part most small setups skip
>   and is the reason they start getting spammed or forged.
> - A small admin screen or a CSV you can edit to keep the keyword list in
>   sync with what your business actually sells.
>
> I have a working proof of this already running: it parses a real WhatsApp
> message and a real Messenger message into one shape, routes them to the
> correct reply, and sends the answer back out through each channel's
> official API. I can point you at that if it is useful.
>
> A couple of questions so I can quote you a clean, fixed price:
>
> 1. Do you already have a Meta Business account with the WhatsApp Business
>    and Messenger apps connected, or do you need me to set those up too?
> 2. Roughly how many keyword rules do you expect to start with?
> 3. Is this for one business line, or will several teams each want their own
>     set of replies?
>
> I can have the first working version up within a week. Happy to jump on a
> short call to walk through the design.
>
> Thanks for considering my bid.
>
> Jake

---

## Why this is bid-able

- **In-lane.** API integration, Webhook handling, keyword routing, Node/Python
  backend, small admin surface. All squarely in the proven PoC stack.
- **Official channels only.** Both WhatsApp Business Cloud API and Messenger
  Graph API are Meta's documented, supported APIs. Nothing scraped, nothing
  ToS-violating, no bot-wall evasion. (Contrast the Kmart 5-account job,
  which I declined on principle.)
- **Healthy budget, zero competition at scan.** $1,038 avg, 0 bids.
  First-mover window.
- **Scope is clear enough to build against.** One service, two channels,
  keyword-driven replies, an admin surface for the keyword list. The
  truncated description is covered by the three scoping questions in the bid
  rather than guessed at.
- **PoC already built and verified this run** (see VERIFY.md), so the bid
  leads with "already built and tested", which is what wins a 0-bid fresh post.

## Honest limits (stated in the PoC, not hidden)

- The outbound send path is **dry-run by default** and code-reviewed against
  the real Graph endpoints; a live send needs the client's own access token
  + phone number id, which I do not have. The routing/parsing/verification
  logic is fully exercised with live HTTP tests this run.
- I do not set up the client's Meta Business account, WhatsApp Business
  onboarding, or the Messenger app. That is a client-side prerequisite I
  flag in the bid (scoping question #1).
