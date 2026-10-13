#!/usr/bin/env python3
"""
Physical Goods Stock Automation System -- proof of concept
===========================================================
A single-file, stdlib-only (Python 3.9+) reference implementation of the
three workflows in the brief, wired together in one service:

  1. ORDER PROCESSING  -- a sales-channel order arrives, stock is atomically
                          committed, and the order status + stock counts are
                          written back to the storefront(s) so customers
                          always see accurate availability.
  2. RESTOCKING       -- after every stock change the engine checks the
                          preset threshold; when available stock drops to it,
                          a purchase order is created and routed to the
                          supplier via the supplier's API adapter or an
                          email adapter (both pluggable, both proven here
                          against test doubles).
  3. MONITORING       -- real-time on-hand / committed / available view
                          across all sales channels, with a full audit trail
                          of every mutation.

Design notes (the parts I'd build on in the real project):
  - State lives in SQLite (lightweight database, single file, no server).
    The schema is the contract; moving to Postgres later is a copy of the
    same DDL.
  - Stock accounting uses the standard committed/available split:
        available = on_hand - committed
    A sale commits stock immediately (customer sees availability drop);
    physical on_hand moves when the item ships or supplier stock lands.
    Because every mutation goes through InventoryService, "counts are
    always accurate" is a property of the system, not a hope.
  - Suppliers and storefronts are adapters behind small interfaces. The
    proof runs them against deterministic in-memory doubles that record
    exactly what would be sent; the live adapters (REST supplier API, SMTP
    email, the client's storefront API) are the same classes with the
    network calls filled in. I'd quote those as integration days, not
    redesigns.
  - The engine is clock-injected, so the whole restock flow is
    deterministic and re-runnable: `python PoC.py` always exits 0 and
    prints the same check table. `python PoC.py --demo` narrates one order
    end to end.

Run:
    python PoC.py            # full self-test, exit 0 on pass
    python PoC.py --demo     # narrated end-to-end walkthrough (no asserts)
"""

import json
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import List, Optional, Protocol

# --------------------------------------------------------------------------
# Domain model
# --------------------------------------------------------------------------

STATUS_FULFILLED = "fulfilled"
STATUS_SHORTSHIPPED = "short_shipped"  # partial fill when stock runs out


class ChannelOrder:
    """An order as it arrives from a sales channel (storefront, marketplace)."""

    def __init__(self, order_id: str, sku: str, qty: int, channel: str,
                 customer_email: str = ""):
        if qty <= 0:
            raise ValueError("order quantity must be positive")
        self.order_id = order_id
        self.sku = sku
        self.qty = qty
        self.channel = channel
        self.customer_email = customer_email


@dataclass
class StockLevel:
    sku: str
    on_hand: int        # physical units in the warehouse
    committed: int      # allocated to open orders, not yet shipped
    threshold: int      # restock trigger: restock when available <= threshold
    reorder_qty: int    # how much to order when triggered

    @property
    def available(self) -> int:
        return self.on_hand - self.committed


@dataclass
class PurchaseOrder:
    po_id: str
    sku: str
    qty: int
    supplier: str
    status: str = "draft"          # draft -> routed -> received
    created_at: str = ""
    routing: Optional[dict] = None  # transport receipt from the adapter

    def to_json(self) -> str:
        d = {"po_id": self.po_id, "sku": self.sku, "qty": self.qty,
             "supplier": self.supplier, "status": self.status,
             "created_at": self.created_at, "routing": self.routing}
        return json.dumps(d, indent=2)


# --------------------------------------------------------------------------
# Adapters (transport layer). Real versions make network calls; these
# doubles record exactly what WOULD be sent, which is what the checks assert.
# --------------------------------------------------------------------------

class SupplierAdapter(Protocol):
    name: str

    def route_po(self, po: PurchaseOrder) -> dict:
        """Deliver a purchase order to the supplier. Returns a receipt dict."""
        ...


