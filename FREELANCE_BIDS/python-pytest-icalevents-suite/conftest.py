"""Shared fixtures for the icalevents test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fixture_dir() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def sample_ics_bytes() -> bytes:
    return (FIXTURES / "sample_events.ics").read_bytes()


@pytest.fixture(scope="session")
def sample_ics_text() -> str:
    return (FIXTURES / "sample_events.ics").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def events(sample_ics_text):
    from datetime import datetime

    from icalevents.icalevents import events as icalevents_events

    return icalevents_events(
        string_content=sample_ics_text,
        start=datetime(2026, 10, 1),
        end=datetime(2026, 11, 15),
        sort=True,
    )
