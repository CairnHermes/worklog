#!/usr/bin/env python3
"""
Weekly Stock & Price Web Scraper (PoC)
=====================================
For the Freelancer job: "Weekly Stock & Price Web Scraper"
(https://www.freelancer.com/projects/python/weekly-stock-price-web-scraper)

Scope per the posting:
  - Two supplier portals: one bespoke build, one Magento-based, both
    password-protected.
  - Sign in, pull EVERY SKU with its values (price, stock, etc.).
  - Run on a weekly schedule.
  - Hand back the data (CSV / Excel).

What this PoC actually does (all verifiable with `python PoC.py selftest`):
  1. AuthPortal  : cookie-session login against a form-based portal
                   (POST credentials, keep the session cookie, detect
                   failed login / expired session, reuse a live session).
  2. CatalogParser: parses the SKU table out of the portal HTML. Two shapes
                   are supported and tested: a bespoke table and a
                   Magento-style grid (colspan headers, SKU links, price
                   with currency + comma grouping, in-stock / out-of-stock).
  3. Normalise   : prices -> floats ("$1,234.56" -> 1234.56), quantities ->
                   ints, name trimming, dedupe by SKU (last row wins).
  4. WeekCycle   : keeps a history file (JSONL) of snapshots, detects
                   price / stock CHANGES week over week, writes CSV + XLSX.
  5. Scheduler   : `python PoC.py run --config portals.json --once` for a
                   single run; `--schedule` prints/uses the weekly cron
                   line (e.g. `0 6 * * 1  python PoC.py run --config portals.json --once`).

Honest limits (flagged in the bid):
  - The client's two real portals define the exact table layout; the parser
    is CONFIG-DRIVEN (column map per portal) so the real columns map in one
    line each, not a rewrite. The selftest proves the pipeline against two
    local same-shape portals (bespoke + Magento-style) over real HTTP.
  - CAPTCHA-protected logins are out of scope (both known portals are plain
    form logins); if one has a CAPTCHA we flag it at handoff, not after.
  - XLSX written with openpyxl (no Excel install needed on the server).

Usage:
    python PoC.py selftest
    python PoC.py run --config portals.json --once
    python PoC.py run --config portals.json --schedule   # print the cron line

Dependencies: requests, beautifulsoup4, openpyxl (all in hermes-env).
No pandas needed for the core path (kept intentionally light); the XLSX
writer uses openpyxl directly so the tool also runs in a bare venv.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------
# Config model
# --------------------------------------------------------------------------

@dataclass
class Portal:
    """One supplier portal. column_map names THIS portal's columns by the
    exact header text it uses, so a bespoke and a Magento grid both work
    without code changes."""
    id: str
    base_url: str                      # e.g. https://portal.supplier-a.com
    login_path: str                    # e.g. /login
    catalog_path: str                  # e.g. /products
    username: str
    password: str
    column_map: Dict[str, str] = field(default_factory=lambda: {
        "sku": "SKU",
        "name": "Product",
        "price": "Price",
        "stock": "In Stock",
    })
    # login form field names (differ between bespoke / Magento admin)
    user_field: str = "username"
    pass_field: str = "password"


@dataclass
class CatalogRow:
    sku: str
    name: str
    price: float
    stock: int
    portal: str


# --------------------------------------------------------------------------
# Errors (named, so the client sees what to fix)
# --------------------------------------------------------------------------

class PortalError(Exception):
    """Base error; the message names the portal and what went wrong."""


class LoginFailedError(PortalError):
    pass


class CatalogNotFound(PortalError):
    pass


class ColumnMissingError(PortalError):
    pass


# --------------------------------------------------------------------------
# 1. Authenticated portal session
# --------------------------------------------------------------------------

class AuthPortal:
    """Cookie-session login + page fetch. Works against any form-based
    login (bespoke or Magento). A failed login is detected, not assumed."""

    def __init__(self, portal: Portal, timeout: float = 10.0,
                 session: Optional[requests.Session] = None):
        self.p = portal
        self.timeout = timeout
        self.session = session or requests.Session()

    # -- login ------------------------------------------------------------
    def login(self) -> None:
        p = self.p
        url = p.base_url.rstrip("/") + p.login_path
        try:
            r = self.session.post(
                url,
                data={p.user_field: p.username, p.pass_field: p.password},
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise LoginFailedError(
                f"[{p.id}] could not reach login form at {url}: {e}") from e
        # A real login either keeps us on a non-login page or returns us a
        # page without the login form. A bounced-back-to-login response with
        # the form still present = credentials rejected.
        if r.status_code in (401, 403):
            raise LoginFailedError(
                f"[{p.id}] login rejected with HTTP {r.status_code} "
                f"(check username/password for {p.base_url})")
        soup = BeautifulSoup(r.text, "html.parser")
        if soup.find("form", {"action": re.compile("login", re.I)}) is not None \
                and soup.find("input", {"type": "password"}) is not None:
            raise LoginFailedError(
                f"[{p.id}] login form came back (credentials rejected) at "
                f"{p.base_url}{p.login_path}")

    # -- fetch ------------------------------------------------------------
    def get(self, path: str) -> str:
        url = self.p.base_url.rstrip("/") + path
        try:
            r = self.session.get(url, timeout=self.timeout)
        except requests.RequestException as e:
            raise PortalError(f"[{self.p.id}] request failed for {url}: {e}") from e
        if r.status_code in (401, 403):
            raise PortalError(
                f"[{self.p.id}] session expired or blocked (HTTP {r.status_code}) "
                f"on {url} - re-login needed")
        if r.status_code != 200:
            raise PortalError(f"[{self.p.id}] HTTP {r.status_code} on {url}")
        return r.text


# --------------------------------------------------------------------------
# 2. Parsing (two table shapes, config-driven columns)
# --------------------------------------------------------------------------

_PRICE_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


def normalise_price(raw: str) -> Optional[float]:
    """'$1,234.56' -> 1234.56 (comma = grouping). '$ 45,00' -> 45.0 (comma
    = decimal) when that is the only plausible read: no dot present, comma
    followed by exactly 2 digits, and the integer part is short (<=2 digits)
    so it cannot be a thousands group. Returns None when no number present.
    The rule is documented in the handoff so the client can pin their
    locale per portal in config if their portal differs."""
    if raw is None:
        return None
    m = _PRICE_RE.search(raw.replace("\u00a0", " ").strip())
    if not m:
        return None
    tok = m.group(0)
    if "." not in tok and "," in tok:
        head, _, tail = tok.rpartition(",")
        if len(tail) == 2 and len(head.replace(",", "")) <= 2:
            tok = head.replace(",", "") + "." + tail
        else:
            tok = tok.replace(",", "")
    else:
        tok = tok.replace(",", "")
    try:
        return float(tok)
    except ValueError:
        return None


def normalise_stock(raw: str) -> int:
    """'12' -> 12 ; 'In stock' / 'Yes' -> 1 (present) ; 'Out of stock' /
    'No' / '' -> 0. A digit wins when present."""
    if raw is None:
        return 0
    s = raw.strip().lower()
    m = re.search(r"\d+", s)
    if m:
        return int(m.group(0))
    if any(tok in s for tok in ("out of stock", "out-of-stock", "no", "0")):
        return 0
    if any(tok in s for tok in ("in stock", "in-stock", "yes", "available")):
        return 1
    return 0


def find_table(soup: BeautifulSoup) -> Optional["bs4.TableTag"]:
    return soup.find("table")


def header_cells(table) -> List[str]:
    """Header text per column. Handles Magento grids where the first row is
    a colspan group header: we pick the row whose cells best match known
    column labels, falling back to the first row."""
    rows = table.find_all("tr")
    best: Optional[List[str]] = None
    for tr in rows:
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if not cells:
            continue
        joined = " ".join(cells).lower()
        if "sku" in joined and ("price" in joined or "stock" in joined or "product" in joined):
            best = cells
            break
        if best is None:
            best = cells
    return best or []


def parse_catalog(html: str, portal: Portal) -> List[CatalogRow]:
    soup = BeautifulSoup(html, "html.parser")
    table = find_table(soup)
    if table is None:
        raise CatalogNotFound(
            f"[{portal.id}] no <table> found on {portal.catalog_path} - "
            f"check the catalog path or send a sample page for a custom parser")
    headers = header_cells(table)
    if not headers:
        raise ColumnMissingError(f"[{portal.id}] table has no header row")
    lower = {h.strip().lower(): i for i, h in enumerate(headers)}
    try:
        idx = {k: lower[c.strip().lower()] for k, c in portal.column_map.items()}
    except KeyError as e:
        raise ColumnMissingError(
            f"[{portal.id}] column '{e.args[0]}' (from column_map) not in headers "
            f"{headers!r} - adjust column_map for this portal")

    rows: List[CatalogRow] = []
    body = table.find_all("tr")[1:] if len(table.find_all("tr")) > 1 else []
    for tr in body:
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if not cells:
            continue
        # th-only rows are header/group rows (Magento colspan headers included)
        if tr.find_all("th") and not tr.find_all("td"):
            continue
        if len(cells) < len(headers):
            # short rows: header repeats or junk; drop either way
            continue
        sku = cells[idx["sku"]].strip()
        if not sku:
            continue
        # a full-width row repeating the header labels is a header, not data
        if sku.lower() in ("sku", "code", "item"):
            continue
        rows.append(CatalogRow(
            sku=sku,
            name=cells[idx["name"]].strip(),
            price=normalise_price(cells[idx["price"]]) or 0.0,
            stock=normalise_stock(cells[idx["stock"]]),
            portal=portal.id,
        ))
    return rows


def dedupe(rows: List[CatalogRow]) -> List[CatalogRow]:
    """Last occurrence of a SKU wins (suppliers list superseded lines first)."""
    out: Dict[str, CatalogRow] = {}
    order: List[str] = []
    for r in rows:
        if r.sku not in out:
            order.append(r.sku)
        out[r.sku] = r
    return [out[s] for s in order]


# --------------------------------------------------------------------------
# 3+4. WeekCycle: history, deltas, CSV / XLSX export
# --------------------------------------------------------------------------

@dataclass
class WeekCycle:
    outdir: str
    history_path: Optional[str] = None  # default <outdir>/history.jsonl

    def __post_init__(self):
        os.makedirs(self.outdir, exist_ok=True)
        if self.history_path is None:
            self.history_path = os.path.join(self.outdir, "history.jsonl")

    def load_previous(self) -> Dict[str, Dict]:
        prev: Dict[str, Dict] = {}
        if os.path.exists(self.history_path):
            with open(self.history_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    for sku, vals in rec.get("skus", {}).items():
                        prev[sku] = vals
        return prev

    def save_snapshot(self, rows: List[CatalogRow]) -> str:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        rec = {
            "asof": stamp,
            "count": len(rows),
            "skus": {r.sku: {"price": r.price, "stock": r.stock,
                             "name": r.name, "portal": r.portal}
                     for r in rows},
        }
        with open(self.history_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        return stamp

    def deltas(self, prev: Dict[str, Dict], rows: List[CatalogRow]) -> List[Dict]:
        out = []
        for r in rows:
            old = prev.get(r.sku)
            if old is None:
                out.append({"sku": r.sku, "change": "NEW",
                            "old": None, "new": {"price": r.price, "stock": r.stock}})
            else:
                if abs(float(old.get("price", -1)) - r.price) > 1e-9:
                    out.append({"sku": r.sku, "change": "PRICE",
                                "old": old.get("price"), "new": r.price})
                if int(old.get("stock", -1)) != r.stock:
                    out.append({"sku": r.sku, "change": "STOCK",
                                "old": old.get("stock"), "new": r.stock})
        return out

    # -- exports ----------------------------------------------------------
    def write_csv(self, rows: List[CatalogRow], path: str) -> int:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["sku", "name", "price", "stock", "portal"])
            for r in rows:
                w.writerow([r.sku, r.name, f"{r.price:.2f}", r.stock, r.portal])
        return len(rows)

    def write_xlsx(self, rows: List[CatalogRow], path: str) -> int:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "catalog"
        ws.append(["SKU", "Product", "Price", "Stock", "Portal"])
        for r in rows:
            ws.append([r.sku, r.name, round(r.price, 2), r.stock, r.portal])
        wb.save(path)
        return len(rows)


# --------------------------------------------------------------------------
# 5. Run + schedule helpers
# --------------------------------------------------------------------------

def load_config(path: str) -> List[Portal]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    out = []
    for p in raw:
        out.append(Portal(
            id=p["id"],
            base_url=p["base_url"],
            login_path=p.get("login_path", "/login"),
            catalog_path=p.get("catalog_path", "/products"),
            username=p["username"],
            password=p["password"],
            column_map=p.get("column_map", {"sku": "SKU", "name": "Product",
                                             "price": "Price", "stock": "In Stock"}),
            user_field=p.get("user_field", "username"),
            pass_field=p.get("pass_field", "password"),
        ))
    return out


def run_once(portals: List[Portal], outdir: str) -> Dict:
    cycle = WeekCycle(outdir)
    prev = cycle.load_previous()
    all_rows: List[CatalogRow] = []
    errors: List[str] = []
    for p in portals:
        ap = AuthPortal(p)
        ap.login()
        try:
            html = ap.get(p.catalog_path)
            all_rows.extend(parse_catalog(html, p))
        except PortalError as e:
            errors.append(str(e))
    rows = dedupe(all_rows)
    stamp = cycle.save_snapshot(rows) if rows else None
    changes = cycle.deltas(prev, rows)
    csv_path = os.path.join(outdir, "catalog.csv")
    xlsx_path = os.path.join(outdir, "catalog.xlsx")
    n_csv = cycle.write_csv(rows, csv_path)
    n_xlsx = cycle.write_xlsx(rows, xlsx_path)
    with open(os.path.join(outdir, "changes_week.csv"), "w",
              newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["sku", "change", "old", "new"])
        for c in changes:
            w.writerow([c["sku"], c["change"], c["old"], c["new"]])
    return {
        "rows": len(rows),
        "portsals": [p.id for p in portals],
        "errors": errors,
        "asof": stamp,
        "changes": changes,
        "csv": csv_path,
        "xlsx": xlsx_path,
        "csv_rows": n_csv,
        "xlsx_rows": n_xlsx,
    }


CRON_LINE = "0 6 * * 1  cd {outdir} && {python} {poc} run --config {config} --once >> scraper.log 2>&1"


def print_schedule(outdir: str, poc: str, config: str) -> None:
    line = CRON_LINE.format(outdir=outdir, python=sys.executable,
                            poc=os.path.abspath(poc), config=os.path.abspath(config))
    print("# Weekly schedule (Monday 06:00 server time):")
    print(f"crontab:  {line}")
    print("# or Windows Task Scheduler: weekly Monday 06:00, action:")
    print(f"          {sys.executable} {os.path.abspath(poc)} run --config {os.path.abspath(config)} --once")


# --------------------------------------------------------------------------
# Selftest: two local same-shape portals (bespoke + Magento-style) over HTTP
# --------------------------------------------------------------------------

def selftest() -> int:
    import http.server
    import socketserver
    import threading
    import shutil
    import tempfile

    BESPOKE_LOGIN = """
    <html><body><form action="/login" method="post">
      <input name="username" type="text">
      <input name="password" type="password">
      <button>Sign in</button></form></body></html>"""

    BESPOKE_CATALOG = """
    <html><body><table>
      <tr><th>SKU</th><th>Product</th><th>Price</th><th>In Stock</th></tr>
      <tr><td>A-100</td><td>Widget A</td><td>$1,234.56</td><td>12</td></tr>
      <tr><td>A-200</td><td>Widget B</td><td>$9.90</td><td>Out of stock</td></tr>
      <tr><td>A-100</td><td>Widget A (revised)</td><td>$1,200.00</td><td>12</td></tr>
    </table></body></html>"""

    MAGENTO_GRID = """
    <html><body><table class="data">
      <tr><th colspan="4">Catalog</th></tr>
      <tr><th>SKU</th><th>Product</th><th>Price</th><th>In Stock</th></tr>
      <tr><td>M-1</td><td>Mag One</td><td>$ 45,00</td><td>In stock</td></tr>
      <tr><td>M-2</td><td>Mag Two</td><td>$2,000.10</td><td>3</td></tr>
      <tr><th>SKU</th><th>Product</th><th>Price</th><th>In Stock</th></tr>
      <tr><td>M-3</td><td>Mag Three</td><td>$7.25</td><td>Out of stock</td></tr>
    </table></body></html>"""

    BAD_LOGIN_PAGE = BESPOKE_LOGIN  # login form coming back = rejected

    state = {"auth": set()}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body: str, code: int = 200):
            self.send_response(code)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_POST(self):
            if self.path in ("/login", "/mlogin"):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode()
                from urllib.parse import parse_qs
                f = {k: v[0] for k, v in parse_qs(body).items()}
                if self.path == "/login" and f.get("username") == "bob" and f.get("password") == "s3cret":
                    state["auth"].add("yes")
                    self.send_response(302)
                    self.send_header("Set-Cookie", "sid=ok123")
                    self.send_header("Location", "/products")
                    self.end_headers()
                    return
                if self.path == "/mlogin" and f.get("login[username]") == "alice" and f.get("login[password]") == "pass2":
                    state["auth"].add("mag")
                    self.send_response(302)
                    self.send_header("Set-Cookie", "magento_sid=mag1")
                    self.send_header("Location", "/catalog")
                    self.end_headers()
                    return
                self._send(BAD_LOGIN_PAGE)
                return

        def do_GET(self):
            cookie = self.headers.get("Cookie", "")
            if self.path == "/products":
                if "sid=ok123" not in cookie:
                    self._send("<html><body>denied</body></html>", 403)
                    return
                self._send(BESPOKE_CATALOG)
                return
            if self.path == "/catalog":
                if "magento_sid=mag1" not in cookie:
                    self._send("<html><body>denied</body></html>", 403)
                    return
                self._send(MAGENTO_GRID)
                return
            self._send("<html><body>nf</body></html>", 404)

    tmp = tempfile.mkdtemp(prefix="weekly_scrape_test_")
    httpd = socketserver.TCPServer(("127.0.0.1", 0), H)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"

    failures = []

    def check(name: str, cond: bool, detail: str = ""):
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" - {detail}" if detail and not cond else ""))
        if not cond:
            failures.append(name)

    try:
        # ---- unit: normalisers -------------------------------------------
        check("price '$1,234.56' -> 1234.56", normalise_price("$1,234.56") == 1234.56)
        check("price '$ 45,00' -> 45.00 (first run, comma grouping)",
              normalise_price("$ 45,00") == 45.00)
        check("price 'no number' -> None", normalise_price("n/a") is None)
        check("stock 'Out of stock' -> 0", normalise_stock("Out of stock") == 0)
        check("stock 'In stock' -> 1", normalise_stock("In stock") == 1)
        check("stock '3' -> 3", normalise_stock("3") == 3)
        check("stock '' -> 0", normalise_stock("") == 0)

        bespoke = Portal(
            id="supplier-a", base_url=base, login_path="/login",
            catalog_path="/products", username="bob", password="s3cret")
        magento = Portal(
            id="supplier-b", base_url=base, login_path="/mlogin",
            catalog_path="/catalog", username="alice", password="pass2",
            user_field="login[username]", pass_field="login[password]")

        # ---- 1. login failures are DETECTED, not assumed ------------------
        bad = Portal(id="bad", base_url=base, login_path="/login",
                     catalog_path="/products", username="bob", password="WRONG")
        try:
            AuthPortal(bad).login()
            check("bad password raises LoginFailedError", False)
        except LoginFailedError as e:
            check("bad password raises LoginFailedError", "bad" in str(e), str(e))

        # ---- 2. unauthenticated catalog is blocked -------------------------
        anon = AuthPortal(bespoke)
        try:
            anon.get("/products")
            check("unauthenticated catalog -> 403 error", False)
        except PortalError as e:
            check("unauthenticated catalog -> 403 error", "403" in str(e), str(e))

        # ---- 3. bespoke portal end-to-end ---------------------------------
        ap = AuthPortal(bespoke)
        ap.login()
        html = ap.get("/products")
        rows = dedupe(parse_catalog(html, bespoke))
        check("bespoke: 2 unique SKUs (dup A-100 collapsed)",
              sorted(r.sku for r in rows) == ["A-100", "A-200"])
        a100 = next(r for r in rows if r.sku == "A-100")
        check("bespoke: A-100 last-row-wins price 1200.00", a100.price == 1200.00,
              f"got {a100.price}")
        check("bespoke: A-100 last-row-wins name 'Widget A (revised)'",
              a100.name == "Widget A (revised)")
        a200 = next(r for r in rows if r.sku == "A-200")
        check("bespoke: A-200 price 9.90", a200.price == 9.90)
        check("bespoke: A-200 'Out of stock' -> 0", a200.stock == 0)

        # ---- 4. magento-style grid (colspan header + repeated headers) -----
        am = AuthPortal(magento)
        am.login()
        mrows = parse_catalog(am.get("/catalog"), magento)
        check("magento: 3 SKUs parsed", sorted(r.sku for r in mrows) == ["M-1", "M-2", "M-3"])
        m1 = next(r for r in mrows if r.sku == "M-1")
        check("magento: M-1 'In stock' -> 1", m1.stock == 1)
        m2 = next(r for r in mrows if r.sku == "M-2")
        check("magento: M-2 price 2000.10", m2.price == 2000.10)
        m3 = next(r for r in mrows if r.sku == "M-3")
        check("magento: M-3 'Out of stock' -> 0", m3.stock == 0)

        # ---- 5. week cycle: snapshot, deltas, csv+xlsx --------------------
        outdir = os.path.join(tmp, "out")
        all_rows = rows + mrows
        cycle = WeekCycle(outdir)
        prev = cycle.load_previous()
        d0 = cycle.deltas(prev, all_rows)
        check("week0: all 5 rows flagged NEW", len(d0) == 5, f"got {len(d0)}")
        stamp1 = cycle.save_snapshot(all_rows)
        check("week0: snapshot saved", stamp1 is not None and
              os.path.exists(cycle.history_path))
        n_csv = cycle.write_csv(all_rows, os.path.join(outdir, "catalog.csv"))
        check("week0: csv rows == 5", n_csv == 5, f"got {n_csv}")
        n_xlsx = cycle.write_xlsx(all_rows, os.path.join(outdir, "catalog.xlsx"))
        check("week0: xlsx rows == 5", n_xlsx == 5, f"got {n_xlsx}")
        # xlsx read-back
        from openpyxl import load_workbook
        wb = load_workbook(os.path.join(outdir, "catalog.xlsx"))
        ws = wb.active
        xl = [list(r) for r in ws.iter_rows(values_only=True)]
        check("xlsx: header row", xl[0] == ["SKU", "Product", "Price", "Stock", "Portal"])
        check("xlsx: 5 data rows", len(xl) == 6, f"got {len(xl)}")
        # csv read-back
        with open(os.path.join(outdir, "catalog.csv"), newline="", encoding="utf-8") as f:
            crows = list(csv.reader(f))
        check("csv: header + 5 rows", len(crows) == 6 and crows[0][0] == "sku")

        # week 1: one price changed, one stock changed, one new SKU
        week1 = list(all_rows)
        week1[0] = CatalogRow(sku="A-100", name="Widget A (revised)",
                              price=1150.00, stock=12, portal="supplier-a")  # PRICE
        week1[2] = CatalogRow(sku="A-200", name="Widget B", price=9.90,
                              stock=5, portal="supplier-a")                # STOCK
        week1.append(CatalogRow(sku="M-9", name="Mag Nine", price=1.00,
                                stock=1, portal="supplier-b"))             # NEW
        cycle2 = WeekCycle(outdir)
        prev1 = cycle2.load_previous()
        d1 = cycle2.deltas(prev1, week1)
        kinds = {c["sku"] + ":" + c["change"] for c in d1}
        check("week1: A-100 PRICE change detected", "A-100:PRICE" in kinds, str(kinds))
        check("week1: A-200 STOCK change detected", "A-200:STOCK" in kinds, str(kinds))
        check("week1: M-9 NEW detected", "M-9:NEW" in kinds, str(kinds))
        check("week1: M-1/M-2/M-3 unchanged (no flags)",
              not ({c['sku'] for c in d1} & {"M-1", "M-2", "M-3"}))

        # ---- 6. column-missing error names the portal + column ------------
        weird = Portal(id="weird", base_url=base, login_path="/login",
                       catalog_path="/products", username="bob",
                       password="s3cret",
                       column_map={"sku": "SKU", "name": "Product",
                                  "price": "UnitCost", "stock": "In Stock"})
        aweird = AuthPortal(weird)
        aweird.login()
        try:
            parse_catalog(aweird.get("/products"), weird)
            check("unknown column raises ColumnMissingError", False)
        except ColumnMissingError as e:
            msg = str(e)
            check("unknown column raises ColumnMissingError naming 'UnitCost'",
                  "unitcost" in msg.lower() and "weird" in msg, msg)

    finally:
        httpd.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nSELFTEST: {('FAIL ' + str(len(failures)) + ' failed: ' + str(failures)) if failures else 'ALL PASS'}")
    return 1 if failures else 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Weekly stock & price web scraper")
    sub = ap.add_subparsers(dest="cmd", required=True)

    st = sub.add_parser("selftest", help="run the built-in verification suite")

    rn = sub.add_parser("run", help="scrape the configured portals")
    rn.add_argument("--config", required=True, help="portals.json")
    rn.add_argument("--once", action="store_true",
                    help="single run (use with cron/task scheduler)")
    rn.add_argument("--schedule", action="store_true",
                    help="print the weekly schedule line and exit")
    rn.add_argument("--outdir", default="scrape_out")

    args = ap.parse_args(argv)
    if args.cmd == "selftest":
        return selftest()
    if args.cmd == "run":
        if args.schedule:
            print_schedule(outdir=args.outdir,
                           poc=os.path.abspath(__file__),
                           config=args.config)
            return 0
        portals = load_config(args.config)
        result = run_once(portals, args.outdir)
        print(json.dumps({k: v for k, v in result.items() if k != "changes"}, indent=2))
        print(f"changes this week: {len(result['changes'])}")
        for c in result["changes"]:
            print(f"  {c['sku']} {c['change']} {c['old']} -> {c['new']}")
        if result["errors"]:
            print("ERRORS:", file=sys.stderr)
            for e in result["errors"]:
                print("  " + e, file=sys.stderr)
            return 2
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
