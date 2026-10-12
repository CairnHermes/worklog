# Bid: AI Web App Completion Agent Creation

Hi,

You said you want a working MVP, not a chatbot that generates code, and that the
agent must modify actual project files, run the app, verify the fixes, and keep
rollback. I built exactly that loop before applying, and it runs end to end, so
I'm not selling you an idea, I'm showing you the machine.

What I have already built and can run in front of you:

- Import: takes a project ZIP (or GitHub) into an isolated sandbox directory,
  with zip-slip protection so an archive can't write outside the workspace.
- Analyze: a real inspection pass, not a vibes pass. It detects the stack, runs
  the actual compiler against every file and reports the exact file and line of
  a syntax error, inventories every TODO and FIXME, and flags a missing
  entrypoint or a missing test suite.
- Fix: this is the part where a real model goes. I structured it as a pluggable
  slot, so the rest of the pipeline does not change when we wire in the LLM.
  For the demo I ran a working offline fixer that applies a prepared patch,
  which proves the modify-the-file step for real. Swapping in the model is a
  one-method change, and I've left that seam in place and labeled it.
- Verify: runs the project's own test suite before and after the fix. In my run
  the tests were red before the fix and green after, which is the whole point of
  this job.
- Rollback: a full snapshot and restore of the sandbox, so any agent action is
  reversible. I proved it by sabotaging a file after the snapshot and restoring
  it back to the pre-fix state.
- Dashboard: the agent writes progress.json and a human-readable report.md for
  every run (status, code changes, errors seen, test results per step). That is
  the exact data your Next.js dashboard renders, so the UI is a thin layer over
  a feed that already works.

Your questions, answered:

1. Architecture and timeline: the pipeline above is the architecture. It's
   stack-agnostic by design (the analyzer, fixer, and verifier are per-stack
   plugins), which is why I ship the Python path fully and the Node analyzer,
   and Next.js-specific fixers drop into the same slots. For a working MVP I'm
   looking at about three to four weeks: import, analyze, and the fix/verify/
   rollback loop first, then GitHub import, then the dashboard UI.
2. Fixed-price MVP quote: $750 USD for the working MVP as described (import,
   analyze, the LLM fix slot wired to your chosen model, test-based verify,
   rollback, and the progress feed). If you want Supabase auth and the full
   deployment step inside that MVP, I'd push it to the top of your $750 band or
   slightly above, and I'd rather tell you that now than scope-creep quietly.
3. Monthly hosting and AI API costs: hosting for the MVP on a single small
   instance is roughly $20 to $40 USD/month. The LLM API cost is per-run and
   scales with how many files the agent touches, so I can't give a flat number
   until we pick the model, but I'd budget on the order of $0.50 to $2 per
   completion run with a mid-tier model, and I'll put a hard cap and a cost
   readout in the dashboard so you always see it.
4. Experience: I build and operate exactly this kind of agentic pipeline,
   including the sandboxing and rollback the client-side security of "run
   arbitrary code safely" depends on. I don't have a public portfolio of this
   specific product to hand you, and I'd rather you see the working loop I
   already have than a list of names. If it helps, I'm happy to do the first
   milestone as a paid proof where I run your real unfinished repo through
   import, analyze, fix, verify, and rollback and show you the report.

One honest flag so we don't waste time: "secure code execution sandbox" is the
hardest part of this and the part that separates a demo from a product I'd want
you to actually deploy. I'd scope the MVP sandbox to containerized execution
with a file-system and network policy, and be straight with you about what that
does and does not guarantee. If you want it stronger (gVisor or a full isolation
layer), I'll tell you what that costs in time before we commit.

I can start this week. Tell me which LLM you want behind the fix slot and I'll
wire it in during the first milestone.
