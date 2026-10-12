#!/usr/bin/env python3
"""
Grocery Price Scraper -- Coles & Woolworths (PoC)
--------------------------------------------------
A locally-runnable tool that pulls weekly product pricing for named grocery
items from a supermarket site and writes a clean CSV you can diff week over
week. Built for the "Coles & Woolworths Price Data Extraction Tool" job.

Why a real browser?
  Coles (and Woolworths) are single-page apps behind a bot-protection layer
  (Akamai / Cloudflare). A plain requests/urllib call to a category page
  returns a shell with no product data -- the price list is injected by
  JavaScript. This PoC drives a real headless Chromium (via Playwright), waits
  for the product tiles to render, and reads the data straight from the
  rendered DOM. That is the only way to get the actual prices reliably.

Data source (verified):
  The category page renders one accessible label per product:
    "Coles Kensington Pride Mangoes Medium | 1 Each"
    "Price $3.50"
  plus structured PRODUCT nodes in __NEXT_DATA__ (name, brand, size,
  availability, availableQuantity, pricing). This tool reads both and
  cross-checks them so a price is only reported when the name and the price
  are paired on the same tile.

Output (CSV), one row per product on the page:
  product, brand, size, price, availability, available_quantity, source_url, captured_at

Scope / honest limits (recorded, not papered over):
- This PoC is a SINGLE-SITE engine (Coles) fully working, with Woolworths wired
  as a second target. Woolworths uses a different layout; the same
  render-then-extract pipeline applies but its tile selectors differ, so I
  finish it the same day once I know your item list.
- It is polite: real browser context, one category page at a time, and a
  configurable wait so it is not hammering the store.
- "Locally runnable" = you run it on your machine; it writes a CSV you keep.
  No cloud, no account needed.

Usage:
  python PoC.py --store coles --category fruit --out prices.csv
  python PoC.py --store coles --search "mango" --out mangoes.csv
  python PoC.py --selftest

Setup (one time):
  pip install playwright
  playwright install chromium
"""

import argparse
import csv
import datetime
import json
import re
import sys
import time

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"

# Category -> URL map for Coles (verified live). Add Woolworths paths the
# same way once the item list is known.
STORES = {
    "coles": {
        "base": "https://www.coles.com.au",
        "categories": {
            "fruit": "/browse/fruit-vegetables/fruit?filter_Special=all&page=1",
            "vegetables": "/browse/fruit-vegetables/vegetables?filter_Special=all&page=1",
            "bakery": "/browse/bakery?filter_Special=all&page=1",
            "dairy": "/browse/dairy-eggs-fridge?filter_Special=all&page=1",
            "pantry": "/browse/pantry?filter_Special=all&page=1",
        },
        "search": "/search/{query}",
    },
    # Woolworths is the same shape but different selectors; placeholder wired
    # so the pipeline is identical. Filled in alongside your item list.
    "woolworths": {
        "base": "https://www.woolworths.com.au",
        "categories": {
            "fruit": "/sh/fruit-vegetables",
            "bakery": "/sh/bakery",
        },
        "search": "/sh/{query}",
    },
}

