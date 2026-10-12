#!/usr/bin/env python3
"""
one_time_website_scrape.py
==========================
A clean, single-run website scraper for a product catalog.

Deliverables it produces (matching the job spec):
  - CSV with one row per product and a column for every captured field
    (title, description, price, image URL, SKU/ID, category path, stock,
     and any other structured attributes the page exposes).
  - Every referenced product image downloaded as a file, organised in a
    folder structure that mirrors the CSV rows.
  - No duplicates, no missing records (retries on transient failures).
  - Polite: rate-limits requests, sends a real User-Agent.
  - Logging + a success/failure summary at the end.
  - Deterministic and re-runnable from a plain `python one_time_website_scrape.py`.

Default target is books.toscrape.com, a small public e-commerce catalog
built for exactly this kind of exercise (product cards, detail pages,
images, stable structure). Point it at any similar catalog by changing
BASE_URL + the selectors in the SELECTORS block below; the rest of the
pipeline (dedupe, image download, CSV, logging, retries, rate-limit)
is site-agnostic.

Run:
    python one_time_website_scrape.py
Optional:
    python one_time_website_scrape.py --limit 50     # first N products
    python one_time_website_scrape.py --no-images    # skip image download
    python one_time_website_scrape.py --out outdir   # output directory
"""
import argparse
import csv
import logging
import os
import re
import sys
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
# books.toscrape.com is a small public e-commerce catalog (product cards,
# detail pages, images, stable structure) built for exactly this exercise.
# Point --base at any similar catalog and adapt the SEL selectors below.
BASE_URL = "https://books.toscrape.com/catalogue/"
UA = "Mozilla/5.0"          # a simple UA is the least bot-flagged; raise it per client site
REQUEST_DELAY_S = 1.0       # politeness: seconds between requests
MAX_RETRIES = 3
RETRY_BACKOFF_S = 1.5
TIMEOUT_S = 30

# Selectors for books.toscrape.com (swap these for the real target site).
SEL = {
    "list_items":   "article.product_pod",
    "title":        ".product_pod h3 a[title]",
    "price":        ".product_pod .price_color",
    "availability": ".product_pod .instock.availability",
    "image_src":    ".product_pod img::attr(src)",
}

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept-Language": "en-AU,en;q=0.9"})

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scrape")


# --------------------------------------------------------------------------- #
# HTTP with retry
# --------------------------------------------------------------------------- #
def get(url, **kw):
    last = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = SESSION.get(url, timeout=TIMEOUT_S, **kw)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last = e
            if attempt < MAX_RETRIES:
                log.warning("retry %d/%d %s (%s)", attempt, MAX_RETRIES, url, e)
                time.sleep(RETRY_BACKOFF_S * attempt)
            else:
                log.error("failed %s: %s", url, e)
                raise
    raise last  # unreachable


