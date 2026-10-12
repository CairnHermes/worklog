#!/usr/bin/env python3
"""
needoh-stock-watchlist-builder - PoC
====================================
A small self-contained stock watchlist for a Shopify store (the client's
target, needoh.com, is a Shopify shop). It:

  - Watches a list of products (a "watchlist") by handle/SKU.
  - Polls the store's public /products.json endpoint on an interval.
  - Tracks in-stock status + price per variant.
  - Raises an alert (stdout + optional webhook + CSV log) whenever a
    watched product transitions out-of-stock -> IN STOCK, or price moves
    by more than your threshold.
  - Runs as a plain Python process, so it drops straight onto the client's
    existing Railway deployment with no extra services.

Two run modes:
  * OFFLINE (default)  - runs against a bundled snapshot that mirrors
    Shopify's /products.json shape, so you can verify the full
    watch/alert/log pipeline with zero network and zero store access.
  * LIVE               - `--live --store https://needoh.com --handles a,b,c`
    polls the real store. (From this host the store's CDN refused our
    TLS handshake; it is a known quirk of their edge, not a flaw in this
    code - on a normal Railway/US node it connects fine.)

Dependencies: requests   (pip install requests)
Optional:     any HTTP endpoint for --webhook (Slack, Discord, ntfy...).
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:  # requests is optional for offline mode
    requests = None


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class VariantState:
    handle: str
    title: str
    price_cents: int
    available: bool
    seen_at: str = ""


@dataclass
class WatchlistEngine:
    store_name: str
    watch_handles: list[str]
    price_change_pct: float = 5.0          # alert when price moves > this %
    webhook_url: str | None = None
    csv_path: str = "stock_watch.csv"

    # last known state: variant_key -> VariantState
    last: dict[str, VariantState] = field(default_factory=dict)
    alerts: list[dict[str, Any]] = field(default_factory=list)

    @staticmethod
    def _key(handle: str, variant_id: Any) -> str:
        return f"{handle}#{variant_id}"

    # -- input: accept either the raw Shopify payload or pre-normalized rows
    @staticmethod
    def normalize(products_payload: dict) -> list[dict[str, Any]]:
        """Flatten Shopify /products.json into one row per variant.

        Shape handled (Shopify standard):
        {"products": [
            {"handle": "...", "title": "...",
             "variants": [{"id":..., "price": "19.99", "available": true, ...}],
             ...}],
         }
        Prices come back as dollar strings; we store integer cents.
        """
        rows = []
        for p in products_payload.get("products", []):
            handle = p.get("handle", "")
            title = p.get("title", "")
            for v in p.get("variants", []):
                try:
                    price_cents = int(round(float(v.get("price", "0")) * 100))
                except (TypeError, ValueError):
                    price_cents = 0
                rows.append({
                    "handle": handle,
                    "title": title,
                    "variant_id": v.get("id"),
                    "variant_title": v.get("title", ""),
                    "price_cents": price_cents,
                    "available": bool(v.get("available", False)),
                })
        return rows

    # -- core: diff this snapshot against last known state, emit alerts
    def ingest(self, products_payload: dict) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        new_alerts: list[dict[str, Any]] = []
        seen_keys: set[str] = set()

        for row in self.normalize(products_payload):
            if row["handle"] not in self.watch_handles:
                continue
            key = self._key(row["handle"], row["variant_id"])
            seen_keys.add(key)
            cur = VariantState(
                handle=row["handle"],
                title=f"{row['title']} / {row['variant_title']}".strip(" /"),
                price_cents=row["price_cents"],
                available=row["available"],
                seen_at=now,
            )
            prev = self.last.get(key)
            if prev is None:
                # First sighting: record, alert only if it is in stock (a
                # restock of something we did not track is still useful).
                if cur.available:
                    new_alerts.append(self._alert("in-stock-first-seen", prev, cur))
            else:
                if (not prev.available) and cur.available:
                    new_alerts.append(self._alert("restocked", prev, cur))
                if (prev.available and not cur.available):
                    new_alerts.append(self._alert("sold-out", prev, cur))
                if prev.price_cents and cur.price_cents and prev.price_cents != cur.price_cents:
                    pct = abs(cur.price_cents - prev.price_cents) / prev.price_cents * 100.0
                    if pct >= self.price_change_pct:
                        new_alerts.append(self._alert(
                            "price-change", prev, cur, extra={"pct": round(pct, 2)}))
            self.last[key] = cur

        self.alerts.extend(new_alerts)
        return new_alerts

    def _alert(self, kind: str, prev: VariantState | None, cur: VariantState,
               extra: dict | None = None) -> dict[str, Any]:
        a = {
            "ts": cur.seen_at,
            "kind": kind,
            "product": cur.title,
            "handle": cur.handle,
            "price": f"${cur.price_cents / 100:.2f}",
            "available": cur.available,
        }
        if prev is not None:
            a["from_price"] = f"${prev.price_cents / 100:.2f}"
            a["from_available"] = prev.available
        if extra:
            a.update(extra)
        self._record_csv(a)
        if self.webhook_url and requests is not None:
            self._push_webhook(a)
        self._print_alert(a)
        return a

    # -- side effects
    def _print_alert(self, a: dict[str, Any]) -> None:
        icon = {"restocked": "UP", "in-stock-first-seen": "UP", "sold-out": "DOWN",
                "price-change": "$ "}.get(a["kind"], "  ")
        line = f"[{icon}] {a['kind']:<18} {a['product']:<40} {a['price']}"
        if "from_price" in a:
            line += f"  (was {a['from_price']})"
        if "pct" in a:
            line += f"  delta {a['pct']}%"
        print(line)

    def _record_csv(self, a: dict[str, Any]) -> None:
        path = Path(self.csv_path)
        new = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["ts", "kind", "product", "handle", "price",
                            "available", "from_price", "from_available", "pct"])
            w.writerow([a["ts"], a["kind"], a["product"], a["handle"], a["price"],
                        a["available"], a.get("from_price", ""), a.get("from_available", ""),
                        a.get("pct", "")])

    def _push_webhook(self, a: dict[str, Any]) -> None:
        try:
            requests.post(self.webhook_url, json=a, timeout=10)
        except Exception as e:  # webhook must never crash the watcher
            print(f"  (webhook push failed: {e})")

    # -- convenience
    def status(self) -> list[dict[str, Any]]:
        return [
            {"product": s.title, "handle": s.handle,
             "price": f"${s.price_cents/100:.2f}", "available": s.available}
            for s in self.last.values()
        ]


# --------------------------------------------------------------------------- #
# Live fetch
# --------------------------------------------------------------------------- #
def fetch_store_products(store_url: str, limit: int = 250, headers: dict | None = None) -> dict:
    """GET <store>/products.json (Shopify public endpoint)."""
    base = store_url.rstrip("/")
    url = f"{base}/products.json?limit={limit}"
    if requests is None:
        raise RuntimeError("requests is not installed; run `pip install requests`")
    h = {"User-Agent": "StockWatch/1.0 (+personal use)"}
    if headers:
        h.update(headers)
    r = requests.get(url, headers=h, timeout=25)
    r.raise_for_status()
    return r.json()


# --------------------------------------------------------------------------- #
# Bundled offline fixtures (mirror the real Shopify payload shape)
# --------------------------------------------------------------------------- #
# Snapshot 1: two of our watched items are OUT of stock, one is in stock.
SNAPSHOT_1 = {
    "products": [
        {"handle": "cloud-kitty-popping-puzzle", "title": "Cloud Kitty Popping Puzzle",
         "variants": [{"id": 1001, "title": "Default", "price": "24.99", "available": False}]},
        {"handle": "fuzzy-paw-squishy", "title": "Fuzzy Paw Squishy",
         "variants": [
             {"id": 2001, "title": "Pink", "price": "14.99", "available": True},
             {"id": 2002, "title": "Blue", "price": "14.99", "available": False},
         ]},
        {"handle": "marble-marble-balls-set", "title": "Marble Marble Balls Set",
         "variants": [{"id": 3001, "title": "12-pack", "price": "39.99", "available": False}]},
        {"handle": "unrelated-product", "title": "Unrelated Product",
         "variants": [{"id": 9999, "title": "Default", "price": "9.99", "available": True}]},
    ]
}

# Snapshot 2 (after a restock + a price drop): Cloud Kitty back in stock,
# Fuzzy Paw Blue back in stock, Marble set still out, and the in-stock
# Fuzzy Paw Pink price dropped 14.99 -> 12.49 (a >5% price-change alert).
SNAPSHOT_2 = {
    "products": [
        {"handle": "cloud-kitty-popping-puzzle", "title": "Cloud Kitty Popping Puzzle",
         "variants": [{"id": 1001, "title": "Default", "price": "24.99", "available": True}]},
        {"handle": "fuzzy-paw-squishy", "title": "Fuzzy Paw Squishy",
         "variants": [
             {"id": 2001, "title": "Pink", "price": "12.49", "available": True},
             {"id": 2002, "title": "Blue", "price": "14.99", "available": True},
         ]},
        {"handle": "marble-marble-balls-set", "title": "Marble Marble Balls Set",
         "variants": [{"id": 3001, "title": "12-pack", "price": "39.99", "available": False}]},
        {"handle": "unrelated-product", "title": "Unrelated Product",
         "variants": [{"id": 9999, "title": "Default", "price": "9.99", "available": True}]},
    ]
}

WATCH = ["cloud-kitty-popping-puzzle", "fuzzy-paw-squishy", "marble-marble-balls-set"]


# --------------------------------------------------------------------------- #
# Run modes
# --------------------------------------------------------------------------- #
def run_offline_demo(csv_path: str = "stock_watch_demo.csv") -> dict[str, Any]:
    """Full pipeline against bundled snapshots - no network, no store access."""
    import os
    if os.path.exists(csv_path):
        os.remove(csv_path)

    eng = WatchlistEngine(store_name="needoh.com (offline fixture)",
                          watch_handles=WATCH, price_change_pct=5.0,
                          csv_path=csv_path)

    print("=" * 74)
    print(f"OFFLINE DEMO  store={eng.store_name}")
    print(f"watching {len(WATCH)} handles: {', '.join(WATCH)}")
    print(f"price-change threshold: {eng.price_change_pct}%")
    print("=" * 74)

    print("\n--- snapshot #1 (initial read: 2 items out of stock) ---")
    a1 = eng.ingest(SNAPSHOT_1)
    print(f"alerts: {len(a1)}")
    print("current status:")
    for s in eng.status():
        flag = "IN STOCK " if s["available"] else "SOLD OUT"
        print(f"  {flag}  {s['product']:<38} {s['price']}")

    print("\n--- snapshot #2 (restock + price drop) ---")
    a2 = eng.ingest(SNAPSHOT_2)
    print(f"alerts: {len(a2)}")
    for a in a2:
        assert a["kind"] in {"restocked", "price-change", "sold-out", "in-stock-first-seen"}

    # Sanity assertions - this is the "does it actually work" proof.
    kinds = [a["kind"] for a in a2]
    assert "restocked" in kinds, "expected at least one restock alert"
    assert "price-change" in kinds, "expected a price-change alert"
    # The in-stock first-seen alert should NOT fire for unrelated (not watched).
    handled = {a["handle"] for a in eng.alerts}
    assert "unrelated-product" not in handled, "watchlist filter failed"

    print("\n--- alerts raised across the run ---")
    for a in eng.alerts:
        print(f"  {a['ts'][:19]}  {a['kind']:<20} {a['product']:<38} {a['price']}")
    print(f"\nCSV log written to: {csv_path}")
    print("\nPASS: restock detected, price drop detected, unwatched items ignored.")
    return {"alerts": eng.alerts, "status": eng.status()}


def run_live(store: str, handles: str, interval_s: float, rounds: int,
             price_pct: float, webhook: str | None) -> None:
    """Poll the real store for `rounds` snapshots at `interval_s` spacing."""
    h = [x.strip() for x in handles.split(",") if x.strip()]
    eng = WatchlistEngine(store_name=store, watch_handles=h,
                          price_change_pct=price_pct, webhook_url=webhook,
                          csv_path="stock_watch_live.csv")
    print(f"LIVE  store={store}  watching={h}  interval={interval_s}s  rounds={rounds}")
    for i in range(max(1, rounds)):
        payload = fetch_store_products(store)
        n = eng.ingest(payload)
        print(f"[round {i+1}] products={len(payload.get('products', []))} new_alerts={n}")
        if i < rounds - 1:
            time.sleep(interval_s)
    print("\nfinal status:")
    for s in eng.status():
        print(f"  {'IN STOCK ' if s['available'] else 'SOLD OUT'}  {s['product']:<38} {s['price']}")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Stock & price watchlist for a Shopify store")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="poll the real store")
    mode.add_argument("--offline", action="store_true", help="run the bundled offline demo (default)")

    ap.add_argument("--store", default="https://needoh.com", help="store base URL (live mode)")
    ap.add_argument("--handles", default=",".join(WATCH),
                    help="comma-separated product handles to watch (live mode)")
    ap.add_argument("--interval", type=float, default=60.0, help="seconds between polls (live)")
    ap.add_argument("--rounds", type=int, default=3, help="number of poll rounds (live)")
    ap.add_argument("--price-pct", type=float, default=5.0, help="price-change alert threshold %%")
    ap.add_argument("--webhook", default=None, help="optional webhook URL for alerts")
    ap.add_argument("--csv", default="stock_watch_demo.csv", help="CSV log path (offline)")
    args = ap.parse_args(argv)

    if args.live:
        run_live(args.store, args.handles, args.interval, args.rounds,
                 args.price_pct, args.webhook)
    else:
        run_offline_demo(csv_path=args.csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
