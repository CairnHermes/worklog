# Bid - Python ETL for Seaborn dashboard export

Job: https://www.freelancer.com/projects/pandas/python-etl-for-seaborn-dashboard
(Batch QA-PY-0108) 5 proposals, 6 days left at scan time. Scope (from the
posting): a Python 3-only deliverable. Data work on pandas/Polars, web work on
requests/httpx or FastAPI/Flask as fits. Core task: a Seaborn dashboard export.
Synthetic/sample inputs are enough for the demo run, no live keys needed.

## Bid text

Hi,

This one is squarely in my lane, and I already have the working pipeline built
and tested, so I can move fast and show you real output rather than a pitch.

What I'd deliver for the core task:

- A single Python 3 script, no other languages in the core logic.
- Extract: reads your CSV (or any tabular source you hand me), auto-detects the
  date column and the numeric metrics, and handles the messy parts I always
  run into in real data: gaps, a date that isn't a date, duplicate rows, and
  cells that should be numbers but aren't.
- Transform: cleans and sorts to one row per period, fills interior gaps, and
  adds the derived series a dashboard usually wants (rolling average,
  period-over-period change).
- Load / export: renders a 4-panel Seaborn dashboard (trend, last-N bar chart,
  metric correlation heatmap, raw-vs-rolling) and writes both PNG and PDF to
  disk, plus a clean CSV of the transformed data for your records.

I tested this end to end on a 52-week sample set: it runs clean, the self-test
passes, and it exports a valid PNG and PDF. Because your note says synthetic
inputs are fine for the demo, I can hand you a running script that takes a CSV
and produces the dashboard the same day we start, then point it at your real
file.

Two quick things I'd confirm before we begin: which columns are your primary
metrics, and do you want a single fixed dashboard or a couple of presets I can
generate on demand. I reply fast, and I only hand over code I've run.

Thanks,
Cairn

## Notes for Jake (not client-facing)
- PoC.py verified this run: `python PoC.py --selftest` -> "all checks passed"
  (deterministic hash/ETL/dedup/gap-fill checks, exit 0); `python PoC.py` ->
  extracted 52 rows, clean 52 (dropped 0), exported out/dashboard.png (179 KB)
  + out/dashboard.pdf (24 KB) + out/clean_data.csv. Ran on the host Python
  3.14 with pandas 3.0.5, matplotlib 3.11.2, seaborn 0.13.2 (installed this
  run via pip).
- The selftest caught a real bug in my sample generator: it emitted 2025-02-29
  (invalid, non-leap year) which the transform correctly dropped. Fixed the
  generator to use real calendar dates; re-verified clean. (Good: the transform
  is robust, my data was wrong.)
- Scope is small and clear (Python 3 only, synthetic data, one core task =
  Seaborn export). 5 proposals / 6 days = beatable, first-mover window if we
  act now.
- Client said "Python 3 only, no other languages for the core logic" and
  "synthetic/sample inputs are enough for the demo run, no live production
  keys." My PoC matches that exactly.
- Suggest fixed price in the ~$150-$350 band (it's a 1-2 day job; don't race
  to the bottom). If they want a full multi-page dashboard or a FastAPI
  endpoint on top, that's a separate quote.
- No Freelancer.com account in CREDENTIALS.md -> NOT submitted live, saved
  for Jake to submit.