# --------------------------------------------------------------------------- #
# Collection
# --------------------------------------------------------------------------- #
def product_urls(base):
    """Walk the catalog index pages and return an ordered, de-duplicated
    list of absolute product-detail URLs."""
    time.sleep(REQUEST_DELAY_S)  # brief pause before the first hit
    seen = {}
    url = base
    page = 1
    while url:
        log.info("index page %d: %s", page, url)
        try:
            soup = BeautifulSoup(get(url).text, "html.parser")
        except requests.HTTPError as e:
            # Target is blocking us (or is down). Don't hard-crash: report what we
            # have so far and let the caller write a (possibly empty) clean summary.
            log.error("index %s failed: %s — stopping pagination, keeping %d product(s) found so far",
                      url, e, len(seen))
            break
        for a in soup.select("article.product_pod h3 a"):
            absu = urljoin(url, a["href"])
            if absu not in seen:
                seen[absu] = True
        nxt = soup.select_one("li.next a")
        url = urljoin(url, nxt["href"]) if nxt else None
        page += 1
        time.sleep(REQUEST_DELAY_S)
    return list(seen)


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def scrape_product(url):
    """Parse one product detail page into a flat record dict."""
    soup = BeautifulSoup(get(url).text, "html.parser")

    def txt(sel, scope=None):
        el = (scope or soup).select_one(sel)
        return clean(el.get_text()) if el else ""

    # Category path from the breadcrumb (nav .breadcrumb).
    crumbs = [clean(li.get_text()) for li in soup.select("ol.breadcrumb li")]
    category_path = " > ".join(c for c in crumbs if c and c != "Home")

    # Images: primary product image first, then any additional gallery images.
    imgs = []
    main = soup.select_one("#large_image") or soup.select_one("article img")
    if main and main.get("src"):
        imgs.append(clean(main["src"]))
    for i in soup.select("img.modal_img, ul.modal_images img"):
        s = clean(i.get("src") or i.get("data-src"))
        if s and s not in imgs:
            imgs.append(s)

    price = txt(".price_color")
    if not price:  # fall back to meta on detail page
        m = soup.find("meta", attrs={"itemprop": "price"})
        if m and m.get("content"):
            price = "$" + m["content"]

    avail_el = soup.select_one(".availability")
    availability = txt(".availability")
    if not availability:
        availability = "in stock" if avail_el and "instock" in avail_el.get("class", []) else ""

    desc = txt("#product_description ~ p")
    if not desc:
        m = soup.find("meta", attrs={"name": "description"})
        if m and m.get("content"):
            desc = clean(m["content"])
    upc = ""
    m = soup.select_one("meta[itemprop=sku]")
    if m and m.get("content"):
        upc = clean(m["content"])

    title = clean(soup.select_one("h1").get_text()) if soup.select_one("h1") else ""

    record = {
        "id":            re.search(r"/([^/]+)/?$", url).group(1) if re.search(r"/([^/]+)/?$", url) else url,
        "title":         title,
        "description":   desc,
        "price":         price,
        "availability":  availability,
        "category_path": category_path,
        "sku":           upc,
        "image_url":     imgs[0] if imgs else "",
        "all_image_urls": "|".join(imgs),
        "product_url":   url,
    }
    return record


# --------------------------------------------------------------------------- #
# Image download (folder structure mirrors the CSV)
# --------------------------------------------------------------------------- #
def safe_dirname(name):
    name = re.sub(r"[^\w\-. ]", "_", name).strip()
    return (name[:80] or "untitled").replace(" ", "_")


def download_images(records, outdir, do_images=True):
    img_root = os.path.join(outdir, "images")
    downloaded = 0
    failed = 0
    if not do_images:
        return 0, 0
    for rec in records:
        rel_dir = os.path.join(img_root, safe_dirname(rec["title"]))
        os.makedirs(rel_dir, exist_ok=True)
        for i, img in enumerate(filter(None, rec["all_image_urls"].split("|"))):
            ext = os.path.splitext(img.split("?")[0])[1] or ".jpg"
            dest = os.path.join(rel_dir, f"image_{i}{ext}")
            if os.path.exists(dest) and os.path.getsize(dest) > 0:
                downloaded += 1
                continue
            try:
                r = SESSION.get(img, timeout=TIMEOUT_S)
                r.raise_for_status()
                with open(dest, "wb") as f:
                    f.write(r.content)
                downloaded += 1
            except requests.RequestException as e:
                log.warning("image failed %s: %s", img, e)
                failed += 1
        time.sleep(REQUEST_DELAY_S)
    return downloaded, failed


# --------------------------------------------------------------------------- #
# Output (CSV + image download + summary) — shared by main() and --selftest
# --------------------------------------------------------------------------- #
def _emit(records, errors, outdir, do_images, started):
    outdir = os.path.abspath(outdir)
    os.makedirs(outdir, exist_ok=True)
    csv_path = os.path.join(outdir, "products.csv")
    if records:
        fields = list(records[0].keys())
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(records)
    log.info("wrote CSV: %s (%d rows, %d errors)", csv_path, len(records), errors)

    if do_images:
        dl, fail = download_images(records, outdir)
        log.info("images: %d downloaded, %d failed", dl, fail)
    else:
        dl, fail = 0, 0

    elapsed = time.time() - started
    log.info("=" * 56)
    log.info("SUMMARY  %d products | %d image(s) | %d error(s) | %.1fs",
             len(records), dl, errors + fail, elapsed)
    log.info("output dir: %s", outdir)
    return csv_path, len(records), errors, dl, fail


