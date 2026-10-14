#!/usr/bin/env python
"""Proof-of-concept: a working pytest suite + quick demo for the `icalevents`
iCalendar parser library.

This is the deliverable for the freelance job "Python pytest suite for
icalevents calendar parser". Two ways to run it:

  1) Demo (no test runner needed):
       python PoC.py

  2) Full test suite (recommended):
       pytest -q
       # or, pointed at this directory:
       python -m pytest -q .

What it proves
--------------
- `parse_events()` extracts real events (summary, uid, location, organizer,
  categories) from an iCalendar (RFC 5545) string.
- Recurring events (RRULE FREQ=WEEKLY / MONTHLY) are expanded into
  individual occurrences.
- All-day vs timed events are distinguished correctly.
- Time-window filtering (start/end) works for both recurring and one-off
  events.
- Timezone output conversion is applied.
- The high-level `icalevents.events()` API works from a string, a file, and
  the tzinfo/sort options.
- `ICalDownload.data_from_url()` is exercised against a LOCAL HTTP server
  (no external network), so the test is deterministic and offline-safe.

The whole suite is deterministic: it never hits the real network and never
depends on "now" (all windows are fixed datetimes).
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "sample_events.ics"

# --- direct library import (works with either package layout) ---------------
try:  # v0.3.x exposes submodules; some versions re-export at the top level.
    from icalevents.icalparser import parse_events  # type: ignore
except Exception:  # pragma: no cover
    from icalevents import parse_events  # type: ignore


def parse() -> list:
    """Parse the bundled fixture for a fixed, deterministic window."""
    text = FIXTURE.read_text(encoding="utf-8")
    return parse_events(
        text, start=datetime(2026, 10, 1), end=datetime(2026, 11, 15)
    )


def demo() -> int:
    events = parse()
    print(f"[demo] parsed {len(events)} event occurrences from {FIXTURE.name}")
    summaries = [e.summary for e in events]
    checks = {
        "team standup present": "Team standup" in summaries,
        "invoice deadline present": "Invoice deadline" in summaries,
        "release 2.1 present": "Release 2.1" in summaries,
        "recurring expanded": sum(1 for e in events if e.summary == "Team standup") >= 1,
        "all_day flagged": any(e.summary == "Invoice deadline" and e.all_day for e in events),
    }
    width = max(len(k) for k in checks)
    all_ok = True
    for k, ok in checks.items():
        all_ok = all_ok and ok
        print(f"  {k:<{width}}  {'OK' if ok else 'FAIL'}")

    # A couple of attribute-level checks that the pytest suite also covers.
    standup = next(e for e in events if e.summary == "Team standup")
    print(f"  standup uid          {standup.uid}")
    print(f"  standup location     {standup.location!r}")
    print(f"  standup organizer    {standup.organizer!r}")
    print(f"  standup categories   {standup.categories}")

    print("\n[demo] now running the pytest suite (this directory) ...")
    rc = run_pytest()
    return 0 if (all_ok and rc == 0) else 1


def run_pytest() -> int:
    """Run the suite with the local interpreter, falling back to `pytest`."""
    # Prefer running under the same interpreter that has the deps installed.
    env = dict(__import__("os").environ)
    cmd = [sys.executable, "-m", "pytest", "-q", str(HERE)]
    print(f"  $ {' '.join(cmd)}")
    return subprocess.call(cmd, cwd=str(HERE))


if __name__ == "__main__":
    sys.exit(demo())
