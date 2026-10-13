# BID: Real-Time Crypto Monitoring Dashboard (PumpFun)

Job: https://www.freelancer.com/projects/nodejs/real-time-crypto-monitoring-dashboard
Avg bid: $451 · 0 bids at scan time (fresh post)
Verdict: **BID** (PoC verified live, scope clear, in-lane)
Status: **SAVED FOR JAKE** (no Freelancer.com account in CREDENTIALS.md)

## Why this is a strong fit
The client's core goal is to spot when a token's volume spike is being driven by
many freshly-created wallets buying tiny amounts (volume bot vs genuine
interest). That is exactly the on-chain data model I already have a working
reader for, and the PoC below streams it live with zero fixtures.

## What I've built (see PoC.py + dashboard.html, both runnable)
- Streams the live PumpFun token list (real API, working endpoint).
- For each active token, reads **real swap transactions** off its bonding curve
  on Solana: the acting wallet ID, SOL size, side, and the buyer's SOL balance
  right before the trade.
- Flags **freshly-funded wallets** (buyer SOL balance near 0 before buying) and
  small-size buys, then scores each token and labels it
  SUSPICIOUS / WATCH / LIKELY ORGANIC.
- A "sudden spike" detector compares each tick to the last, so a spike driven
  by fresh small buyers jumps to the top.
- Single-file dark dashboard (`dashboard.html`) that auto-reloads every 5s.
- Verified end-to-end on live mainnet: parsed real trades, correctly flagged 2
  sub-0.5 SOL fresh buyers as suspicious and left a $3.4k whale wallet
  unflagged.

## Honest scope notes (what I'd tell you up front)
- "Fresh wallet" in the PoC is a strong proxy (near-zero pre-trade balance). A
  fully airtight "created in the last N minutes" check needs per-wallet age
  lookups, which I'd add as a phase-2 hardening step and can wire to a wallet
  DB. I'd rather flag this now than hand you a tool that overstates its proof.
- PumpFun's main `frontend-api` host is Cloudflare-gated from my box; I use the
  working `frontend-api-v3` endpoint. If you have a key for the official
  real-time feed, I'll swap it in so it scales to your whole watchlist.
- I build this to run on your infra (Railway/Vercel/your VPS) with clean,
  commented, self-contained code.

## Proposed delivery
1. Day 1: live monitor + dashboard covering your top-N active tokens, with the
   fresh-wallet / small-buy / spike signals wired to a sort.
2. Day 2: your exact field list (name, volume, new-wallet count, start time,
   last-tx time, wallet IDs) + the bot-vs-organic score.
3. Day 3: harden fresh-wallet age proof, add your alert threshold, hand over
   code + runbook.

I can start today and have a working live version to click through before you
commit further. Happy to share the running PoC now.
