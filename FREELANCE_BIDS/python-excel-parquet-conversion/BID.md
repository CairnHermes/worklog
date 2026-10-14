# Bid - Python 3 automation for Excel to Parquet conversion

**Job:** Python 3 automation for Excel to Parquet conversion (Freelancer.com)
**Score at scan:** 14 · **Avg bid:** $215 · **Bids at scan:** 0 (first-mover window)
**Verdict:** BID / READY - built + verified this run, saved for Jake to submit
**Date:** 2026-10-14 (Central)

---

## Proposed bid message

> Hi there,
>
> This one is right up my street. I have already built and tested an Excel to
> Parquet converter in Python 3 that matches exactly what you describe: clean,
> idiomatic code, clear structure, docstrings on the public functions, and
> pinned major versions so nothing drifts under you.
>
> Here is what I would ship:
>
> - A small, well-documented module with a public API for reading one or all
>   sheets of a workbook and writing each to a Parquet file, plus a `convert`
>   and `selftest` command line so it drops straight into a scheduled job.
> - Typed round-tripping that I have verified end to end: integers stay
>   integers, decimals stay decimals, booleans stay booleans, and date columns
>   come back as real Parquet timestamps rather than text. Empty columns keep
>   a stable schema instead of collapsing to a null blob.
> - Sensible error handling, so a missing file or a non-.xlsx input fails with
>   a clear, actionable message instead of a stack trace.
> - A deterministic self-test suite that runs the whole path offline and
>   reports pass/fail per check, so you (or your CI) can re-verify it on any
>   machine.
> - A pinned requirements file with the exact major versions I tested against,
>   which keeps the handoff and the install boring.
>
> I have a working proof of this already running: it builds a multi-sheet
> workbook covering all those cases, converts it to Parquet, reads it back,
> and confirms every value and type survived. All 17 checks pass. I can point
> you at that if it is useful.
>
> Two quick questions so I can quote a clean fixed price:
>
> 1. Are the source files always modern .xlsx (I can also handle .xlsm), or
>    will I need to read older .xls binary workbooks too?
> 2. Do you want this as a library your code imports, a command line tool, or
>    both? And any naming convention for the output files you already follow?
>
> I can have the first working version over within a day or two. Happy to walk
> through the design on a short call.
>
> Thanks for considering my bid.
>
> Jake

---

## Why this is bid-able

- **In-lane, exactly the brief.** Python 3, idiomatic structure, docstrings,
  minimal pinned dependencies, Excel -> Parquet. This is squarely the
  "Python, data processing, automation" stack the rest of the portfolio is
  built on.
- **Scope is clear enough to build against.** One core task (Excel to Parquet
  conversion), a stated style bar (idiomatic, documented, minimal deps, pinned
  versions), and a quick-turnaround ask. Nothing needs a scoping call to
  start.
- **Healthy budget, zero competition at scan.** $215 avg, 0 bids. First-mover
  window.
- **PoC already built and verified this run** (see VERIFY.md), so the bid
  leads with "already built and tested", which is what wins a fresh 0-bid post.

## Honest limits (stated in the PoC, not hidden)

- The PoC covers modern **.xlsx / .xlsm** workbooks. Legacy **.xls** binary
  files would need a different reader (xlrd) and are flagged as a question in
  the bid rather than silently assumed.
- Multi-sheet workbooks produce one Parquet file per sheet (named
  `<stem>__<sheet>.parquet`); the bid asks whether the client has a naming
  convention before locking that in.
