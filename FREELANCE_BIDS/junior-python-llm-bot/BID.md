# Bid: Junior Python LLM Bot Developer

Hi,

You want a developer who can build a bot that handles routine tasks using
both an online service and a local offline backend, and you were clear that
the person should be excited about building, not just filling in forms. That
is exactly the kind of work I take, so before I applied I built and ran the
hard core of the job so you can check my work instead of trusting a
description.

What I have working and tested already (a single Python file, stdlib only,
runs on any Python 3.9+ box with no packages to install):

- A clean split between the two backends you named. One interface, two real
  implementations behind it: an online provider that speaks the standard
  chat-completions wire format (key from an env var, any compatible endpoint
  works), and an offline local provider that answers the same task types with
  no network and no key. The bot code does not know which one is active. That
  "both online and offline" line is the whole architecture, and it is proven
  in the file, not promised.
- A task router. Incoming text is classified (order status, return policy,
  shipping ETA, refund status, product question, or a polite fallback) and
  dispatched to the right handler. That is the part that makes it an
  automation bot rather than a chat widget, and every route is tested.
- Guardrails on every response. Length cap, a blocked-topic refusal, a
  confidence score, and a trace id on every reply so it is auditable in logs.
  Nothing leaves the process that does not pass those.
- A self-test that proves all of it. `python PoC.py selftest` runs 24 real
  checks over the running code: routing for every task type, offline answers,
  the online provider's exact request payload (verified against an in-process
  mock that speaks the same JSON, so the wire format is real without a
  network call), guardrail rejections, and the provider swap. All 24 pass.

Honest notes, so there are no surprises:

- The offline backend in the proof is a small deterministic intent engine
  scoped to the task types in your brief. In the real build that is where a
  heavier local engine goes if you want one. The interface and the guardrails
  do not change; only the answer body does. I will not hand you a regex and
  call it a finished system.
- The online provider path is verified against the real wire format but runs
  in the proof against a mock, because I do not have your key or endpoint.
  Pointing it at your provider is configuration, not a rewrite.
- I am honest about the "junior" framing: the core engine, the router, the
  two backends and the guardrails are done and tested. The remaining work is
  scoping your exact task list and wiring in your real data source.

The way I would run this:

- I start by writing down the 5 to 10 routine tasks you actually want
  automated, in plain words, and I map each one to a task type. That list is
  the contract for the next step and it keeps the build from drifting.
- I build each task as a small, tested handler, with a self-test that fails
  before and passes after, so "done" is checkable rather than a claim.
- I hand you the file, the self-test, and a short note on how to add a new
  task type in five lines. You should be able to extend it without me.

A few things I need to quote a real number:

- Which 5 to 10 tasks are the priority for the first two weeks? That decides
  how much of the router and how many backends I build first.
- Do you already have a service you want to use (which one, and where it
  runs), or should I pick a small local one and keep a fallback online path?
  That is the single biggest swing in scope.
- Where does the bot live, a script on a box, a web endpoint, or a messaging
  channel? I will build the core the same way either way and wire the front
  to match.

If it helps, the fastest de-risk is a short paid day where I take three of
your real routine tasks, run them through this router and both backends, and
hand you the diff plus the passing self-test. Tell me the task list and I
will start that today.

Happy to jump on a call, walk through the working file together, then give you
a firm number and a timeline.

Thanks,
Cairn

## Notes for Jake (not client-facing)

- Live proof: `python PoC.py selftest` exits 0, 24/24 checks. Stdlib only
  (urllib, re, json, uuid, dataclasses, typing); no pip installs required.
  Boots the real TaskBot, exercises routing for all 5 intents + fallback,
  offline answers, OpenAI chat-completions wire format (url, Bearer auth,
  system+user messages, choices[0] parsing) against an in-process mock,
  guardrail length-cap + blocked-topic refusal, confidence ordering,
  trace-id format, provider swap, determinism.
- One real bug found and fixed in selftest: `\bship\b` did not match
  "shipping" (no word boundary inside the word), so shipping_eta routed to
  fallback. Fixed to `\bship\w*|\barrive\w*`. Re-ran: 24/24.
- AI-language rule honored: the client-facing body above contains no "AI",
  no "LLM", no "language model". The job title is left as-is (it is the
  client's title, not my claim). If Jake asks me to add AI/LLM wording for
  competitiveness, flag to Jake first per CREDENTIALS.md rule (do not lie,
  do not confirm, handle together).
- CREDENTIALS check: no Freelancer.com account on file (only the PARTIAL
  Contra signup, blocked on Jake's manual Turnstile step). No live bid
  submitted; saved for Jake to review and submit manually.
- Budget on listing is $72 avg bid (a junior-level, low-budget post). The
  bid scopes to 5-10 tasks + a paid 1-day de-risk to match the budget
  rather than inflating it.
