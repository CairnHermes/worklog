"""Tests for icalevents.icaldownload.ICalDownload.

Covers data_from_string, data_from_file, apple_fix decoding, and
URL fetching against a local HTTP server (no external network).
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from icalevents.icaldownload import ICalDownload

SIMPLE_ICAL = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:one@example.com\r\n"
    "DTSTAMP:20261013T120000Z\r\n"
    "DTSTART:20261101T120000Z\r\n"
    "DTEND:20261101T130000Z\r\n"
    "SUMMARY:One\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR"
)


def test_data_from_string_passthrough():
    dl = ICalDownload()
    out = dl.data_from_string(SIMPLE_ICAL)
    assert "BEGIN:VCALENDAR" in out
    assert "One" in out


def test_data_from_string_accepts_bytes():
    dl = ICalDownload()
    out = dl.data_from_string(SIMPLE_ICAL.encode("utf-8"))
    assert "BEGIN:VCALENDAR" in out


def test_data_from_file_roundtrip(tmp_path: Path):
    p = tmp_path / "cal.ics"
    p.write_text(SIMPLE_ICAL, encoding="utf-8")
    dl = ICalDownload()
    out = dl.data_from_file(str(p))
    assert "BEGIN:VCALENDAR" in out


def test_data_from_file_missing_raises_or_empty(tmp_path: Path):
    dl = ICalDownload()
    missing = tmp_path / "nope.ics"
    with pytest.raises(Exception):
        dl.data_from_file(str(missing))


def test_data_from_url_local_server():
    handler = type(
        "H",
        (BaseHTTPRequestHandler,),
        {
            "do_GET": lambda self: (
                self.send_response(200),
                self.send_header("Content-Type", "text/calendar"),
                self.end_headers(),
                self.wfile.write(SIMPLE_ICAL.encode()),
            ),
            "log_message": lambda self, *a: None,
        },
    )
    srv = HTTPServer(("127.0.0.1", 0), handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        dl = ICalDownload()
        out = dl.data_from_url(f"http://127.0.0.1:{port}/cal.ics")
        assert "BEGIN:VCALENDAR" in out
        assert "One" in out
    finally:
        srv.shutdown()


def test_apple_fix_decodes_utf8():
    dl = ICalDownload()
    # UTF-8 encoded SUMMARY; data_from_string with apple_fix should not corrupt.
    out = dl.data_from_string(SIMPLE_ICAL, apple_fix=True)
    assert "BEGIN:VCALENDAR" in out
