#!/usr/bin/env python3
"""
Python Flask utility for Google Sheets API sync
===============================================

A small, well-documented utility that syncs a range of rows from one Google
Sheet to another, with a thin Flask service in front so it can be triggered
over HTTP. Built for clarity and easy handoff (Python 3 only), with
lightweight error handling that fails with messages that help debug input
paths and API responses.

Design (swappable backend, stdlib core):
    - ``SheetsBackend``  protocol: list_rows / replace_rows / metadata.
    - ``HttpSheetsBackend``: the REAL Google Sheets API v4 client. Uses the
      standard library (urllib) only -- no third-party ``googleapiclient``
      dependency, so it installs anywhere and the transport is easy to audit.
      Auth is a bearer token (an OAuth2 access token the client provides).
    - ``LocalBackend``: an in-memory backend with the identical surface, used
      by the offline self-test and demo so the whole path is exercised with
      no network and no credentials.
    - ``SyncEngine``: pull a range from a source, optionally transform each
      row, and push the result to a destination. Returns a typed
      ``SyncResult``.
    - Flask app (``create_app``): ``GET /health``, ``POST /sync``,
      ``GET /sheets/<ref>/rows``. Imported lazily, so the core runs even where
      Flask is not installed (the ``serve`` command degrades with a clear
      message if Flask is missing).

CLI:
    python PoC.py selftest          # offline + local-HTTP verification
    python PoC.py demo              # pull -> transform -> push on LocalBackend
    python PoC.py serve --port 8899 # Flask service (needs Flask + a token)

Exit codes:
    0  success
    2  user / config error (bad ref, missing token, Flask not installed,
       bad HTTP response)
    3  unexpected internal error
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Protocol


# -----------------------------------------------------------------------------
# Errors
# -----------------------------------------------------------------------------

class SheetsError(Exception):
    """Base class for user-facing errors in this utility."""


class InvalidSheetRefError(SheetsError):
    """A sheet reference (spreadsheet id / A1 range) is malformed."""

    def __init__(self, ref: str, why: str = ""):
        self.ref = ref
        self.why = why
        super().__init__(f"invalid sheet reference {ref!r}" + (f": {why}" if why else ""))


class MissingTokenError(SheetsError):
    def __init__(self, hint: str = ""):
        super().__init__(
            "no Google access token available. Pass an OAuth2 access token "
            "(env GOOGLE_SHEETS_TOKEN or the --token flag)."
            + (f" Hint: {hint}" if hint else "")
        )


class ApiError(SheetsError):
    """The Google API answered with a non-2xx status or a parse failure."""

    def __init__(self, status: int, detail: str = ""):
        self.status = status
        self.detail = detail
        super().__init__(
            f"Google Sheets API error (HTTP {status})"
            + (f": {detail[:300]}" if detail else "")
        )


# -----------------------------------------------------------------------------
# Model
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class SheetRef:
    """A target to read/write: a spreadsheet id plus an A1 range (optional)."""

    spreadsheet_id: str
    range: Optional[str] = None  # e.g. "'Orders'!A1:C50"; None => first sheet

    def __str__(self) -> str:
        return self.spreadsheet_id + (f"#{self.range}" if self.range else "")

    @classmethod
    def parse(cls, text: str) -> "SheetRef":
        """Parse ``spreadsheet-id`` or ``spreadsheet-id#A1range``.

        Accepts either an A1 range after a ``#`` or a bare id. Validates the
        id shape (Google ids are 20+ alphanumeric chars) so typos fail early
        with a helpful message instead of a round-trip to the API.
        """
        if not text or not text.strip():
            raise InvalidSheetRefError(text or "", "empty reference")
        raw = text.strip()
        if "#" in raw:
            sid, rng = raw.split("#", 1)
            sid = sid.strip()
            rng = rng.strip() or None
        else:
            sid, rng = raw, None
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,}", sid):
            raise InvalidSheetRefError(
                raw,
                "spreadsheet id looks too short or contains invalid characters "
                "(expected a 20+ character Google spreadsheet id)",
            )
        if rng and not re.fullmatch(r"[\w!'\s,:]+", rng):
            raise InvalidSheetRefError(raw, f"malformed A1 range {rng!r}")
        return cls(sid, rng)


@dataclass
class SyncResult:
    """Typed outcome of a sync, suitable for JSON reporting over the API."""

    source: str
    destination: str
    rows_read: int
    rows_written: int
    transform_applied: bool
    elapsed_ms: int = 0
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "destination": self.destination,
            "rows_read": self.rows_read,
            "rows_written": self.rows_written,
            "transform_applied": self.transform_applied,
            "elapsed_ms": self.elapsed_ms,
            "notes": self.notes,
        }


# -----------------------------------------------------------------------------
# Backend protocol + implementations
# -----------------------------------------------------------------------------

Row = List[str]


class SheetsBackend(Protocol):
    """The minimal surface the sync engine needs from a Sheets provider."""

    def list_rows(self, ref: SheetRef) -> List[Row]: ...
    def replace_rows(self, ref: SheetRef, rows: List[Row]) -> int: ...
    def metadata(self, ref: SheetRef) -> dict: ...


class LocalBackend:
    """In-memory backend with the identical surface.

    Used for the offline self-test and demo so the engine path is fully
    exercised with no network and no credentials. Backed by a dict mapping
    ``spreadsheet_id`` to a list of rows (a single sheet per id is enough for
    the sync use case; a range's row/col window is honored when present).
    """

    def __init__(self, data: Optional[dict] = None):
        self.data: dict = data if data is not None else {}
        self.calls: List[tuple] = []  # audit trail for tests

    def list_rows(self, ref: SheetRef) -> List[Row]:
        self.calls.append(("list", ref))
        if ref.spreadsheet_id not in self.data:
            raise ApiError(404, f"spreadsheet {ref.spreadsheet_id} not found (local)")
        rows = self.data[ref.spreadsheet_id]
        if ref.range is None:
            return [list(r) for r in rows]
        lo, hi = _range_bounds(ref.range)
        return [list(r) for r in rows[lo:hi]]

    def replace_rows(self, ref: SheetRef, rows: List[Row]) -> int:
        self.calls.append(("replace", ref, len(rows)))
        # Replace the target window (whole sheet when no range is given).
        existing = self.data.get(ref.spreadsheet_id, [])
        if ref.range is None:
            self.data[ref.spreadsheet_id] = [list(r) for r in rows]
        else:
            lo, hi = _range_bounds(ref.range)
            merged = list(existing[:lo]) + [list(r) for r in rows]
            # Preserve any rows beyond the window so a partial replace is safe.
            merged = merged + list(existing[max(hi, lo + len(rows)):]) if hi > lo else merged
            self.data[ref.spreadsheet_id] = merged
        return len(rows)

    def metadata(self, ref: SheetRef) -> dict:
        self.calls.append(("meta", ref))
        if ref.spreadsheet_id not in self.data:
            raise ApiError(404, f"spreadsheet {ref.spreadsheet_id} not found (local)")
        rows = self.data[ref.spreadsheet_id]
        width = max((len(r) for r in rows), default=0)
        return {
            "spreadsheet_id": ref.spreadsheet_id,
            "rows": len(rows),
            "max_columns": width,
            "backend": "local",
        }


class HttpSheetsBackend:
    """REAL Google Sheets API v4 client, stdlib-only (no googleapiclient).

    Endpoints used:
        GET  /v4/spreadsheets/{id}/values/{range}          -> read a range
        PUT  /v4/spreadsheets/{id}/values/{range}?valueInputOption=RAW -> write
        GET  /v4/spreadsheets/{id}                          -> metadata

    Auth: an OAuth2 bearer token (the client's own). We do not do the OAuth
    exchange here; the service is handed a ready access token, which keeps the
    credential path on the client and keeps this file dependency-free.
    """

    BASE = "https://sheets.googleapis.com"

    def __init__(self, token: str, base_url: Optional[str] = None, timeout: float = 20.0):
        if not token:
            raise MissingTokenError()
        self.token = token
        self.base_url = (base_url or self.BASE).rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, url: str, body: Optional[dict] = None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace") if hasattr(e, "read") else ""
            raise ApiError(e.code, detail) from None
        except urllib.error.URLError as e:
            raise SheetsError(f"network error reaching Google Sheets API: {e.reason}") from None
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise ApiError(200, f"unparseable JSON body: {e}") from e

    def _enc(self, s: str) -> str:
        return urllib.parse.quote(s, safe="")

    def list_rows(self, ref: SheetRef) -> List[Row]:
        rng = ref.range or "A1:Z"
        url = f"{self.base_url}/v4/spreadsheets/{ref.spreadsheet_id}/values/{self._enc(rng)}"
        body = self._request("GET", url)
        values = body.get("values", [])
        # Normalize each row to a flat list of strings (Google returns mixed
        # types; the sync engine wants consistent strings for RAW writes).
        return [[("" if c is None else str(c)) for c in row] for row in values]

    def replace_rows(self, ref: SheetRef, rows: List[Row]) -> int:
        rng = ref.range or "A1:Z"
        url = (
            f"{self.base_url}/v4/spreadsheets/{ref.spreadsheet_id}/values/{self._enc(rng)}"
            f"?valueInputOption=RAW"
        )
        body = self._request("PUT", url, {"range": rng, "values": rows})
        updated = int(body.get("updates", {}).get("updatedCells", len(rows)))
        return updated

    def metadata(self, ref: SheetRef) -> dict:
        url = f"{self.base_url}/v4/spreadsheets/{ref.spreadsheet_id}"
        body = self._request("GET", url)
        sheets = body.get("sheets", [])
        prop = (sheets[0].get("sheetProperties", {}) if sheets else {})
        return {
            "spreadsheet_id": ref.spreadsheet_id,
            "title": body.get("properties", {}).get("title"),
            "first_sheet": prop.get("title"),
            "grid_rows": prop.get("gridProperties", {}).get("rowCount"),
            "backend": "http",
        }


# -----------------------------------------------------------------------------
# Engine
# -----------------------------------------------------------------------------

Transform = Callable[[Row], Row]


def _range_bounds(range_str: str):
    """Best-effort parse of an A1 range's first/last ROW into list bounds.

    Understands forms like ``A1:C5`` or ``'Orders'!A1:C5`` and returns
    ``(start, end)`` zero-based, half-open, for the row dimension. If it
    cannot determine rows, it returns ``(0, 0)`` meaning 'whole sheet' -- a
    conservative, safe default rather than a silent mis-trim.
    """
    m = re.search(r"[A-Za-z]+(\d+)", range_str)
    end_m = re.search(r"[A-Za-z]+(\d+)(?![\dA-Za-z])", range_str)
    try:
        if m and end_m:
            first = int(m.group(1))
            last = int(end_m.group(1))
            return (first - 1, last)
        if m:
            return (int(m.group(1)) - 1, 0)  # single row -> start only; end=0 => whole
    except (ValueError, IndexError):
        return (0, 0)
    return (0, 0)


class SyncEngine:
    """Pull a range from a source sheet, optionally transform, push to a dest."""

    def __init__(self, backend: SheetsBackend, transform: Optional[Transform] = None):
        self.backend = backend
        self.transform = transform

    def sync(self, source: SheetRef, destination: SheetRef) -> SyncResult:
        t0 = time.perf_counter()
        notes: List[str] = []
        rows = self.backend.list_rows(source)
        transform_applied = self.transform is not None
        if transform_applied:
            out: List[Row] = []
            for r in rows:
                try:
                    tr = self.transform(r)
                except Exception as e:  # a bad row should not abort the whole sync
                    notes.append(f"row skipped (transform error: {e!r})")
                    continue
                out.append(tr if tr is not None else r)
            rows = out
        written = self.backend.replace_rows(destination, rows)
        elapsed = int((time.perf_counter() - t0) * 1000)
        return SyncResult(
            source=str(source),
            destination=str(destination),
            rows_read=len(rows),
            rows_written=written,
            transform_applied=transform_applied,
            elapsed_ms=elapsed,
            notes=notes,
        )


# -----------------------------------------------------------------------------
# Flask app (lazy import -- core runs without Flask installed)
# -----------------------------------------------------------------------------

def create_app(engine: Optional[SyncEngine] = None):
    """Build the Flask service. Raises a clean SheetsError if Flask is missing."""
    try:
        from flask import Flask, jsonify, request  # lazy, optional
    except ImportError as e:  # pragma: no cover
        raise SheetsError(
            "Flask is not installed, so the HTTP service cannot start. "
            "Install it with `pip install flask` and re-run, or use "
            "`python PoC.py selftest` / `demo` which need no Flask."
        ) from e

    if engine is None:
        engine = _default_engine()

    app = Flask(__name__)

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "service": "google-sheets-sync"})

    @app.get("/sheets/<path:ref>/rows")
    def rows(ref: str):
        try:
            sr = SheetRef.parse(ref)
            return jsonify({"ref": str(sr), "rows": engine.backend.list_rows(sr)})
        except SheetsError as e:
            return jsonify({"error": str(e)}), 400

    @app.post("/sync")
    def sync():
        payload = request.get_json(silent=True) or {}
        try:
            source = SheetRef.parse(payload.get("source", ""))
            destination = SheetRef.parse(payload.get("destination", ""))
            result = engine.sync(source, destination)
            return jsonify(result.to_dict())
        except SheetsError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:  # defensive: never leak a stack trace to the client
            return jsonify({"error": f"internal sync error: {e!r}"}), 500

    return app


def _default_engine() -> SyncEngine:
    """Pick a backend for ``serve``: real HTTP if a token is present, else a
    clearly-labeled in-memory one (so the service still boots and is testable)."""
    token = os.environ.get("GOOGLE_SHEETS_TOKEN", "").strip()
    if token:
        return SyncEngine(HttpSheetsBackend(token))
    return SyncEngine(LocalBackend())


# -----------------------------------------------------------------------------
# CLI + self-test
# -----------------------------------------------------------------------------

def _demo() -> int:
    src_id = "srcspreadsheetid1234567890"
    dst_id = "dstspreadsheetid1234567890"
    backend = LocalBackend({
        src_id: [
            ["name", "qty", "price"],
            ["widget", "3", "9.50"],
            ["gadget", "1", "24.00"],
        ],
    })

    def upper_name(row: Row) -> Row:
        # A tiny real transform: uppercase the first column, keep the rest.
        if not row:
            return row
        return [row[0].upper() if isinstance(row[0], str) else row[0]] + list(row[1:])

    engine = SyncEngine(backend, transform=upper_name)
    res = engine.sync(
        SheetRef(src_id),
        SheetRef(dst_id),
    )
    print("demo sync result:")
    print(json.dumps(res.to_dict(), indent=2))
    print("\ndestination sheet after sync:")
    for r in backend.list_rows(SheetRef(dst_id)):
        print("  ", r)
    return 0


def _selftest() -> int:
    passed = 0
    failed = 0

    def check(name: str, cond: bool, detail: str = ""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  ok    {name}")
        else:
            failed += 1
            print(f"  FAIL  {name}" + (f"  ({detail})" if detail else ""))

    # --- parse / validation -------------------------------------------------
    r = SheetRef.parse("abcdefghijklmnopqrstuvwx")
    check("parse bare id", r.spreadsheet_id == "abcdefghijklmnopqrstuvwx" and r.range is None)
    r = SheetRef.parse("abcdefghijklmnopqrstuvwx#'Orders'!A1:C5")
    check("parse id + A1 range", r.range == "'Orders'!A1:C5")
    bad = 0
    for b in ["", "short", "abc!A1"]:
        try:
            SheetRef.parse(b)
        except SheetsError:
            bad += 1
    check("reject empty / short id", bad == 3)

    # --- local engine round trip --------------------------------------------
    src = "localsrcidlocalsrcid12345"
    dst = "localdstidlocaldstid12345"
    backend = LocalBackend({
        src: [["h1", "h2"], ["a", "1"], ["b", "2"], ["c", "3"]],
    })
    eng = SyncEngine(backend)
    res = eng.sync(SheetRef(src), SheetRef(dst))
    check("local rows_read", res.rows_read == 4, f"got {res.rows_read}")
    check("local rows_written >= rows", res.rows_written >= 4, f"got {res.rows_written}")
    check("local dest populated", len(backend.list_rows(SheetRef(dst))) == 4)
    check("no transform reported", res.transform_applied is False)

    # --- transform path (incl. a bad row that is skipped, not fatal) --------
    def bad_transform(row: Row) -> Row:
        if row and row[0] == "BOMB":
            raise ValueError("simulated bad row")
        return [x.upper() for x in row]
    src2 = "localsrcidlocalsrcid99999"
    dst2 = "localdstidlocaldstid99999"
    backend2 = LocalBackend({src2: [["BOMB", "x"], ["keep", "me"]]})
    eng2 = SyncEngine(backend2, transform=bad_transform)
    res2 = eng2.sync(SheetRef(src2), SheetRef(dst2))
    check("transform applied flag", res2.transform_applied is True)
    check("bad row skipped (not fatal)", any("skipped" in n for n in res2.notes),
          f"notes={res2.notes}")
    check("good row still synced", res2.rows_read == 1, f"got {res2.rows_read}")

    # --- HTTP backend against a LOCAL server (real network path, offline) ----
    import threading
    import http.server

    captured = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):  # silence
            pass

        def _json(self, code: int, obj: dict):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            # 401 on a wrong bearer token, mirroring real Google behavior.
            if self.headers.get("Authorization") != "Bearer goodtoken":
                self._json(401, {"error": {"code": 401, "message": "invalid token"}})
                return
            # Route by the values/ sub-path, not a specific id: any spreadsheet
            # id with a /values/ range returns the values payload; a bare
            # spreadsheet id returns metadata.
            if "/values/" in self.path:
                self._json(200, {"values": [["n", "p"], ["a", "10"], ["b", "20"]]})
            elif self.path.startswith("/v4/spreadsheets/"):
                self._json(200, {
                    "properties": {"title": "T"},
                    "sheets": [{"sheetProperties": {"title": "S1",
                                                   "gridProperties": {"rowCount": 3}}}
                ]})
            else:
                self._json(404, {"error": {"message": "nope"}})

        def do_PUT(self):
            if self.headers.get("Authorization") != "Bearer goodtoken":
                self._json(401, {"error": {"code": 401, "message": "invalid token"}})
                return
            ln = int(self.headers.get("Content-Length", 0))
            _ = self.rfile.read(ln)
            self._json(200, {"updates": {"updatedCells": 6}})

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    try:
        hb = HttpSheetsBackend("goodtoken", base_url=f"http://127.0.0.1:{port}")
        rows = hb.list_rows(SheetRef("httpspreadsheetsheetid12345"))
        check("http list_rows", rows[0] == ["n", "p"] and len(rows) == 3, f"got {rows}")
        meta = hb.metadata(SheetRef("httpspreadsheetsheetid12345"))
        check("http metadata", meta.get("title") == "T" and meta.get("first_sheet") == "S1")
        written = hb.replace_rows(SheetRef("httpspreadsheetsheetid12345"), [["a"], ["b"]])
        check("http replace_rows reports updatedCells", written == 6, f"got {written}")
        # Engine over the HTTP backend.
        hsrc = "httpspreadsheetsheetid12345"
        hdst = "httpdestspreadsheetid12345"
        heng = SyncEngine(hb)
        hres = heng.sync(SheetRef(hsrc), SheetRef(hdst))
        check("http engine sync wrote", hres.rows_written == 6 and hres.rows_read == 3)
        # Auth failure surfaces as a clean ApiError, not a stack trace.
        badb = HttpSheetsBackend("wrongtoken", base_url=f"http://127.0.0.1:{port}")
        auth_err = 0
        try:
            badb.list_rows(SheetRef("httpspreadsheetsheetid12345"))
        except ApiError as e:
            auth_err = e.status
        check("http 401 -> clean ApiError(401)", auth_err == 401, f"got {auth_err}")
        # Missing token raises the right error type.
        tok_err = 0
        try:
            HttpSheetsBackend("")
        except MissingTokenError:
            tok_err = 1
        check("missing token -> MissingTokenError", tok_err == 1)
    finally:
        server.shutdown()

    print(f"\nselftest: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 2


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1]
    if cmd == "selftest":
        return _selftest()
    if cmd == "demo":
        return _demo()
    if cmd == "serve":
        try:
            app = create_app()
        except SheetsError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        port = 8899
        if "--port" in argv:
            port = int(argv[argv.index("--port") + 1])
        app.run(host="127.0.0.1", port=port, debug=False)
        return 0
    print(f"unknown command: {cmd} (expected selftest | demo | serve)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except SheetsError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:  # pragma: no cover
        print(f"internal error: {e!r}", file=sys.stderr)
        sys.exit(3)