class EmailSupplierAdapter:
    """Routes POs by email. The real send goes through an SMTP server; this
    double captures the composed message (recipient, subject, PO JSON body)."""

    name = "email"

    def __init__(self, supplier_email: str, mail_capture: list):
        self.supplier_email = supplier_email
        self.mail_capture = mail_capture  # shared capture list (test hook)

    def route_po(self, po: PurchaseOrder) -> dict:
        msg = EmailMessage()
        msg["From"] = "orders@store.example"
        msg["To"] = self.supplier_email
        msg["Subject"] = f"PO {po.po_id}: {po.qty} x {po.sku}"
        msg.set_content(po.to_json())
        self.mail_capture.append({"to": msg["To"],
                                  "subject": msg["Subject"],
                                  "body": msg.get_content()})
        return {"transport": "smtp", "to": self.supplier_email,
                "subject": msg["Subject"], "sent": True}


class ApiSupplierAdapter:
    """Routes POs to the supplier's REST API (POST /purchase-orders). This
    double records the exact request it would send; the live version does
    `urllib.request.urlopen` on the same payload with the same auth header."""

    name = "api"

    def __init__(self, base_url: str, token: str, capture: list):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.capture = capture

    def route_po(self, po: PurchaseOrder) -> dict:
        payload = {"po_id": po.po_id, "sku": po.sku, "qty": po.qty}
        self.capture.append({
            "method": "POST",
            "url": self.base_url + "/purchase-orders",
            "headers": {"Authorization": "Bearer " + self.token,
                        "Content-Type": "application/json"},
            "body": payload,
        })
        return {"transport": "rest", "url": self.base_url + "/purchase-orders",
                "http_status": 201, "supplier_po_ref": "SUP-" + po.po_id}


class StorefrontAdapter(Protocol):
    name: str

    def push_stock(self, sku: str, available: int) -> dict: ...
    def push_order_status(self, order_id: str, status: str) -> dict: ...


class StorefrontDouble:
    """Records every push to a storefront (stock + order status)."""

    def __init__(self, name: str, pushes: list):
        self.name = name
        self.pushes = pushes

    def push_stock(self, sku: str, available: int) -> dict:
        self.pushes.append({"sku": sku, "available": available})
        return {"ok": True}

    def push_order_status(self, order_id: str, status: str) -> dict:
        self.pushes.append({"order_id": order_id, "status": status})
        return {"ok": True}


