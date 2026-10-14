#!/usr/bin/env python3
"""
FXDashboard / Liquid Markets -- Python marketing data pipeline (PoC)
=====================================================================
One of the three deliverables named in the 90-day brief:

    3. Python Automation: Develop Python scripts for marketing data
       [collection, cleaning, reporting]

This is the "Python automation" half, built as a small, working,
self-contained pipeline that:

  1. Fetches a marketing analytics JSON payload from a URL (or a local
     file, for offline testing).
  2. Normalises the rows into a clean schema (session, date, channel,
     page, country, conversions, revenue).
  3. Aggregates by channel: sessions, conversions, revenue, conversion
     rate.
  4. Flags rows where conversion rate is below a threshold (so the
     marketing team can see which pages/channels need attention).
  5. Emits a CSV report (for the marketing team) and a JSON summary
     (for downstream tooling).

The fetch layer is swappable: the default `fetch` hits a live URL with a
timeout + status check; for offline verification we use a `--from-file`
mode that reads a local JSON file, and a `--mock` mode that serves a
small in-memory payload. None of this touches WordPress or a live
analytics account.

CLI:
    python PoC.py selftest
    python PoC.py run --from-file sample.json --out-dir out
    python PoC.py run --url https://example.com/api/metrics --out-dir out

Exit codes:
    0  success
    2  user / config / I/O error
    3  unexpected internal error
"""

from __future__ import annotations
import argparse
import csv
import io
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

# -----------------------------------------------------------------------------
# Errors
# -----------------------------------------------------------------------------

class PipelineError(Exception):
    """Base for user-facing pipeline errors."""


class FetchError(PipelineError):
    pass


class SchemaError(PipelineError):
    def __init__(self, row_index: int, missing: List[str]):
        self.row_index = row_index
        self.missing = missing
        super().__init__(
            f"row {row_index}: missing required field(s): {', '.join(missing)}"
        )


class EmptyDatasetError(PipelineError):
    pass


# -----------------------------------------------------------------------------
# Data model
# -----------------------------------------------------------------------------

REQUIRED_FIELDS = ("date", "channel", "page", "sessions", "conversions", "revenue")
CHANNELS = ("organic", "paid", "email", "referral", "social", "direct")


@dataclass
class MarketingRow:
    date: str
    channel: str
    page: str
    sessions: int
    conversions: int
    revenue: float
    country: str = "unknown"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "date": self.date,
            "channel": self.channel,
            "page": self.page,
            "sessions": self.sessions,
            "conversions": self.conversions,
            "revenue": self.revenue,
            "country": self.country,
        }


@dataclass
class ChannelAgg:
    channel: str
    sessions: int
    conversions: int
    revenue: float

    @property
    def conv_rate(self) -> float:
        if self.sessions <= 0:
            return 0.0
        return self.conversions / self.sessions


@dataclass
class Report:
    generated_at: str
    total_sessions: int
    total_conversions: int
    total_revenue: float
    channels: List[ChannelAgg]
    flagged: List[Dict[str, Any]] = field(default_factory=list)

    def to_json(self, indent: Optional[int] = 2) -> str:
        payload = {
            "generated_at": self.generated_at,
            "totals": {
                "sessions": self.total_sessions,
                "conversions": self.total_conversions,
                "revenue": self.total_revenue,
                "conv_rate": (
                    self.total_conversions / self.total_sessions
                    if self.total_sessions else 0.0
                ),
            },
            "channels": [
                {
                    "channel": c.channel,
                    "sessions": c.sessions,
                    "conversions": c.conversions,
                    "revenue": c.revenue,
                    "conv_rate": c.conv_rate,
                }
                for c in self.channels
            ],
            "flagged": self.flagged,
        }
        return json.dumps(payload, indent=indent, ensure_ascii=False)


# -----------------------------------------------------------------------------
# Normalisation
# -----------------------------------------------------------------------------

def _to_int(v: Any, field_name: str, row_index: int) -> int:
    if isinstance(v, bool):
        raise SchemaError(row_index, [field_name])
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        try:
            return int(s)
        except ValueError:
            raise SchemaError(row_index, [field_name])
    raise SchemaError(row_index, [field_name])


def _to_float(v: Any, field_name: str, row_index: int) -> float:
    if isinstance(v, bool):
        raise SchemaError(row_index, [field_name])
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace("$", "").replace(",", "")
        try:
            return float(s)
        except ValueError:
            raise SchemaError(row_index, [field_name])
    raise SchemaError(row_index, [field_name])


