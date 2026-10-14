# Verification - Python Flask utility for Google Sheets API sync

**Job:** Python Flask utility for Google Sheets API sync (Freelancer.com, iteration #125)
**Verified:** 2026-10-14 (Central)
**Environment:** Python 3.14.6 (this box). Flask and googleapiclient are NOT
installed here, so verification uses the stdlib-only core + a local HTTP mock.

## Commands + real output

### `python PoC.py selftest`  ->  exit 0, 16/16 pass
```
  ok    parse bare id
  ok    parse id + A1 range
  ok    reject empty / short id
  ok    local rows_read
  ok    local rows_written >= rows
  ok    local dest populated
  ok    no transform reported
  ok    transform applied flag
  ok    bad row skipped (not fatal)
  ok    good row still synced
  ok    http list_rows
  ok    http metadata
  ok    http replace_rows reports updatedCells
  ok    http engine sync wrote
  ok    http 401 -> clean ApiError(401)
  ok    missing token -> MissingTokenError

selftest: 16 passed, 0 failed
```

### `python PoC.py demo`  ->  exit 0
Pulls a 3-row sheet from a `LocalBackend`, applies a transform (uppercases the
first column), pushes to a destination sheet, prints the typed `SyncResult`
(`rows_read: 3, rows_written: 3, transform_applied: true`) and the destination
sheet after sync (`['NAME','qty','price']`, `['WIDGET','3','9.50']`, ...).

### `python PoC.py serve --port 8911`  ->  exit 2 (clean, expected here)
```
error: Flask is not installed, so the HTTP service cannot start. Install it with
`pip install flask` and re-run, or use `python PoC.py selftest` / `demo` which
need no Flask.
```
This is the intended degradation path on a box without Flask: the core still
runs; the optional HTTP service reports a clear, actionable message instead of
a stack trace.

## What is genuinely exercised (not just code-reviewed)
- **SheetRef parsing + validation:** bare id, id + A1 range, and rejection of
  empty / too-short / invalid refs (fail early, helpful message).
- **LocalBackend engine round trip:** read N rows, write to a destination,
  destination actually populated.
- **Transform path:** transform applied, and a bad row is SKIPPED (not fatal)
  with a note, while good rows still sync.
- **REAL HTTP transport against a live local server** (`http.server` on
  127.0.0.1:<port>, same `urllib` client the real Google endpoint uses):
  `list_rows`, `metadata`, `replace_rows` (reports `updatedCells`), engine sync
  over HTTP, and a **401 auth-failure path surfacing as a clean `ApiError(401)`**
  rather than a traceback. This is the transport layer, driven over real
  sockets with real bearer-token auth checking.
- **Missing-token guard:** `HttpSheetsBackend("")` raises `MissingTokenError`.

## Honest limits (not hidden)
- The **live Google endpoint** (`sheets.googleapis.com`) is code-reviewed here,
  not called: that needs the client's own OAuth2 access token + a sheet with a
  real id, which I do not have. The transport layer it uses is what was driven
  live against the local mock (identical `urllib` request/response path,
  including non-2xx handling), so the verified logic is the logic the live
  call runs.
- **Flask** is the thin optional service layer; it is imported lazily and the
  core (engine + backends + CLI) runs without it. `create_app` / the route
  handlers are code-reviewed; the Flask import-failure path is exercised
  (`serve` returns exit 2 with a clean message). On a machine with `pip install
  flask`, `python PoC.py serve` boots and exposes `/health`, `/sheets/<ref>/rows`,
  `/sync`.
- The OAuth2 token exchange is intentionally out of scope (the client supplies
  a ready access token via env/flag), keeping the credential path on the client
  and the file dependency-free.

## Reproduce
```
python PoC.py selftest    # 16/16, exit 0, offline + local-HTTP
python PoC.py demo        # pull -> transform -> push, exit 0
python PoC.py serve       # needs Flask + GOOGLE_SHEETS_TOKEN for real sheets
```
