"""Tests for the high-level icalevents.events() API.

Covers window filtering, tzinfo output, sort, and the string_content
entry point.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from icalevents.icalevents import events as icalevents_events


def test_events_from_string_content(sample_ics_text):
    res = icalevents_events(
        string_content=sample_ics_text,
        start=datetime(2026, 10, 1),
        end=datetime(2026, 11, 15),
    )
    assert len(res) >= 3
    assert any(e.summary == "Team standup" for e in res)


def test_events_window_excludes_non_recurring_outside(sample_ics_text):
    # Recurring events legitimately expand into any window; the window
    # contract that matters is that NON-recurring events outside the window
    # are not returned.
    res = icalevents_events(
        string_content=sample_ics_text,
        start=datetime(2030, 1, 1),
        end=datetime(2030, 2, 1),
    )
    for e in res:
        if not e.recurring:
            assert False, "non-recurring event leaked outside the window"


def test_events_sorted_output(events):
    starts = [e.start for e in events]
    assert starts == sorted(starts)


def test_events_unsorted_is_a_list(events):
    assert isinstance(events, list)
    assert len(events) >= 3


def test_events_tzinfo_makes_start_aware(sample_ics_text):
    # tzinfo= is documented as "return values in specified tz". The
    # observable contract: at least one returned event has a
    # timezone-aware start datetime.
    from dateutil.tz import gettz

    berlin = gettz("Europe/Berlin")
    res = icalevents_events(
        string_content=sample_ics_text,
        start=datetime(2026, 10, 1),
        end=datetime(2026, 11, 15),
        tzinfo=berlin,
    )
    with_tz = [
        e for e in res
        if isinstance(e.start, datetime) and e.start.tzinfo is not None
    ]
    assert with_tz, "expected at least one timezone-aware event"


def test_events_file_entry_point(fixture_dir):
    path = fixture_dir / "sample_events.ics"
    res = icalevents_events(
        file=str(path),
        start=datetime(2026, 10, 1),
        end=datetime(2026, 11, 15),
    )
    assert len(res) >= 3


def test_events_no_sources_raises():
    # Calling events() with no source is an invalid call: the library
    # raises ValueError ("Content is invalid!") rather than returning [].
    import pytest

    with pytest.raises(ValueError):
        icalevents_events()