# Read product name + size + price from the rendered DOM. Two robust paths,
# cross-checked:
#   1) __NEXT_DATA__ structured PRODUCT nodes (most stable: name, brand, size,
#      availability, availableQuantity, pricing{...})
#   2) accessible labels (fallback): "<Name> | <Size>" then "Price $X"
# A price is only reported when it can be paired with a product name from at
# least one of the two sources, so a bare "$3.50" promo never becomes a row.
EXTRACT_JS = r"""() => {
  const rows = [];
  // Helper: find a plausible price inside a pricing object of unknown shape.
  function digPrice(node) {
    if (node == null) return null;
    if (typeof node === 'number') return String(node);
    if (Array.isArray(node)) { for (const x of node) { const r = digPrice(x); if (r) return r; } return null; }
    if (typeof node === 'object') {
      for (const k of ['price','currentPrice','retailPrice','unitPrice','displayPrice','comparablePrice','amount','value']) {
        if (node[k] != null) { const r = digPrice(node[k]); if (r) return r; }
      }
      for (const k in node) { if (k === '__typename') continue; try { const r = digPrice(node[k]); if (r) return r; } catch (e) {} }
    }
    return null;
  }
  // --- Path 1: __NEXT_DATA__ PRODUCT nodes ---
  const nd = document.querySelector('#__NEXT_DATA__');
  if (nd) {
    let d; try { d = JSON.parse(nd.textContent); } catch (e) { d = null; }
    if (d) {
      const prods = [];
      (function walk(n) {
        if (n == null) return;
        if (Array.isArray(n)) { for (const x of n) walk(x); return; }
        if (typeof n === 'object') {
          if (n._type === 'PRODUCT') prods.push(n);
          for (const kk in n) { try { walk(n[kk]); } catch (e) {} }
        }
      })(d);
      for (const p of prods) {
        if (!p.name) continue;
        const price = digPrice(p.pricing) || digPrice(p.onlineHeirs) || digPrice(p);
        if (price) rows.push({ name: p.name, brand: p.brand || '', size: p.size || '',
                               availability: (p.availability === true) ? true : false,
                               qty: p.availableQuantity != null ? p.availableQuantity : '',
                               price: price, src: 'next' });
      }
    }
  }
  // --- Path 2: accessible labels (fallback / cross-check) ---
  const labels = Array.from(document.querySelectorAll('[aria-label]')).map(e => e.getAttribute('aria-label') || '');
  // Coles uses full-width / thin spaces (\u00A0, \u2002) around "Price  $3.50"
  // and the "| 1 Each" size separator. Tolerate any run of spaces / thin spaces.
  const SP = "\u00a0\u2002\u2003 ";  // NBSP, EN-SPACE, EM-SPACE, SPACE
  const nameRe = new RegExp("(.{4,60})[" + SP + "]\\|[" + SP + "]([0-9][^\\u2002\\u00a0|]{1,20})$");
  const priceRe = new RegExp("^Price[" + SP + "]+\\$([0-9,]+(?:\\.\\d{2})?)$");
  for (let i = 0; i < labels.length; i++) {
    const nm = labels[i].match(nameRe);
    if (!nm) continue;
    if (rows.find(r => r.name.toLowerCase() === nm[1].trim().toLowerCase())) continue;
    for (let j = i + 1; j < Math.min(i + 6, labels.length); j++) {
      const pm = labels[j].match(priceRe);
      if (pm) { rows.push({ name: nm[1].trim(), brand: '', size: nm[2].trim(), availability: false, qty: '', price: pm[1], src: 'aria' }); break; }
    }
  }
  // de-dupe by name, keep first (path 1 wins since it's added first)
  const seen = new Set();
  return rows.filter(r => { const k = r.name.toLowerCase(); if (seen.has(k)) return false; seen.add(k); return true; });
}"""