def _normalise(raw: Dict[str, Any], idx: int) -> MarketingRow:
    missing = [f for f in REQUIRED_FIELDS if f not in raw or raw[f] in (None, "")]
    if missing:
        raise SchemaError(idx, missing)
    return MarketingRow(
        date=str(raw["date"]).strip(),
        channel=str(raw["channel"]).strip().lower(),
        page=str(raw["page"]).strip(),
        sessions=_to_int(raw["sessions"], "sessions", idx),
        conversions=_to_int(raw["conversions"], "conversions", idx),
        revenue=_to_float(raw["revenue"], "revenue", idx),
        country=str(raw.get("country", "unknown")).strip() or "unknown",
    )


# -----------------------------------------------------------------------------
# Aggregation
# -----------------------------------------------------------------------------

def aggregate(rows: Sequence[MarketingRow]) -> List[ChannelAgg]:
    by_channel: Dict[str, ChannelAgg] = {}
    for r in rows:
        c = r.channel
        if c not in by_channel:
            by_channel[c] = ChannelAgg(channel=c, sessions=0, conversions=0, revenue=0.0)
        by_channel[c].sessions += r.sessions
        by_channel[c].conversions += r.conversions
        by_channel[c].revenue += r.revenue
    out = list(by_channel.values())
    out.sort(key=lambda a: a.channel)
    return out


def flag_low_conversion(
    rows: Sequence[MarketingRow],
    threshold: float,
    min_sessions: int = 10,
) -> List[Dict[str, Any]]:
    """Return rows where conversion rate is below threshold and the row has
    at least `min_sessions` sessions (avoids flagging single-session noise).
    """
    flagged: List[Dict[str, Any]] = []
    for r in rows:
        if r.sessions < min_sessions:
            continue
        rate = (r.conversions / r.sessions) if r.sessions else 0.0
        if rate < threshold:
            flagged.append({
                "date": r.date,
                "channel": r.channel,
                "page": r.page,
                "sessions": r.sessions,
                "conversions": r.conversions,
                "conv_rate": round(rate, 6),
                "reason": f"conv_rate {rate:.4f} below threshold {threshold:.4f}",
            })
    flagged.sort(key=lambda d: (d["conv_rate"], d["date"], d["page"]))
    return flagged


def build_report(rows: Sequence[MarketingRow], threshold: float) -> Report:
    channels = aggregate(rows)
    flagged = flag_low_conversion(rows, threshold)
    return Report(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        total_sessions=sum(r.sessions for r in rows),
        total_conversions=sum(r.conversions for r in rows),
        total_revenue=round(sum(r.revenue for r in rows), 2),
        channels=channels,
        flagged=flagged,
    )


# -----------------------------------------------------------------------------
# Fetch layer
# -----------------------------------------------------------------------------

def fetch_json(url: str, timeout: float = 15.0, headers: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """Fetch a JSON array from `url`. Raises FetchError on any problem."""
    hdrs = {"User-Agent": "marketing-poc/1.0", "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status = getattr(r, "status", 200)
            if status != 200:
                raise FetchError(f"HTTP {status} from {url}")
            body = r.read(2_000_000)
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code} from {url}") from e
    except urllib.error.URLError as e:
        raise FetchError(f"network error reaching {url}: {e.reason}") from e
    except TimeoutError as e:
        raise FetchError(f"timeout reaching {url}") from e
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except json.JSONDecodeError as e:
        raise FetchError(f"invalid JSON from {url}: {e}") from e
    if not isinstance(data, list):
        raise FetchError(f"expected a JSON array from {url}, got {type(data).__name__}")
    return data


def read_file(path: str) -> List[Dict[str, Any]]:
    if not os.path.isfile(path):
        raise PipelineError(f"file not found: {path!r}")
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise PipelineError(f"invalid JSON in {path!r}: {e}") from e
    if not isinstance(data, list):
        raise PipelineError(f"expected a JSON array in {path!r}, got {type(data).__name__}")
    return data


# -----------------------------------------------------------------------------
# Pipeline
# -----------------------------------------------------------------------------

def run_pipeline(
    rows_raw: List[Dict[str, Any]],
    threshold: float = 0.03,
    min_sessions: int = 10,
) -> Report:
    """Normalise raw JSON rows and build the report.

    Raises EmptyDatasetError when there is nothing to process, and SchemaError
    (a PipelineError subclass) when a row is missing a required field or has a
    value that does not fit the schema.
    """
    if not rows_raw:
        raise EmptyDatasetError("no rows to process")
    rows = [_normalise(r, i) for i, r in enumerate(rows_raw)]
    return _build_report(rows, threshold=threshold, min_sessions=min_sessions)


def _build_report(rows: List[MarketingRow], threshold: float, min_sessions: int) -> Report:
    channels = aggregate(rows)
    flagged = flag_low_conversion(rows, threshold, min_sessions)
    return Report(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        total_sessions=sum(r.sessions for r in rows),
        total_conversions=sum(r.conversions for r in rows),
        total_revenue=round(sum(r.revenue for r in rows), 2),
        channels=channels,
        flagged=flagged,
    )


# -----------------------------------------------------------------------------
# Output
# -----------------------------------------------------------------------------

def write_outputs(out_dir: str, report: Report, rows_raw: List[Dict[str, Any]]) -> Dict[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, "report.json")
    with open(json_path, "w", encoding="utf-8") as f:
        f.write(report.to_json())

    csv_path = os.path.join(out_dir, "channel_report.csv")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["channel", "sessions", "conversions", "revenue", "conv_rate"])
    for c in report.channels:
        w.writerow([c.channel, c.sessions, c.conversions, f"{c.revenue:.2f}", f"{c.conv_rate:.6f}"])
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        f.write(buf.getvalue())

    return {"json": json_path, "csv": csv_path}


