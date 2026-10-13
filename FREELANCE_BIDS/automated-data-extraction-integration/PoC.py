#!/usr/bin/env python3
"""
automated-data-extraction-integration - PoC
===========================================
A config-driven "extract from N third-party sites -> serve through a clean
JSON API" pipeline. That is the shape of the job: a reliable automated
data-extraction system for 8 third-party websites, with the results exposed
through an API so the client's website / plugin can consume them.

Design (deliberately dependency-light, Python stdlib + requests):
  * EXTRACTOR  - reads a JSON config describing each site (id, name, list of
                 page URLs, a CSS selector for each record, and a per-field
                 selector map). For every configured page it fetches, finds
                 every record, and reads the fields into a flat dict.
                 Polite: real User-Agent, per-site rate limit, retries,
                 and de-duplication of identical records.
  * DATA STORE - a small JSON file is the integration's source of truth.
                 The extractor writes fresh records here; the API reads from
                 here, so the website/plugin and the crawler are decoupled
                 (one can run on a schedule, the other serves on demand).
  * API        - a real running HTTP + JSON server (Python's http.server, no
                 Flask) exposing the extracted data:
                   GET /healthz                 liveness + store stats
                   GET /api/sites               list configured sites
                   GET /api/data?site=<id>      latest records for one site
                   GET /api/data                latest records for all sites
                   POST /api/refresh?site=<id>  trigger a fresh extraction
                 These are the endpoints the client's frontend/plugin calls.
  * --selftest - spins up a local HTTP server that serves two FAKE third-party
                 sites with stable structure, runs the REAL extractor against
                 the configured selectors, writes the data store, then starts
                 the REAL API server and drives it over HTTP with requests.
                 Proves extraction AND API integration end-to-end with zero
                 external network (the 8 real third-party sites are the
                 client's; this proves the pipeline, not their live pages).

Run:
    python PoC.py --selftest          # end-to-end proof, no external network
    python PoC.py --config sites.json --store store.json
    python PoC.py --api --port 8080   # serve the data store through the API
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

UA = "DataSync/1.0 (+personal crawler)"
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("extract")


# --------------------------------------------------------------------------- #
# HTTP with retry (polite)
# --------------------------------------------------------------------------- #
def get(url: str, timeout: int = 25, retries: int = 3,
        backoff: float = 1.2) -> requests.Response:
    last = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last = e
            if attempt < retries:
                log.warning("retry %d/%d %s (%s)", attempt, retries, url, e)
                time.sleep(backoff * attempt)
            else:
                log.error("failed %s: %s", url, e)
    raise last  # unreachable


# --------------------------------------------------------------------------- #
# Extraction (config-driven, selector-based)
# --------------------------------------------------------------------------- #
def _read_field(item, spec: dict) -> str:
    """Read one field from an item element using a relative selector spec.

    spec = {"selector": "h2.title", "attr": "data-sku" | None}
    If selector is "" or None, the field is read from the item element itself.
    """
    sel = spec.get("selector") or ""
    attr = spec.get("attr")
    if sel:
        el = item.select_one(sel)
    else:
        el = item
    if el is None:
        return ""
    if attr:
        return (el.get(attr) or "").strip()
    return re.sub(r"\s+", " ", el.get_text()).strip()


def extract_records(html: str, site_cfg: dict) -> list[dict]:
    """Find every record on a page and read its configured fields."""
    soup = BeautifulSoup(html, "html.parser")
    item_sel = site_cfg["item_selector"]
    items = soup.select(item_sel)
    records = []
    for item in items:
        rec = {"site_id": site_cfg["id"], "site_name": site_cfg.get("name", "")}
        for field_name, spec in site_cfg["fields"].items():
            rec[field_name] = _read_field(item, spec)
        # de-duplication key: site + the first non-empty identifying field
        key_src = rec.get("sku") or rec.get("id") or rec.get("name") or \
            json.dumps(rec, sort_keys=True)
        rec["_key"] = f"{site_cfg['id']}::{key_src}"
        records.append(rec)
    return records


def dedupe(records: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for r in records:
        seen[r["_key"]] = r
    return list(seen.values())


def run_site(site_cfg: dict, store) -> list[dict]:
    """Scrape every configured page of one site, merge, dedupe, store."""
    all_records: list[dict] = []
    page_delay = float(site_cfg.get("rate_limit_s", 1.0))
    pages = site_cfg["pages"]
    for i, page in enumerate(pages):
        log.info("[%s] fetching page %d/%d: %s", site_cfg["id"], i + 1, len(pages), page)
        try:
            r = get(page)
        except requests.RequestException:
            log.error("[%s] page failed, skipping: %s", site_cfg["id"], page)
            continue
        recs = extract_records(r.text, site_cfg)
        log.info("[%s]   %d record(s) on %s", site_cfg["id"], len(recs), page)
        all_records.extend(recs)
        if i < len(pages) - 1 and page_delay > 0:
            time.sleep(page_delay)
    records = dedupe(all_records)
    store.put(site_cfg["id"], records, source=site_cfg.get("source_url", ""))
    log.info("[%s] stored %d unique record(s)", site_cfg["id"], len(records))
    return records


def run_all(config: dict, store) -> dict:
    results = {}
    for site_cfg in config["sites"]:
        try:
            results[site_cfg["id"]] = run_site(site_cfg, store)
        except Exception as e:  # one bad site must not sink the whole batch
            log.error("[%s] site failed: %s", site_cfg.get("id"), e)
            results[site_cfg["id"]] = []
    return results


# --------------------------------------------------------------------------- #
# Data store (JSON file = source of truth shared by extractor and API)
# --------------------------------------------------------------------------- #
class Store:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._data = {"sites": {}, "updated_at": None}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                log.warning("store %s unreadable (%s), starting fresh", self.path, e)

    def put(self, site_id: str, records: list[dict], source: str = ""):
        with self._lock:
            self._data.setdefault("sites", {})[site_id] = {
                "records": records,
                "source": source,
                "count": len(records),
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            self._data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._save()

    def get(self, site_id: str | None = None):
        with self._lock:
            if site_id is not None:
                site = self._data.get("sites", {}).get(site_id)
                return {"site_id": site_id, "records": site["records"] if site else [],
                        "count": site["count"] if site else 0,
                        "updated_at": site["updated_at"] if site else None}
            return dict(self._data)

    def stats(self):
        with self._lock:
            return {"updated_at": self._data.get("updated_at"),
                    "site_ids": sorted(self._data.get("sites", {}).keys()),
                    "total_records": sum(s.get("count", 0)
                                        for s in self._data.get("sites", {}).values())}

    def _save(self):
        # Lock-free on purpose: callers (put) already hold self._lock, and
        # self._lock is a non-reentrant Lock, so re-acquiring here would
        # deadlock. get/stats/read paths hold the lock around _load-free reads.
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)
        os.replace(tmp, self.path)


# --------------------------------------------------------------------------- #
# JSON API (http.server, no framework) - the integration surface
# --------------------------------------------------------------------------- #
def make_api_handler(store: Store, config: dict, refresh_cb):
    class ApiHandler(BaseHTTPRequestHandler):
        # silence default stderr logging per request
        def log_message(self, *a):
            pass

        def _send(self, code: int, obj):
            body = json.dumps(obj, indent=2).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _route(self, method: str):
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)
            site_id = (qs.get("site", [None])[0]) if qs.get("site") else None

            if method == "GET" and path == "/healthz":
                self._send(200, {"ok": True, **store.stats()})
            elif method == "GET" and path == "/api/sites":
                self._send(200, {"sites": [
                    {"id": s["id"], "name": s.get("name", ""),
                     "pages": len(s.get("pages", []))}
                    for s in config.get("sites", [])]})
            elif method == "GET" and path == "/api/data":
                data = store.get(site_id)
                self._send(200, data)
            elif method == "POST" and path == "/api/refresh":
                try:
                    if site_id:
                        cfg = next((s for s in config.get("sites", [])
                                    if s["id"] == site_id), None)
                        if not cfg:
                            self._send(404, {"ok": False, "error": f"unknown site {site_id!r}"})
                            return
                        records = run_site(cfg, store)
                        self._send(200, {"ok": True, "site_id": site_id,
                                         "count": len(records)})
                    else:
                        results = run_all(config, store)
                        self._send(200, {"ok": True,
                                         "counts": {k: len(v) for k, v in results.items()}})
                except Exception as e:
                    self._send(500, {"ok": False, "error": str(e)})
            else:
                self._send(404, {"ok": False, "error": "not found", "path": path})

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

    return ApiHandler


def start_api(store: Store, config: dict, port: int = 0) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port),
                              make_api_handler(store, config, None))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# --------------------------------------------------------------------------- #
# --selftest: two fake third-party sites served locally; run the REAL
# extractor + API against them and assert the results over HTTP.
# --------------------------------------------------------------------------- #
SITE_A_PAGE = """
<html><body>
  <div class="product">
    <h2 class="title">Aurora Field Guide</h2>
    <span class="price" data-price="23.99">£23.99</span>
    <em class="sku" data-sku="978-0-7011-3938-2">978-0-7011-3938-2</em>
    <p class="stock">In stock (12 available)</p>
  </div>
  <div class="product">
    <h2 class="title">Tidewater Recipes</h2>
    <span class="price" data-price="12.50">£12.50</span>
    <em class="sku" data-sku="978-1-4028-9462-7">978-1-4028-9462-7</em>
    <p class="stock">In stock (3 available)</p>
  </div>