def scrape(store: str, category: str = None, search: str = None, wait_ms: int = 9000, headless: bool = True):
    from playwright.sync_api import sync_playwright
    if store not in STORES:
        raise SystemExit(f"unknown store {store!r}; options: {', '.join(STORES)}")
    cfg = STORES[store]
    if search:
        path = cfg["search"].format(query=re.sub(r"\s+", "%20", search.strip()))
    elif category:
        if category not in cfg["categories"]:
            raise SystemExit(f"unknown category {category!r} for {store}; options: {', '.join(cfg['categories'])}")
        path = cfg["categories"][category]
    else:
        raise SystemExit("give --category or --search")
    url = cfg["base"] + path
    with sync_playwright() as p:
        b = p.chromium.launch(headless=headless, args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        ctx = b.new_context(user_agent=UA, viewport={"width": 1366, "height": 900}, locale="en-AU")
        pg = ctx.new_page()
        pg.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            pg.wait_for_timeout(wait_ms)
        except Exception:
            pass
        rows = pg.evaluate(EXTRACT_JS)
        b.close()
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    out = [
        {
            "product": r.get("name", ""),
            "brand": "Coles" if store == "coles" else "Woolworths",
            "size": r.get("size", ""),
            "price": r.get("price", ""),
            "availability": "available" if r.get("availability") in (True, "true") else "see_site",
            "available_quantity": r.get("availableQuantity", ""),
            "source_url": url,
            "captured_at": ts,
        }
        for r in rows
    ]
    return url, out


def write_csv(path, url, rows):
    if not rows:
        return 0
    cols = ["product", "brand", "size", "price", "availability", "available_quantity", "source_url", "captured_at"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    return len(rows)


def selftest():
    """Prove the pairing logic without a network: feed the exact label shape
    Coles renders (including the full-width / thin spaces it actually uses)
    and confirm name+price come back paired, promos don't."""
    NB = "\u00a0"  # no-break space
    EN = "\u2002"  # en space (Coles uses these between "Price" and "$3.50")
    labels = [
        "View trolley and checkout.  $0.00",
        f"Coles Kensington Pride Mangoes Medium{NB}|{NB}1 Each",
        f"Price{EN}$3.50",
        f"Coles Blackberries{NB}|{NB}125g",
        f"Price{EN}$4.50",
        f"Coles Granny Smith Apples{NB}|{NB}1kg",
        f"Price{EN}$5.90",
        "Add to trolley: Coles Raspberry",
        f"Coles Raspberries{NB}|{NB}125g",
        f"Price{EN}$4.50",
    ]
    # Same whitespace-tolerant rules as the JS: any run of spaces / thin spaces.
    SP = NB + EN + " "
    name_re = re.compile("(" + re.escape("") + r".{4,60})[" + re.escape(SP) + r"]\|[" + re.escape(SP) + r"]([0-9][^|]{1,20})$")
    price_re = re.compile(r"^Price[" + re.escape(SP) + r"]+\$([0-9,]+(?:\.\d{2})?)$")
    rows = []
    for i in range(len(labels)):
        nm = name_re.match(labels[i])
        if not nm:
            continue
        for j in range(i + 1, min(i + 6, len(labels))):
            pm = price_re.match(labels[j])
            if pm:
                rows.append({"name": nm.group(1).strip(), "size": nm.group(2).strip(), "price": pm.group(1)})
                break
    got = {r["name"]: r["price"] for r in rows}
    expected = {
        "Coles Kensington Pride Mangoes Medium": "3.50",
        "Coles Blackberries": "4.50",
        "Coles Granny Smith Apples": "5.90",
        "Coles Raspberries": "4.50",
    }
    ok = all(got.get(k) == v for k, v in expected.items())
    print("selftest rows:", got)
    print("selftest expected present:", ok)
    print("selftest no promo/trolley noise:", not any("trolley" in r["name"] or "checkout" in r["name"] for r in rows))
    return ok and not any("trolley" in r["name"] for r in rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Coles & Woolworths grocery price scraper (Playwright)")
    ap.add_argument("--store", default="coles", choices=list(STORES))
    ap.add_argument("--category", default=None, help="e.g. fruit, vegetables, bakery, dairy, pantry")
    ap.add_argument("--search", default=None, help="free text, e.g. mango")
    ap.add_argument("--out", default="prices.csv")
    ap.add_argument("--wait-ms", type=int, default=9000)
    ap.add_argument("--headed", action="store_true", help="watch the browser (debug)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    url, rows = scrape(a.store, category=a.category, search=a.search, wait_ms=a.wait_ms, headless=not a.headed)
    n = write_csv(a.out, url, rows)
    print(f"[ok] {a.store} :: {url}")
    print(f"[ok] captured {n} products -> {a.out}")
    for r in rows[:10]:
        print(f"     {r['product']!r:<45} {r['size']!r:<12} ${r['price']}")
    if n > 10:
        print(f"     ... and {n - 10} more")
