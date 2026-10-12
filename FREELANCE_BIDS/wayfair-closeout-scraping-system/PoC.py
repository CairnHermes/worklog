#!/usr/bin/env python3
"""
Wayfair Closeout Section Scraper
================================
Captures: Product Name, Price, Description
Outputs : Clean, deduplicated CSV file.

Built to be locally runnable, robust to Wayfair's anti-bot gating, and easy to
point at any search / closeout query.

Usage:
    python wayfair_scraper.py --query "closeout" --pages 3 -o closeout.csv
    python wayfair_scraper.py --category 552266 --pages 2
    python wayfair_scraper.py --urls "https://www.wayfair.com/closeout/..."

Design notes
------------
* Wayfair ships product data as JSON-LD + a per-card JSON blob inside the HTML.
  We prefer the structured JSON (reliable fields) and fall back to regex on the
  card markup if the page layout shifts.
* Dedup is on a stable key (product id, else slug, else normalized name+price).
* A polite default delay + randomized jitter avoids hammering the origin.
* Every run logs to stderr; the CSV is the only stdout artifact.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from typing import Iterator, Optional

LOG = logging.getLogger("wayfair")

USER_AGENTS = [
    # a small, realistic set of recent desktop UAs; rotate per request
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
]

BASE = "https://www.wayfair.com"
SEARCH_PATH = "/search?q={q}"
CATEGORY_PATH = "/category/{cat}"
CLOSEOUT_PATH = "/closeout/search?q={q}"


@dataclass
class Product:
    name: str
    price: Optional[float]
    description: str
    url: str
    product_id: str = ""
    source: str = ""          # "jsonld" | "card" | "regex"
    first_seen: float = 0.0

    @property
    def dedup_key(self) -> str:
        base = self.product_id or self._slug() or f"{self.name}|{self.price}"
        return hashlib.md5(base.encode("utf-8", "ignore")).hexdigest()

    def _slug(self) -> str:
        m = re.search(r"/[a-z0-9-]+-[A-Z0-9]+-\d+/", self.url)
        return m.group(0) if m else ""


def _headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": BASE + "/",
        "Cache-Control": "no-cache",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }


def fetch(url: str, timeout: int = 25, retries: int = 3) -> Optional[str]:
    """Fetch a URL with rotating headers + jittered retries. Returns text or None."""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=_headers())
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", "ignore")
                if resp.status != 200:
                    raise urllib.error.HTTPError(url, resp.status, "non-200", None, None)
                LOG.info("fetched %s (%d bytes)", url, len(body))
                return body
        except Exception as e:  # noqa: BLE001 - we retry on any transport error
            last_err = e
            backoff = (2 ** attempt) + random.uniform(0.2, 1.2)
            LOG.warning("attempt %d/%d failed for %s: %s -> sleeping %.1fs",
                        attempt, retries, url, e, backoff)
            time.sleep(backoff)
    LOG.error("giving up on %s after %d attempts: %s", url, retries, last_err)
    return None


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _to_price(v) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"\$?\s*([\d,]+(?:\.\d{1,2})?)", str(v))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def parse_jsonld(html: str, page_url: str) -> list[Product]:
    """Pull products from <script type="application/ld+json"> blocks."""
    out: list[Product] = []
    for m in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                         html, re.S):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if isinstance(node, dict) and node.get("@type") == "Product":
                name = _clean(str(node.get("name", "")))
                desc = _clean(str(node.get("description", "")))
                price = _to_price(node.get("price")) or _to_price(node.get("offers", {}).get("price") if isinstance(node.get("offers"), dict) else None)
                pid = str(node.get("sku", "") or node.get("mpn", "") or "")
                if name:
                    out.append(Product(name=name, price=price, description=desc,
                                       url=page_url, product_id=pid, source="jsonld"))
    return out


CARD_RE = re.compile(r'<div[^>]+data-qa=["\']productCard["\'][^>]*>(.*?)</div>\s*</div>', re.S)
NAME_RE = re.compile(r'data-qa=["\']productCardName["\'][^>]*>(.*?)</a>', re.S)
PRICE_RE = re.compile(r'data-qa=["\']productCardPrice["\'][^>]*>.*?\$?\s*([\d,]+\.?\d*)', re.S)


def parse_cards(html: str, page_url: str) -> list[Product]:
    """Fallback: scrape the per-product card markup (survives layout shifts)."""
    out: list[Product] = []
    for m in CARD_RE.finditer(html):
        card = m.group(1)
        nm = NAME_RE.search(card)
        pr = PRICE_RE.search(card)
        name = _clean(re.sub(r"<[^>]+>", "", nm.group(1))) if nm else ""
        if not name:
            continue
        link = re.search(r'href=["\']([^"\']+)["\']', card)
        url = (BASE + link.group(1)) if link and link.group(1).startswith("/") else (link.group(1) if link else page_url)
        out.append(Product(name=name, price=_to_price(pr.group(1)) if pr else None,
                           description="", url=url, source="card"))
    return out


def parse_regex(html: str, page_url: str) -> list[Product]:
    """Last resort: grab any /<slug>-<SKU>-<id>/ product links near a price."""
    out: list[Product] = []
    seen = set()
    for m in re.finditer(r'href="(/([a-z0-9-]+-[A-Z0-9]+-\d+)/)"[^>]*>([^<]{5,120})</a>', html):
        slug, label = m.group(1), _clean(m.group(3))
        if slug in seen:
            continue
        seen.add(slug)
        out.append(Product(name=label, price=None, description="",
                           url=BASE + slug, source="regex"))
    return out


def scrape_page(html: str, page_url: str) -> list[Product]:
    """Try structured -> card -> regex. Dedupe within the page."""
    products = parse_jsonld(html, page_url) or parse_cards(html, page_url) or parse_regex(html, page_url)
    seen: set[str] = set()
    unique: list[Product] = []
    for p in products:
        k = p.dedup_key
        if k in seen:
            continue
        seen.add(k)
        p.first_seen = time.time()
        unique.append(p)
    return unique


def iter_pages(args) -> Iterator[str]:
    if args.urls:
        for u in args.urls.split():
            yield u.strip()
        return
    for page in range(1, args.pages + 1):
        q = urllib.parse.quote(args.query or "")
        if args.category:
            base = BASE + CATEGORY_PATH.format(cat=args.category)
        else:
            base = BASE + (CLOSEOUT_PATH.format(q=q) if args.closeout else SEARCH_PATH.format(q=q))
        sep = "&" if "?" in base else "?"
        yield f"{base}{sep}page={page}" if page > 1 else base


def main() -> int:
    ap = argparse.ArgumentParser(description="Wayfair closeout / search scraper -> clean CSV")
    ap.add_argument("--query", default="closeout", help="search query (default: closeout)")
    ap.add_argument("--category", default="", help="Wayfair numeric category id (overrides query)")
    ap.add_argument("--closeout", action="store_true", help="prefer the /closeout/ path")
    ap.add_argument("--urls", default="", help="space-separated explicit URLs to scrape instead")
    ap.add_argument("--pages", type=int, default=1, help="number of search pages to walk")
    ap.add_argument("-o", "--out", default="wayfair_closeout.csv", help="output CSV path")
    ap.add_argument("--delay", type=float, default=3.0, help="base delay between pages (s)")
    ap.add_argument("--timeout", type=int, default=25, help="per-request timeout (s)")
    ap.add_argument("--max-items", type=int, default=500, help="stop after N unique products")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")

    all_products: dict[str, Product] = {}
    for url in iter_pages(args):
        LOG.info("scraping: %s", url)
        html = fetch(url, timeout=args.timeout)
        if not html:
            LOG.warning("no HTML for %s (blocked or down) - skipping", url)
            continue
        for p in scrape_page(html, url):
            all_products.setdefault(p.dedup_key, p)
        if len(all_products) >= args.max_items:
            break
        time.sleep(max(0.5, args.delay) + random.uniform(0, 1.0))

    rows = list(all_products.values())
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "price", "description", "url", "product_id", "source"])
        for p in rows:
            w.writerow([p.name, p.price if p.price is not None else "",
                        p.description, p.url, p.product_id, p.source])

    LOG.info("done: %d unique products -> %s", len(rows), os.path.abspath(args.out))
    if rows:
        sample = rows[0]
        LOG.info("sample: %r @ %s", sample.name[:60], sample.price)
    else:
        LOG.error("no products captured. Wayfair likely served a bot-wall page for this "
                  "run. Options: try --closeout, a specific --category, a residential "
                  "egress, or a different UA. This is a known, non-code blocker.")
    return 0 if rows else 2


if __name__ == "__main__":
    raise SystemExit(main())