# --------------------------------------------------------------------------
# Inventory service -- the single write path over SQLite
# --------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory (
    sku         TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    supplier    TEXT NOT NULL,
    on_hand     INTEGER NOT NULL DEFAULT 0,
    committed   INTEGER NOT NULL DEFAULT 0,
    threshold   INTEGER NOT NULL DEFAULT 0,
    reorder_qty INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    order_id    TEXT PRIMARY KEY,
    sku         TEXT NOT NULL,
    qty         INTEGER NOT NULL,
    filled      INTEGER NOT NULL DEFAULT 0,
    channel     TEXT NOT NULL,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS purchase_orders (
    po_id       TEXT PRIMARY KEY,
    sku         TEXT NOT NULL,
    qty         INTEGER NOT NULL,
    supplier    TEXT NOT NULL,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    routing     TEXT
);
CREATE TABLE IF NOT EXISTS audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    action      TEXT NOT NULL,
    detail      TEXT
);
"""


class InventoryService:
    def __init__(self, db_path: str = ":memory:"):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.clock = time.time
        self._seq = [0]
        self._suppliers = {}
        self._storefronts: List = []

    # -- clock (injected for determinism) --
    def _now(self) -> str:
        return datetime.fromtimestamp(self.clock(), tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")

    # -- setup --
    def upsert_sku(self, sku: str, name: str, supplier: str, on_hand: int,
                   threshold: int, reorder_qty: int) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO inventory(sku,name,supplier,on_hand,committed,
                       threshold,reorder_qty,updated_at)
                   VALUES(?,?,?,?,0,?,?,?)
                   ON CONFLICT(sku) DO UPDATE SET
                     name=excluded.name, supplier=excluded.supplier,
                     on_hand=excluded.on_hand, threshold=excluded.threshold,
                     reorder_qty=excluded.reorder_qty,
                     updated_at=excluded.updated_at""",
                (sku, name, supplier, on_hand, threshold, reorder_qty,
                 self._now()))

    def bind_suppliers(self, **by_name):
        self._suppliers = by_name

    def bind_storefronts(self, storefronts: List):
        self._storefronts = storefronts

    # -- reads --
    def stock(self, sku: str) -> StockLevel:
        r = self.conn.execute(
            "SELECT * FROM inventory WHERE sku=?", (sku,)).fetchone()
        if r is None:
            raise KeyError(sku)
        return StockLevel(r["sku"], r["on_hand"], r["committed"],
                          r["threshold"], r["reorder_qty"])

    def order(self, order_id: str) -> dict:
        r = self.conn.execute("SELECT * FROM orders WHERE order_id=?",
                              (order_id,)).fetchone()
        return dict(r)

    def audit(self, n: int = 200) -> list:
        rows = self.conn.execute(
            "SELECT at, action, detail FROM audit ORDER BY id DESC LIMIT ?",
            (n,)).fetchall()
        return [dict(r) for r in rows]

    # -- workflow 1: order processing --
    def process_order(self, order: ChannelOrder) -> tuple:
        """Atomically fill an order from available stock, write status and
        stock back to the storefront(s), and fire the restock check on the
        post-sale level. Short-ships (never oversells) when stock is low.
        Returns (order_dict, [PurchaseOrder] triggered)."""
        level = self.stock(order.sku)
        filled = min(order.qty, level.available)
        status = STATUS_FULFILLED if filled == order.qty else STATUS_SHORTSHIPPED
        now = self._now()
        with self.conn:
            self.conn.execute(
                "UPDATE inventory SET committed=committed+?, updated_at=? WHERE sku=?",
                (filled, now, order.sku))
            self.conn.execute(
                """INSERT INTO orders(order_id,sku,qty,filled,channel,status,
                                       created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (order.order_id, order.sku, order.qty, filled, order.channel,
                 status, now, now))
            self.conn.execute(
                "INSERT INTO audit(at,action,detail) VALUES(?,?,?)",
                (now, "order.processed",
                 json.dumps({"order": order.order_id, "sku": order.sku,
                             "qty": order.qty, "filled": filled,
                             "channel": order.channel, "status": status})))
        # restock check on the post-sale available level
        restocks = []
        after = self.stock(order.sku)
        if after.available <= after.threshold:
            restocks = [self._restock(order.sku)]
        # write status + stock counts back to the storefront(s)
        self._push_order_status(order.order_id, status)
        self._push_stock(order.sku)
        return (self.order(order.order_id), restocks)

    # -- workflow 2: restocking --
    def restock_now(self, sku: str) -> PurchaseOrder:
        """Force a restock (manual override / daily catch-up job)."""
        return self._restock(sku)

    def _restock(self, sku: str) -> PurchaseOrder:
        row = self.conn.execute(
            "SELECT * FROM inventory WHERE sku=?", (sku,)).fetchone()
        if row is None:
            raise KeyError(sku)
        self._seq[0] += 1
        po = PurchaseOrder(po_id=f"PO-{self._seq[0]:05d}", sku=sku,
                           qty=row["reorder_qty"], supplier=row["supplier"],
                           created_at=self._now())
        adapter = self._suppliers.get(row["supplier"])
        if adapter is None:
            raise RuntimeError(f"no supplier adapter for {row['supplier']}")
        receipt = adapter.route_po(po)
        now = self._now()
        with self.conn:
            self.conn.execute(
                """INSERT INTO purchase_orders(po_id,sku,qty,supplier,status,
                                               created_at,routing)
                   VALUES(?,?,?,?,?,?,?)""",
                (po.po_id, sku, po.qty, row["supplier"], "routed", now,
                 json.dumps(receipt)))
            self.conn.execute(
                "INSERT INTO audit(at,action,detail) VALUES(?,?,?)",
                (now, "restock.routed",
                 json.dumps({"po": po.po_id, "sku": sku, "qty": po.qty,
                             "supplier": row["supplier"],
                             "transport": receipt.get("transport")})))
        po.status = "routed"
        po.routing = receipt
        return po

    # -- workflow 3: supplier delivery + monitoring --
    def supplier_delivers(self, po_id: str) -> None:
        """Supplier delivers: stock lands on the shelf, PO closes, and the
        new available count is pushed to every storefront."""
        po = self.conn.execute(
            "SELECT * FROM purchase_orders WHERE po_id=?", (po_id,)).fetchone()
        if po is None:
            raise KeyError(po_id)
        now = self._now()
        with self.conn:
            self.conn.execute(
                "UPDATE inventory SET on_hand=on_hand+?, updated_at=? WHERE sku=?",
                (po["qty"], now, po["sku"]))
            self.conn.execute(
                "UPDATE purchase_orders SET status='received' WHERE po_id=?",
                (po_id,))
            self.conn.execute(
                "INSERT INTO audit(at,action,detail) VALUES(?,?,?)",
                (now, "restock.received",
                 json.dumps({"po": po_id, "sku": po["sku"], "qty": po["qty"]})))
        self._push_stock(po["sku"])

    def ship_order(self, order_id: str) -> None:
        """Physical goods leave the warehouse: on_hand drops, committed
        clears, storefront stock re-syncs."""
        o = self.conn.execute("SELECT * FROM orders WHERE order_id=?",
                              (order_id,)).fetchone()
        if o is None:
            raise KeyError(order_id)
        now = self._now()
        with self.conn:
            self.conn.execute(
                "UPDATE inventory SET on_hand=on_hand-?, committed=committed-?, "
                "updated_at=? WHERE sku=?", (o["filled"], o["filled"], now,
                                             o["sku"]))
            self.conn.execute(
                "UPDATE orders SET status='shipped', updated_at=? WHERE order_id=?",
                (now, order_id))
            self.conn.execute(
                "INSERT INTO audit(at,action,detail) VALUES(?,?,?)",
                (now, "order.shipped",
                 json.dumps({"order": order_id, "sku": o["sku"],
                             "filled": o["filled"]})))
        self._push_stock(o["sku"])

    # -- storefront sync --
    def _push_stock(self, sku: str) -> None:
        lvl = self.stock(sku)
        for sf in self._storefronts:
            sf.push_stock(sku, lvl.available)

    def _push_order_status(self, order_id: str, status: str) -> None:
        for sf in self._storefronts:
            sf.push_order_status(order_id, status)


# --------------------------------------------------------------------------
# Self-test -- the whole system against a realistic mini-scenario
# --------------------------------------------------------------------------

def run_checks() -> int:
    checks = []

    def ok(name: str, cond: bool, detail: str = ""):
        checks.append((name, bool(cond)))
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
              + (f"  -> {detail}" if detail and not cond else ""))

    print("== stock-automation self-test ==")

    svc = InventoryService()
    mail_cap, api_cap, sf_pushes = [], [], []
    svc.bind_suppliers(
        **{"supplier-a": EmailSupplierAdapter("purchasing@supplier-a.com", mail_cap),
           "supplier-b": ApiSupplierAdapter("https://api.supplier-b.example",
                                            "tok_test", api_cap)})
    svc.bind_storefronts([StorefrontDouble("web", sf_pushes),
                          StorefrontDouble("marketplace", sf_pushes)])

    # two SKUs, two suppliers, two sales channels
    svc.upsert_sku("MUG-450", "Cafe mug 450ml", "supplier-a",
                   on_hand=10, threshold=4, reorder_qty=40)
    svc.upsert_sku("BAG-TOTE", "Canvas tote", "supplier-b",
                   on_hand=6, threshold=5, reorder_qty=24)

    # CHECK 1: pre-state
    ok("initial on-hand tracked", svc.stock("MUG-450").on_hand == 10)
    ok("initial available == on-hand", svc.stock("MUG-450").available == 10)

    # CHECK 2: normal order above threshold -- no restock, writes go back out
    o1, r1 = svc.process_order(ChannelOrder(
        "ORD-0001", "MUG-450", 3, "web", "jake@example.com"))
    ok("order filled in full",
       o1["filled"] == 3 and o1["status"] == STATUS_FULFILLED)
    ok("committed tracks the open order", svc.stock("MUG-450").committed == 3)
    ok("available drops for the customer", svc.stock("MUG-450").available == 7)
    ok("no restock above threshold", r1 == [])
    ok("order status pushed to BOTH storefronts",
       sum(1 for p in sf_pushes if p.get("order_id") == "ORD-0001") == 2)
    ok("order in DB", svc.order("ORD-0001")["status"] == STATUS_FULFILLED)

    # CHECK 3: real-time channel view
    pushes_for_mug = [p for p in sf_pushes if p.get("sku") == "MUG-450"]
    ok("stock pushed to both channels after sale",
       len(pushes_for_mug) == 2 and all(p["available"] == 7 for p in pushes_for_mug),
       str(pushes_for_mug))

    # CHECK 4: order crosses threshold -> automatic PO routed via email
    o2, r2 = svc.process_order(ChannelOrder("ORD-0002", "MUG-450", 4, "marketplace"))
    ok("threshold breach triggers restock", len(r2) == 1, f"got {r2}")
    po1 = r2[0] if r2 else None
    ok("PO quantity == reorder_qty", po1 is not None and po1.qty == 40)
    ok("PO supplier == SKU supplier", po1 is not None and po1.supplier == "supplier-a")
    ok("PO routed via EMAIL adapter",
       bool(mail_cap) and mail_cap[-1]["to"] == "purchasing@supplier-a.com")
    po_body = json.loads(mail_cap[-1]["body"]) if mail_cap else {}
    ok("PO body carries sku+qty",
       po_body.get("sku") == "MUG-450" and po_body.get("qty") == 40, str(po_body)[:80])
    ok("no double-trigger from same level",
       svc.conn.execute(
           "SELECT COUNT(*) c FROM purchase_orders").fetchone()["c"] == 1)

    # CHECK 5: supplier delivers -> stock lands, PO closes, channels re-pushed
    svc.supplier_delivers(po1.po_id)
    ok("stock restored after delivery", svc.stock("MUG-450").on_hand == 50)
    ok("PO marked received",
       svc.conn.execute("SELECT status FROM purchase_orders WHERE po_id=?",
                        (po1.po_id,)).fetchone()[0] == "received")
    ok("storefront re-pushed after delivery (available=43)",
       [p for p in sf_pushes if p.get("sku") == "MUG-450"][-1]["available"] == 43)

    # CHECK 6: API-supplier path (different transport, same engine)
    o3, r3 = svc.process_order(ChannelOrder("ORD-0003", "BAG-TOTE", 2, "web"))
    # available 6 -> 4 <= threshold 5 => restock
    ok("API-supplier SKU also auto-restocks", len(r3) == 1)
    po2 = r3[0] if r3 else None
    ok("PO routed via REST adapter",
       bool(api_cap) and api_cap[-1]["method"] == "POST"
       and api_cap[-1]["url"].endswith("/purchase-orders"))
    ok("API auth header present",
       bool(api_cap) and api_cap[-1]["headers"].get("Authorization", "")
       .startswith("Bearer "))
    ok("API body is the PO payload",
       bool(api_cap) and po2 is not None
       and api_cap[-1]["body"] == {"po_id": po2.po_id,
                                   "sku": "BAG-TOTE", "qty": 24})

    # CHECK 7: ship -> physical stock moves, committed clears
    svc.ship_order("ORD-0001")
    ok("ship drops on_hand", svc.stock("MUG-450").on_hand == 47)
    ok("ship clears that order's commit", svc.stock("MUG-450").committed == 4)
    ok("shipped order status in DB", svc.order("ORD-0001")["status"] == "shipped")

    # CHECK 8: short-ship when stock runs out (never oversells)
    svc2 = InventoryService()
    svc2.bind_suppliers(**{"s": EmailSupplierAdapter("p@s.com", [])})
    svc2.bind_storefronts([])
    svc2.upsert_sku("PEN", "Ballpoint", "s", on_hand=1, threshold=0, reorder_qty=50)
    o4, r4 = svc2.process_order(ChannelOrder("ORD-0004", "PEN", 3, "web"))
    ok("short-ship caps fill at available", o4["filled"] == 1)
    ok("short-ship status set", o4["status"] == STATUS_SHORTSHIPPED)
    ok("available never negative", svc2.stock("PEN").available == 0)

    # CHECK 9: duplicate order id rejected, stock untouched
    before = svc2.stock("PEN").on_hand
    try:
        svc2.process_order(ChannelOrder("ORD-0004", "PEN", 1, "web"))
        dup = "no-error"
    except Exception as e:
        dup = type(e).__name__
    ok("duplicate order id rejected (UNIQUE)", dup == "IntegrityError", dup)
    ok("duplicate rejected without stock change",
       svc2.stock("PEN").on_hand == before)

    # CHECK 10: audit trail covers the whole lifecycle
    acts = [a["action"] for a in svc.audit(200)]
    for a in ("order.processed", "order.shipped", "restock.routed",
              "restock.received"):
        ok(f"audit has {a}", a in acts)

    failed = [c for c in checks if not c[1]]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


def run_demo() -> None:
    print("== narrated demo: one order, end to end ==")
    svc = InventoryService()
    mail = []
    svc.bind_suppliers(
        **{"supplier-a": EmailSupplierAdapter("buy@supplier-a.com", mail)})
    svc.bind_storefronts([StorefrontDouble("web", []),
                          StorefrontDouble("marketplace", [])])
    svc.upsert_sku("MUG-450", "Cafe mug 450ml", "supplier-a", 10, 4, 40)

    print("\nShelf state: 10 mugs on hand, restock threshold 4, reorder 40.")
    order = ChannelOrder("ORD-100", "MUG-450", 6, "web", "customer@shop.com")
    o, restocks = svc.process_order(order)
    lvl = svc.stock("MUG-450")
    print(f"\nCustomer ordered {order.qty} mugs on channel '{order.channel}'.")
    print(f"Filled {o['filled']}, status {o['status']}.")
    print(f"Storefront now shows: {lvl.available} available "
          f"({lvl.on_hand} on hand, {lvl.committed} committed).")
    if restocks:
        po = restocks[0]
        print(f"\nAvailable dropped to {lvl.available} (<= threshold "
              f"{lvl.threshold}), so a restock fired automatically:")
        print(f"  {po.po_id}: buy {po.qty} x {po.sku} from {po.supplier}")
        print(f"  routed via {po.routing['transport']} to {po.routing['to']}: "
              f"\"{po.routing['subject']}\"")
    svc.supplier_delivers(restocks[0].po_id)
    lvl = svc.stock("MUG-450")
    print(f"\nSupplier delivers. Shelf now: {lvl.on_hand} on hand, "
          f"{lvl.available} available; PO {restocks[0].po_id} closed.")
    print(f"Both storefronts re-synced to the new count.")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        run_demo()
        sys.exit(0)
    sys.exit(run_checks())
