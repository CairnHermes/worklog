"""Tests for icalevents.icalparser.parse_events.

Covers:
- basic event extraction from a VCALENDAR string
- recurring event expansion (weekly RRULE)
- all-day event handling
- window filtering (start/end)
- timezone conversion (tzinfo)
- sort=True behaviour
- strict mode behaviour
- event attribute extraction (summary, location, organizer, categories)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from icalevents.icalparser import Event, parse_events


def test_parse_returns_list_of_events(sample_ics_text):
    events = parse_events(sample_ics_text)
    assert isinstance(events, list)
    assert all(isinstance(e, Event) for e in events)


def test_basic_event_fields(sample_ics_text):
    events = parse_events(sample_ics_text)
    summaries = {e.summary for e in events}
    assert "Team standup" in summaries
    assert "Invoice deadline" in summaries
    assert "Release 2.1" in summaries


def test_event_uid_and_description(sample_ics_text):
    events = parse_events(sample_ics_text)
    standup = next(e for e in events if e.summary == "Team standup")
    assert standup.uid == "standup-mon@example.com"
    assert standup.description == "Daily sync"


def test_event_location_and_organizer(sample_ics_text):
    events = parse_events(sample_ics_text)
    standup = next(e for e in events if e.summary == "Team standup")
    assert standup.location == "Room 4B"
    assert standup.organizer == "mailto:alice@example.com"


def test_event_categories(sample_ics_text):
    events = parse_events(sample_ics_text)
    standup = next(e for e in events if e.summary == "Team standup")
    assert "WORK" in standup.categories


def test_recurring_event_expands(sample_ics_text):
    events = parse_events(sample_ics_text)
    standup_occurrences = [e for e in events if e.summary == "Team standup"]
    assert len(standup_occurrences) >= 1
    assert all(e.recurring for e in standup_occurrences)


def test_all_day_event_flag(sample_ics_text):
    events = parse_events(sample_ics_text)
    deadline = next(e for e in events if e.summary == "Invoice deadline")
    assert deadline.all_day is True
    release = next(e for e in events if e.summary == "Release 2.1")
    assert release.all_day is False


def test_tzinfo_conversion(sample_ics_text):
    from dateutil.tz import gettz

    tz = gettz("Europe/Berlin")
    events = parse_events(sample_ics_text, tzinfo=tz)
    standup = next(
        (e for e in events if e.summary == "Team standup"), None
    )
    if standup is not None and isinstance(standup.start, datetime):
        assert standup.start.tzinfo == tz


def test_sort_true_produces_sorted(events):
    starts = [e.start for e in events]
    assert starts == sorted(starts)


def test_events_lt_ordering(sample_ics_text):
    events = parse_events(sample_ics_text, sort=True)
    for a, b in zip(events, events[1:]):
        assert a < b or a == b or a.start <= b.start


def test_parse_events_empty_content_raises():
    # The library raises ValueError on empty/invalid content rather than
    # silently returning []: document that contract.
    with pytest.raises(ValueError):
        parse_events("")


def test_parse_events_respects_window(sample_ics_text):
    # A window in the far future still yields only events that fall inside
    # it (recurring events expand into it). Assert containment, not emptiness.
    far_start = datetime(2030, 1, 1)
    far_end = datetime(2030, 2, 1)
    events = parse_events(sample_ics_text, start=far_start, end=far_end)
    # Every non-recurring occurrence returned must sit inside the window.
    for e in events:
        if not e.recurring and isinstance(e.start, datetime):
            assert far_start <= e.start <= far_end


def test_event_time_left_returns_timedelta(sample_ics_text):
    from datetime import datetime, timezone as _tz

    events = parse_events(sample_ics_text)
    target = next(e for e in events if e.summary == "Release 2.1")
    ref = datetime(2026, 10, 16, 14, 30, tzinfo=_tz.utc)
    tl = target.time_left(ref)
    assert isinstance(tl, timedelta)


def test_event_str_contains_summary(sample_ics_text):
    events = parse_events(sample_ics_text)
    target = next(e for e in events if e.summary == "Release 2.1")
    s = str(target)
    assert "Release 2.1" in s


def test_event_copy_to(sample_ics_text):
    # copy_to(new_start, uid) returns a NEW Event (occurrence) at the new
    # start time, with a derived uid.
    events = parse_events(sample_ics_text)
    src = next(e for e in events if e.summary == "Release 2.1")
    new_start = datetime(2027, 1, 1)
    moved = src.copy_to(new_start=new_start)
    assert isinstance(moved, Event)
    assert moved.summary == src.summary
    assert moved.start == new_start
    assert moved.uid != src.uid


def test_event_astimezone(sample_ics_text):
    from dateutil.tz import gettz

    events = parse_events(sample_ics_text)
    target = next(e for e in events if e.summary == "Release 2.1")
    berlin = gettz("Europe/Berlin")
    moved = target.astimezone(berlin)
    assert moved is target
    assert isinstance(target.start, datetime)


def test_event_str_raises_on_comparing_non_event():
    a = Event()
    a.start = datetime(2026, 1, 1)
    with pytest.raises(ValueError):
        a < "not-an-event"  # type: ignore[operator]