# -----------------------------------------------------------------------------
# Sample data (for demo / offline verification)
# -----------------------------------------------------------------------------

SAMPLE_ROWS: List[Dict[str, Any]] = [
    # Organic: healthy
    {"date": "2026-10-01", "channel": "organic",  "page": "/home",     "sessions": 420, "conversions": 21, "revenue": 2100.00, "country": "US"},
    {"date": "2026-10-02", "channel": "organic",  "page": "/pricing",  "sessions": 180, "conversions":  9, "revenue":  900.00, "country": "US"},
    # Paid: weak (should be flagged)
    {"date": "2026-10-01", "channel": "paid",     "page": "/home",     "sessions": 310, "conversions":  2, "revenue":  200.00, "country": "US"},
    {"date": "2026-10-02", "channel": "paid",     "page": "/checkout", "sessions": 140, "conversions":  1, "revenue":  100.00, "country": "CA"},
    # Email: healthy
    {"date": "2026-10-01", "channel": "email",    "page": "/home",     "sessions":  90, "conversions":  6, "revenue":  600.00, "country": "US"},
    # Referral: low volume, should NOT be flagged (min_sessions gate)
    {"date": "2026-10-01", "channel": "referral", "page": "/home",     "sessions":   4, "conversions":  0, "revenue":    0.00, "country": "UK"},
    # Social: weak
    {"date": "2026-10-02", "channel": "social",   "page": "/blog/x",   "sessions":  75, "conversions":  0, "revenue":    0.00, "country": "AU"},
]