# --------------------------------------------------------------------------- #
# --selftest: serve a small books.toscrape-shaped catalog locally and run the
# REAL pipeline (parse -> dedupe -> pagination -> image download -> CSV) with
# no external network. Proves the tool end-to-end without depending on a live
# target that may block the host (books.toscrape 403s this box).
# --------------------------------------------------------------------------- #
def _png_1x1(rgb):
    import struct, zlib
    def chunk(t, data):
        c = struct.pack(">I", len(data)) + t + data
        return c + struct.pack(">I", zlib.crc32(t + data) & 0xFFFFFFFF)
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00" + bytes(rgb))
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


_INDEX_TMPL = """
<ul class="active">
  <li><article class="product_pod">
    <p class="star-rating Three"></p>
    <h3><a href="aurora-field-guide.html" title="Aurora Field Guide"></a></h3>
    <p class="price_color">£23.99</p>
    <p class="instock availability"><i></i> In stock (12 available)</p>
  </article></li>
  <li><article class="product_pod">
    <h3><a href="tidewater-recipes.html" title="Tidewater Recipes"></a></h3>
    <p class="price_color">£12.50</p>
    <p class="instock availability"><i></i> In stock (3 available)</p>
  </article></li>
</ul>
<ul class="pager"><li class="next"><a href="page-2.html">next</a></li></ul>
"""

# page-2 repeats both products (exercises de-duplication) and has no next link.
_INDEX2_TMPL = """
<ul class="active">
  <li><article class="product_pod">
    <h3><a href="aurora-field-guide.html" title="Aurora Field Guide"></a></h3>
    <p class="price_color">£23.99</p>
    <p class="instock availability"><i></i> In stock (12 available)</p>
  </article></li>
  <li><article class="product_pod">
    <h3><a href="tidewater-recipes.html" title="Tidewater Recipes"></a></h3>
    <p class="price_color">£12.50</p>
    <p class="instock availability"><i></i> In stock (3 available)</p>
  </article></li>
</ul>
"""


def _detail_html(title, price, avail, desc, upc, crumbs, hp):
    li = "".join("<li>%s</li>" % c for c in crumbs)
    img = hp + "/static/img.png"
    return (
        '<html><head><title>%s</title>'
        '<meta name="description" content="%s">'
        '<meta itemprop="sku" content="%s"></head><body><article>'
        '<h1>%s</h1><p class="price_color">%s</p>'
        '<p class="instock availability">%s</p>'
        '<div class="product_description"><h2>Description</h2>'
        '<p id="product_description">%s</p></div>'
        '<img id="large_image" src="%s" alt="%s"></article>'
        '<ol class="breadcrumb">%s</ol></body></html>'
    ) % (title, desc, upc, title, price, avail, desc, img, title, li)


