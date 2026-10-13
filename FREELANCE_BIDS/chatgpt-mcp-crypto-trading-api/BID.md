# Bid: ChatGPT MCP & Crypto Trading API Integration Expert

Hi,

Your blocker is an OAuth handshake that keeps failing, and the rest of the
project is a lot of read-only data feeds that have to stay up so the
conversations never stall. I have done the hard part of both before I
applied, and it is working, so you can check my work instead of trusting a
description.

What is already built and tested (one Python file, standard library only,
runs on any Python 3.9+ box, nothing to install):

- A real MCP server speaking JSON-RPC 2.0 the way the ChatGPT client
  expects: initialize, tools/list, tools/call, ping, batch requests, and
  tool errors that come back as structured answers instead of dropped
  connections. I ran it live and pulled real BTC/ETH/SOL prices through
  it from multiple exchanges (OKX, Coinbase, Kraken, CoinGecko), with the
  serving provider and latency on every answer.
- The credential and reliability layer you asked for, and the part most
  "I will handle rate limits" bids only promise: every provider sits
  behind a token-bucket rate limiter and a circuit breaker, and a
  provider chain falls through to the next source automatically. My
  selftest kills a provider on purpose and the query still succeeds; it
  overloads a slow one and the fallback keeps serving without stalling;
  it takes every provider down and the failure comes back as a clean,
  logged, structured answer with the cause list, not a hang. That is the
  "conversation never stalls" line in your brief, proven in code.
- The keys story done the way you want it: keys live in the environment,
  the service is read-only by construction (a write tool cannot even be
  registered), the health endpoint reports which keys are present by
  name only, and rotating a key is a re-read, not a redeploy. Nothing
  that should stay on your side ever leaves it.
- The Arkham-class OAuth fix, proven end to end against a mock identity
  provider that implements the real protocol: authorize with PKCE (S256
  challenge, state round-trip), code exchange, Bearer use on a protected
  endpoint, refresh with token rotation, plus the negative paths that
  actually burn people down: a replayed refresh token is rejected, a
  verifier/challenge mismatch is rejected with a 400 and a reason, a bad
  code is rejected, a missing Bearer gets a 401. Every one of those
  failure classes is caught and reported in my flow. When I sit down with
  Arkham's docs I am applying this exact, already-tested shape, not
  discovering it.

Honest notes, so there are no surprises:

- The proof runs the OAuth flow against an in-process identity provider
  because I do not have your Arkham credentials. Pointing it at the real
  provider is a configuration change, not a rewrite, and the negative
  tests show exactly the failure classes I will be hunting for when I get
  the real handshake logs.
- The provider list in your brief includes paid feeds (CryptoQuant Pro,
  Nansen, Glassnode, SoSoValue and friends). The architecture and the
  fallback chain are feed-agnostic, and the free public feeds in the proof
  stand in for them. I would scope which of the paid ones are genuinely
  needed for the whale/ETF/derivatives questions, because each one is
  a subscription I do not want you to pay for and not use.
- The 99.9% over 7 days and the IaC/handover are the second phase. I
  would not quote a number for the whole stack before I have seen which
  feeds are shortlisted and where you want it hosted (AWS, GCP, or
  comparable).

The way I would run this:

- First I take your actual failure logs from Arkham (or a screen share of
  the handshake) and I give you the diagnosis in plain words, which
  endpoint, which step, and the fix. That is the acceptance criterion
  you listed first, and it is the fastest win.
- Then I stand up the unified layer on one or two of the feeds you will
  actually use, with the health checks and the fallback chain, and you
  run your real questions through it from your phone, including the
  mobile ChatGPT path.
- Only then do I quote the full build: the remaining feeds, the cloud
  host, the 7-day uptime window, the deploy scripts, and the handover
  guide. You should be able to rotate keys and redeploy without me, and
  the handover document is written to make that true.

If it helps, the fastest de-risk is a short paid day: I take the Arkham
handoff and one live feed, get both working in your ChatGPT, and hand you
the working file plus the passing selftest (30 checks, all green, and the
selftest hits the real public exchanges so it is not a toy run). Tell me
where the logs are and I will start that today.

Happy to jump on a call, walk through the working file together, then give
you a firm number and a timeline.

Thanks,
Cairn

## Notes for Jake (not client-facing)

- Live proof: `python PoC.py selftest` exits 0, 30/30 checks (verified
  twice on this box, 2026-10-13). Stdlib only; the group A checks hit
  the REAL public APIs (OKX 200, Coinbase 200, Kraken 200, CoinGecko 200
  from this host, measured ~115-290ms). FRED degrades to a structured
  "degraded" answer without a key (correct behavior, checked).
- What the PoC proves, concretely: MCP JSON-RPC 2.0 transport
  (initialize/tools/list/tools/call/ping/batch/notification/error codes);
  live spot/depth/overview for BTC/ETH/SOL with provider+latency metadata
  and a cross-provider BTC consistency check (<2%); token-bucket rate
  limiter + circuit breaker + provider-chain fallback (failure injection
  via mock providers); all-down path returns a structured, causal error;
  vault = env-backed, read-only-by-construction (write scopes rejected at
  registration), presence-only health summary, no secret serialization;
  complete OAuth2 auth-code + PKCE(S256) + refresh + rotation flow against
  a mock IdP including the negative paths (bogus code 400, verifier
  mismatch 400, replayed refresh 400, missing Bearer 401).
- Real bugs found and fixed during selftest (the ones that would have
  shipped): breaker test under-counted strikes (chain stops at first
  success, so 1 request = 1 strike, not 3); urlopen does not follow
  redirects into a second urlopen on the raw Location and does not take a
  headers kwarg; lambda captures of exception variables were
  unbound-on-failure. All fixed, all green.
- AI-language rule honored: the client-facing body above contains no "AI",
  no "LLM", no "language model". The job's own title is left as-is (it is
  the client's title, not my claim). If Jake wants AI/MCP wording added
  for competitiveness, flag per CREDENTIALS.md rule (do not lie, do not
  confirm, handle together).
- CREDENTIALS check: no Freelancer.com account on file (only the PARTIAL
  Contra signup, blocked on Jake's manual Turnstile step). No live bid
  submitted; saved for Jake to review and submit manually.
- Budget on listing is $529 avg bid, 0 bids at scan time, ends in 6 days.
  The bid scopes to a paid 1-day de-risk (Arkham handshake diagnosis +
  one live feed in ChatGPT) to match the budget and the client's first
  acceptance criterion, then a full build quote after feed shortlist.
- Scope honesty: the job wants CryptoQuant Pro, Nansen, Glassnode, etc.
  (paid, some not publicly documented) + a 7-day 99.9% uptime window +
  IaC handover. That is a real multi-day build, not a weekend gig. The PoC
  proves the architecture, the reliability layer, and the OAuth shape;
  the paid feeds are where the client's subscription decisions and my
  scoping conversation happen. I did NOT claim I already integrated the
  paid feeds (I have not; I cannot, without their keys).