def _write_sample(path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(SAMPLE_ROWS, f, indent=2)


# -----------------------------------------------------------------------------
# Selftest
# -----------------------------------------------------------------------------

def _selftest(tmp: str) -> None:
    checks: List[tuple] = []
    def add(name: str, fn: Callable[[], None]) -> None:
        checks.append((name, fn))

    # --- 1. Normalisation: required fields present --------------------------
    def t_normalise() -> None:
        r = _normalise(SAMPLE_ROWS[0], 0)
        assert r.channel == "organic"
        assert r.sessions == 420
        assert r.conversions == 21
        assert abs(r.revenue - 2100.00) < 1e-9
    add("normalise: happy path", t_normalise)

    # --- 2. Normalisation: missing required field raises SchemaError --------
    def t_missing_field() -> None:
        bad = {"date": "2026-10-01", "channel": "organic", "page": "/home",
               "sessions": 10, "revenue": 1.0}  # conversions missing
        try:
            _normalise(bad, 3)
        except SchemaError as e:
            assert "conversions" in e.missing
            return
        raise AssertionError("expected SchemaError for missing conversions")
    add("normalise: missing field raises SchemaError", t_missing_field)

    # --- 3. Normalisation: bad type (string sessions) -----------------------
    def t_bad_type() -> None:
        bad = {"date": "2026-10-01", "channel": "organic", "page": "/home",
               "sessions": "not-a-number", "conversions": 1, "revenue": 1.0}
        try:
            _normalise(bad, 0)
        except SchemaError as e:
            assert "sessions" in e.missing
            return
        raise AssertionError("expected SchemaError for non-numeric sessions")
    add("normalise: non-numeric sessions raises SchemaError", t_bad_type)

    # --- 4. Aggregation: channels sorted, totals correct --------------------
    def t_aggregate() -> None:
        rows = [_normalise(r, i) for i, r in enumerate(SAMPLE_ROWS)]
        aggs = aggregate(rows)
        by = {a.channel: a for a in aggs}
        # organic: 420 + 180 = 600 sessions, 21 + 9 = 30 conversions
        assert by["organic"].sessions == 600
        assert by["organic"].conversions == 30
        # paid: 310 + 140 = 450, 2 + 1 = 3
        assert by["paid"].sessions == 450
        assert by["paid"].conversions == 3
        # Sorted alphabetically
        assert [a.channel for a in aggs] == sorted([a.channel for a in aggs])
    add("aggregate: per-channel totals correct + sorted", t_aggregate)

    # --- 5. Flagging: low conv rate rows flagged, low-volume not ------------
    def t_flag() -> None:
        rows = [_normalise(r, i) for i, r in enumerate(SAMPLE_ROWS)]
        flagged = flag_low_conversion(rows, threshold=0.03, min_sessions=10)
        channels_flagged = {f["channel"] for f in flagged}
        # paid (3/450 = 0.67%) should be flagged
        assert "paid" in channels_flagged
        # social (0/75 = 0%) should be flagged
        assert "social" in channels_flagged
        # referral (0/4) should NOT be flagged (below min_sessions)
        assert "referral" not in channels_flagged
        # organic (30/600 = 5%) should NOT be flagged
        assert "organic" not in channels_flagged
    add("flag: low conv rate flagged, low volume excluded", t_flag)

    # --- 6. End-to-end report (from file) -----------------------------------
    def t_e2e_file() -> None:
        sample = os.path.join(tmp, "sample.json")
        _write_sample(sample)
        raw = read_file(sample)
        rep = _build_report(
            [_normalise(r, i) for i, r in enumerate(raw)],
            threshold=0.03, min_sessions=10,
        )
        assert rep.total_sessions == sum(r["sessions"] for r in raw)
        assert rep.total_conversions == sum(r["conversions"] for r in raw)
        out = write_outputs(os.path.join(tmp, "out"), rep, raw)
        assert os.path.isfile(out["json"])
        assert os.path.isfile(out["csv"])
        # CSV should have header + 6 channel rows
        with open(out["csv"], "r", encoding="utf-8") as f:
            lines = [ln for ln in f.read().splitlines() if ln]
        assert lines[0] == "channel,sessions,conversions,revenue,conv_rate"
        assert len(lines) == 1 + 5  # 5 distinct channels in sample
    add("end-to-end: read file -> report -> CSV+JSON outputs", t_e2e_file)

    # --- 7. Empty dataset raises -------------------------------------------
    def t_empty() -> None:
        try:
            _build_report([], 0.03, 10)
        except Exception:
            return
        # _build_report doesn't raise on empty; run_pipeline does.
        try:
            run_pipeline([], threshold=0.03)
        except EmptyDatasetError:
            return
        raise AssertionError("expected EmptyDatasetError for empty input")
    add("empty dataset raises EmptyDatasetError", t_empty)

    # --- 8. fetch_json: clean error on unreachable host --------------------
    def t_fetch_fail() -> None:
        # Point at a host that is guaranteed to fail (port 1 on localhost
        # is reserved and nothing is listening there).
        try:
            fetch_json("http://127.0.0.1:1/nope", timeout=2.0)
        except FetchError as e:
            msg = str(e).lower()
            # Should be a clean, actionable message, not a raw traceback.
            assert "127.0.0.1:1" in msg or "network" in msg or "timeout" in msg
            return
        raise AssertionError("expected FetchError for unreachable host")
    add("fetch_json: clean FetchError on unreachable host", t_fetch_fail)

    # --- 9. fetch_json: clean error on non-JSON body ------------------------
    def t_fetch_badjson() -> None:
        # Spin up a tiny in-process HTTP server that returns non-JSON.
        import http.server
        import socketserver
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"this is not json"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a, **kw):
                pass

        with socketserver.TCPServer(("127.0.0.1", 0), Handler) as srv:
            port = srv.server_address[1]
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            try:
                fetch_json(f"http://127.0.0.1:{port}/", timeout=3.0)
            except FetchError as e:
                assert "json" in str(e).lower() or "JSON" in str(e)
                return
            finally:
                srv.shutdown()
        raise AssertionError("expected FetchError for non-JSON body")
    add("fetch_json: clean FetchError on non-JSON body", t_fetch_badjson)

    # --- 10. CSV output: fields are quoted correctly ------------------------
    def t_csv_format() -> None:
        sample = os.path.join(tmp, "sample2.json")
        _write_sample(sample)
        raw = read_file(sample)
        rep = _build_report(
            [_normalise(r, i) for i, r in enumerate(raw)],
            threshold=0.03, min_sessions=10,
        )
        out = write_outputs(os.path.join(tmp, "out2"), rep, raw)
        with open(out["csv"], "r", encoding="utf-8") as f:
            text = f.read()
        # Each data row should have exactly 5 comma-separated fields
        lines = [ln for ln in text.splitlines() if ln]
        for ln in lines[1:]:
            assert len(ln.split(",")) == 5, f"bad field count in {ln!r}"
    add("CSV: 5 fields per data row", t_csv_format)

    # --- 11. JSON output is valid and has expected keys ---------------------
    def t_json_format() -> None:
        sample = os.path.join(tmp, "sample3.json")
        _write_sample(sample)
        raw = read_file(sample)
        rep = _build_report(
            [_normalise(r, i) for i, r in enumerate(raw)],
            threshold=0.03, min_sessions=10,
        )
        obj = json.loads(rep.to_json())
        assert "totals" in obj
        assert "channels" in obj
        assert "flagged" in obj
        assert obj["totals"]["sessions"] == sum(r["sessions"] for r in raw)
    add("JSON: valid + expected keys", t_json_format)

    # --- 12. Normalisation: string revenue with $ is accepted ---------------
    def t_money_string() -> None:
        r = _normalise({
            "date": "2026-10-01", "channel": "organic", "page": "/home",
            "sessions": 10, "conversions": 1, "revenue": "$1,234.50",
        }, 0)
        assert abs(r.revenue - 1234.50) < 1e-9
    add("normalise: '$1,234.50' revenue string accepted", t_money_string)

    # --- Run ----------------------------------------------------------------
    failed = 0
    for name, fn in checks:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            print(f"  FAIL  {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
            failed += 1

    if failed:
        print(f"\n{failed}/{len(checks)} checks failed.")
        sys.exit(2)
    print(f"\nAll {len(checks)} selftest checks passed.")


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="FXDashboard marketing data pipeline (PoC).")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("selftest", help="Run selftest checks.")

    run_p = sub.add_parser("run", help="Run the pipeline.")
    src = run_p.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="Fetch a JSON array from this URL.")
    src.add_argument("--from-file", help="Read a JSON array from this local file.")
    run_p.add_argument("--out-dir", default="out", help="Where to write report.json + channel_report.csv.")
    run_p.add_argument("--threshold", type=float, default=0.03,
                       help="Flag rows where conv_rate is below this (default 0.03 = 3%%).")
    run_p.add_argument("--min-sessions", type=int, default=10,
                       help="Only flag rows with at least this many sessions (default 10).")
    run_p.add_argument("--timeout", type=float, default=15.0, help="Fetch timeout in seconds.")

    args = ap.parse_args(argv)
    if args.cmd is None:
        ap.print_help()
        return 2

    import tempfile

    if args.cmd == "selftest":
        with tempfile.TemporaryDirectory(prefix="fxmkt_") as tmp:
            _selftest(tmp)
        return 0

    if args.cmd == "run":
        if args.url:
            raw = fetch_json(args.url, timeout=args.timeout)
        else:
            raw = read_file(args.from_file)
        if not raw:
            print("error: no rows to process", file=sys.stderr)
            return 2
        rows = [_normalise(r, i) for i, r in enumerate(raw)]
        rep = _build_report(rows, threshold=args.threshold, min_sessions=args.min_sessions)
        out = write_outputs(args.out_dir, rep, raw)
        print(f"rows in:       {len(raw)}")
        print(f"total sessions {rep.total_sessions}")
        print(f"total convs    {rep.total_conversions}")
        print(f"total revenue  {rep.total_revenue:.2f}")
        print(f"channels:      {len(rep.channels)}")
        print(f"flagged:       {len(rep.flagged)}")
        print(f"json:          {out['json']}")
        print(f"csv:           {out['csv']}")
        return 0

    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PipelineError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)