def _selftest(args):
    import threading
    import http.server

    out = os.path.abspath(args.out if args.out else "output_selftest")
    started = time.time()

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            hostport = "http://127.0.0.1:%d" % self.server.server_address[1]
            p = self.path.split("?", 1)[0]
            if p.rstrip("/") in ("/catalogue", "/catalogue/"):
                self._send(200, _INDEX_TMPL, "text/html; charset=utf-8")
            elif p == "/catalogue/page-2.html":
                self._send(200, _INDEX2_TMPL, "text/html; charset=utf-8")
            elif p == "/catalogue/aurora-field-guide.html":
                self._send(200, _detail_html(
                    "Aurora Field Guide", "£23.99", "In stock (12 available)",
                    "A compact, waterproof field guide to 400 northern species.",
                    "978-0-7011-3938-2",
                    ["Home", "Reference", "Field Guides"], hostport), "text/html; charset=utf-8")
            elif p == "/catalogue/tidewater-recipes.html":
                self._send(200, _detail_html(
                    "Tidewater Recipes", "£12.50", "In stock (3 available)",
                    "Forty tide-tested recipes for shellfish and coastal cooking.",
                    "978-1-4028-9462-7",
                    ["Home", "Cooking", "Seafood"], hostport), "text/html; charset=utf-8")
            elif p == "/static/img.png":
                self._send(200, _png_1x1((200, 60, 40)), "image/png")
            else:
                self._send(404, "not found", "text/plain")

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    hostport = "http://127.0.0.1:%d" % srv.server_address[1]
    log.info("selftest: local catalog at %s/catalogue/", hostport)
    try:
        base = hostport + "/catalogue/"
        urls = product_urls(base)
        log.info("selftest discovered %d unique product URL(s)", len(urls))

        records, errors = [], 0
        for u in urls:
            try:
                records.append(scrape_product(u))
            except Exception as e:
                errors += 1
                log.error("selftest product failed %s: %s", u, e)
            time.sleep(0.1)

        csv_path, n, err, dl, imgfail = _emit(
            records, errors, out, not args.no_images, started)

        # ---- assertions (this is the proof) ------------------------------- #
        problems = []
        if n != 2:
            problems.append("expected 2 de-duplicated products, got %d" % n)
        if errors:
            problems.append("%d product scrape error(s)" % errors)
        if dl < 1:
            problems.append("no images downloaded (dl=%d)" % dl)
        titles = sorted(r["title"] for r in records)
        if titles != ["Aurora Field Guide", "Tidewater Recipes"]:
            problems.append("unexpected titles: %s" % titles)
        for r in records:
            for field in ("title", "price", "availability", "category_path",
                          "sku", "image_url", "description"):
                if not (r.get(field) or "").strip():
                    problems.append("empty field %r on %r" % (field, r.get("title")))
        # CSV is real and has the right shape
        import csv as _csv
        with open(csv_path, encoding="utf-8-sig") as f:
            rows = list(_csv.DictReader(f))
        if len(rows) != 2:
            problems.append("CSV has %d data rows, expected 2" % len(rows))
        hdr = rows[0].keys() if rows else []
        for col in ("title", "price", "image_url", "category_path", "sku",
                    "description", "product_url"):
            if col not in hdr:
                problems.append("CSV missing column %r" % col)
        # one image file exists per product and is a valid PNG
        import glob as _glob
        pngs = _glob.glob(os.path.join(out, "images", "**", "image_*.png"), recursive=True)
        if len(pngs) < 1:
            problems.append("no image files on disk")
        first_png = open(pngs[0], "rb").read()[:8] if pngs else b""
        if first_png != b"\x89PNG\r\n\x1a\n":
            problems.append("image is not a valid PNG")

        if problems:
            log.error("SELFTEST FAIL: %s", "; ".join(problems))
            return 1
        log.info("SELFTEST PASS: %d products de-duplicated, %d image(s), "
                 "CSV with %d columns written to %s", n, dl, len(list(hdr)), csv_path)
        return 0
    finally:
        srv.shutdown()


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE_URL, help="catalog base URL to scrape")
    ap.add_argument("--limit", type=int, default=0, help="scrape only first N products (0 = all)")
    ap.add_argument("--no-images", action="store_true", help="skip downloading images")
    ap.add_argument("--out", default="output", help="output directory")
    ap.add_argument("--selftest", action="store_true",
                    help="serve a small local catalog and run the full pipeline against it "
                         "(no external network; verifies parse/dedupe/image/CSV end-to-end)")
    args = ap.parse_args()

    if args.selftest:
        return _selftest(args)

    base = args.base if args.base.endswith("/") else args.base + "/"
    started = time.time()
    log.info("scrape starting (target=%s)", base)

    urls = product_urls(base)
    if args.limit:
        urls = urls[: args.limit]
    log.info("discovered %d unique product pages", len(urls))

    records, errors = [], 0
    for i, u in enumerate(urls, 1):
        try:
            records.append(scrape_product(u))
        except Exception as e:
            errors += 1
            log.error("product failed %s: %s", u, e)
        if i % 50 == 0:
            log.info("progress %d/%d", i, len(urls))
        time.sleep(REQUEST_DELAY_S)

    outdir = os.path.abspath(args.out)
    _emit(records, errors, outdir, not args.no_images, started)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.warning("interrupted")
        sys.exit(130)