</body></html>
"""

SITE_B_PAGE = """
<html><body>
  <div class="listing">
    <h3 class="name">Cloud Kitty Popping Puzzle</h3>
    <span class="price">$24.99</span>
    <code class="id">CK-1001</code>
    <p class="avail">Out of stock</p>
  </div>
  <div class="listing">
    <h3 class="name">Fuzzy Paw Squishy (Pink)</h3>
    <span class="price">$14.99</span>
    <code class="id">FP-2001</code>
    <p class="avail">In stock</p>
  </div>
  <div class="listing">
    <h3 class="name">Fuzzy Paw Squishy (Blue)</h3>
    <span class="price">$14.99</span>
    <code class="id">FP-2002</code>
    <p class="avail">Out of stock</p>
  </div>
</body></html>
"""


def _serve_fake_sites():
    import http.server

    pages = {"/supplier-a/catalog": SITE_A_PAGE,
             "/supplier-b/listings": SITE_B_PAGE}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            p = self.path.split("?", 1)[0]
            body = pages.get(p)
            if body is None:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"not found")
                return
            b = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host = f"http://127.0.0.1:{srv.server_address[1]}"
    return srv, host


def selftest() -> int:
    srv, host = _serve_fake_sites()
    outdir = os.path.abspath("selftest_out")
    os.makedirs(outdir, exist_ok=True)
    store_path = os.path.join(outdir, "store.json")
    if os.path.exists(store_path):
        os.remove(store_path)

    # config describing two "third-party sites" with different shapes -
    # proves the pipeline is config-driven, not hardcoded to one site.
    config = {
        "sites": [
            {
                "id": "supplier-alpha",
                "name": "Supplier Alpha",
                "source_url": f"{host}/supplier-a",
                "rate_limit_s": 0.05,
                "pages": [f"{host}/supplier-a/catalog"],
                "item_selector": "div.product",
                "fields": {
                    "name":   {"selector": "h2.title"},
                    "price":  {"selector": "span.price", "attr": "data-price"},
                    "sku":    {"selector": "em.sku", "attr": "data-sku"},
                    "stock":  {"selector": "p.stock"},
                },
            },
            {
                "id": "supplier-beta",
                "name": "Supplier Beta",
                "source_url": f"{host}/supplier-b",
                "rate_limit_s": 0.05,
                "pages": [f"{host}/supplier-b/listings"],
                "item_selector": "div.listing",
                "fields": {
                    "name":  {"selector": "h3.name"},
                    "price": {"selector": "span.price"},
                    "id":    {"selector": "code.id"},
                    "avail": {"selector": "p.avail"},
                },
            },
        ]
    }

    store = Store(store_path)

    # 1) run the REAL extractor pipeline (no mock, real requests against the
    #    fake sites, real selectors, real dedupe, real store write).
    run_all(config, store)

    # 2) start the REAL API server and drive it over HTTP.
    api = start_api(store, config)
    base = f"http://127.0.0.1:{api.server_address[1]}"
    s = requests.Session()

    problems = []
    try:
        # /healthz
        h = s.get(f"{base}/healthz", timeout=10).json()
        if not h.get("ok"):
            problems.append("healthz not ok")
        if h.get("total_records") != 5:
            problems.append(f"expected 5 total records, got {h.get('total_records')}")

        # /api/sites
        sites = s.get(f"{base}/api/sites", timeout=10).json()["sites"]
        if sorted(x["id"] for x in sites) != ["supplier-alpha", "supplier-beta"]:
            problems.append(f"bad site list: {sites}")

        # /api/data?site=supplier-alpha  -> 2 records, correct fields
        a = s.get(f"{base}/api/data", params={"site": "supplier-alpha"}, timeout=10).json()
        if a["count"] != 2:
            problems.append(f"supplier-alpha count {a['count']} != 2")
        recs = {r["name"]: r for r in a["records"]}
        if "Aurora Field Guide" not in recs:
            problems.append("missing Aurora Field Guide")
        aurora = recs.get("Aurora Field Guide", {})
        if aurora.get("price") != "23.99":
            problems.append(f"aurora price {aurora.get('price')!r} != '23.99' (attr read failed)")
        if aurora.get("sku") != "978-0-7011-3938-2":
            problems.append(f"aurora sku {aurora.get('sku')!r} wrong")
        if not aurora.get("stock"):
            problems.append("aurora stock empty")

        # /api/data?site=supplier-beta -> 3 records
        b = s.get(f"{base}/api/data", params={"site": "supplier-beta"}, timeout=10).json()
        if b["count"] != 3:
            problems.append(f"supplier-beta count {b['count']} != 3")
        brecs = {r["name"]: r for r in b["records"]}
        if brecs.get("Cloud Kitty Popping Puzzle", {}).get("id") != "CK-1001":
            problems.append("beta Cloud Kitty id wrong")

        # /api/data (all sites) -> 5 records total across both
        allr = s.get(f"{base}/api/data", timeout=10).json()
        total = sum(v.get("count", 0) for v in allr.get("sites", {}).values())
        if total != 5:
            problems.append(f"all-sites total {total} != 5")

        # POST /api/refresh (trigger a fresh extraction through the API)
        ref = s.post(f"{base}/api/refresh", params={"site": "supplier-alpha"}, timeout=15).json()
        if not ref.get("ok") or ref.get("count") != 2:
            problems.append(f"refresh bad: {ref}")

        # unknown site -> 404
        unknown = s.get(f"{base}/api/data", params={"site": "nope"}, timeout=10)
        if unknown.status_code != 200 or unknown.json().get("count") != 0:
            problems.append("unknown site should return count 0 (graceful)")

        if problems:
            log.error("SELFTEST FAIL: %s", "; ".join(problems))
            return 1

        log.info("SELFTEST PASS: 2 config-driven sites, 5 records, "
                 "attr+text field reads correct, API served over HTTP, "
                 "refresh + graceful unknown-site handled.")
        return 0
    finally:
        api.shutdown()
        srv.shutdown()


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(description="Config-driven data extraction + JSON API")
    ap.add_argument("--selftest", action="store_true",
                    help="end-to-end proof against local fake sites (no external network)")
    ap.add_argument("--config", default="sites.json", help="JSON config of sites to scrape")
    ap.add_argument("--store", default="store.json", help="JSON data-store path")
    ap.add_argument("--api", action="store_true", help="start the JSON API server")
    ap.add_argument("--port", type=int, default=8080, help="API port")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    with open(args.config, encoding="utf-8") as f:
        config = json.load(f)
    store = Store(args.store)

    if args.api:
        srv = start_api(store, config, port=args.port)
        log.info("API serving on http://127.0.0.1:%d  (GET /healthz /api/sites /api/data, POST /api/refresh)",
                 srv.server_address[1])
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            log.warning("shutdown")
            srv.shutdown()
        return 0

    run_all(config, store)
    log.info("done. Store at %s", store.path)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
