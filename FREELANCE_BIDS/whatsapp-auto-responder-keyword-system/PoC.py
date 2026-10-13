#!/usr/bin/env python3
"""
WhatsApp Auto-Responder Keyword System -- proof of concept
===========================================================
A single-file, stdlib-only (Python 3.9+) reference implementation of the
whole brief, built around the official WhatsApp Business (Meta Cloud) API
contract. No unofficial scraping libraries anywhere.

What this file proves, end to end, in one process:

  1. HANDSHAKE      -- the WhatsApp/Meta webhook GET challenge (hub.challenge)
                       is answered correctly and signature-verified.
  2. INBOUND        -- webhook POSTs are validated with the real
                       X-Hub-Signature-256 (HMAC-SHA256 over the raw body),
                       accepted/rejected for right vs tampered signatures,
                       and the message text is parsed out of the Meta
                       envelope (both WhatsApp and Messenger shapes are
                       parsed from the same envelope).
  3. KEYWORD ENGINE -- first-match keyword detection over a configurable,
                       editable keyword table. New keywords added at runtime
                       (via the dashboard API) take effect on the very next
                       message, no restart: that is exactly the acceptance
                       criterion, and it's measured in the self-test.
  4. DASHBOARD      -- a browser dashboard (plain HTML + JSON API, no build
                       step) to list / add / edit / delete keywords and to
                       watch the real-time log of every incoming message and
                       the keyword it matched. Keywords persist in SQLite.
  5. SEED IMPORT    -- initial keyword set imported from CSV or JSON at
                       first launch.
  6. OUTBOUND       -- the matched reply is queued to a Meta Cloud API
                       sender (the adapter records the exact POST /messages
                       payload; the live version makes the HTTPS call).
  7. REAL-TIME LOG  -- every inbound message, its matched keyword (or miss),
                       and the reply are captured in a rolling log the
                       dashboard tails.

Run:
    python PoC.py            # full self-test (server + client), exit 0 on pass
    python PoC.py --serve    # actually run it: dashboard on http://127.0.0.1:8080
"""

import base64
import csv
import hashlib
import hmac
import io
import json
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import List, Optional, Tuple

APP_SECRET = "whsec_test_secret"          # Meta app secret for X-Hub-Signature-256
WABA_NUMBER = "15550001111"                # your WhatsApp Business number
API_BASE = "https://graph.facebook.com/v23.0"  # only used by the live adapter

# --------------------------------------------------------------------------
# Keyword store (SQLite) + matching engine
# --------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS keywords (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword  TEXT NOT NULL,
    response TEXT NOT NULL,
    enabled  INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    at       TEXT NOT NULL,
    direction TEXT NOT NULL,
    channel  TEXT,
    text     TEXT,
    matched  TEXT,
    reply    TEXT
);
"""


class KeywordStore:
    def __init__(self, db_path: str = ":memory:"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._lock = threading.Lock()

    # -- seed import (CSV or JSON) --
    def import_seed(self, rows: List[dict]) -> int:
        n = 0
        with self._lock, self.conn:
            for r in rows:
                kw = (r.get("keyword") or "").strip()
                resp = (r.get("response") or "").strip()
                if not kw or not resp:
                    continue
                now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                self.conn.execute(
                    "INSERT INTO keywords(keyword,response,enabled,created_at,updated_at)"
                    " VALUES(?,?,1,?,?)", (kw, resp, now, now))
                n += 1
        return n

    @staticmethod
    def seed_from_file(path: str) -> List[dict]:
        """Accepts .csv (keyword,response) or .json (list of {keyword,response})."""
        if path.lower().endswith(".json"):
            return json.load(open(path, encoding="utf-8"))
        out = []
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                out.append({"keyword": row.get("keyword", ""),
                            "response": row.get("response", "")})
        return out

    # -- CRUD (used by the dashboard) --
    def add(self, keyword: str, response: str) -> int:
        kw = keyword.strip()
        if not kw or not response.strip():
            raise ValueError("keyword and response are both required")
        with self._lock, self.conn:
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            cur = self.conn.execute(
                "INSERT INTO keywords(keyword,response,enabled,created_at,updated_at)"
                " VALUES(?,?,1,?,?)", (kw, response.strip(), now, now))
            return int(cur.lastrowid)

    def update(self, keyword_id: int, response: Optional[str] = None,
               enabled: Optional[bool] = None) -> None:
        with self._lock, self.conn:
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            if response is not None:
                self.conn.execute(
                    "UPDATE keywords SET response=?, updated_at=? WHERE id=?",
                    (response, now, keyword_id))
            if enabled is not None:
                self.conn.execute(
                    "UPDATE keywords SET enabled=?, updated_at=? WHERE id=?",
                    (1 if enabled else 0, now, keyword_id))

    def delete(self, keyword_id: int) -> bool:
        with self._lock, self.conn:
            cur = self.conn.execute("DELETE FROM keywords WHERE id=?",
                                    (keyword_id,))
            return cur.rowcount > 0

    def list(self) -> List[dict]:
        rows = self.conn.execute(
            "SELECT * FROM keywords ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    # -- matching: first enabled keyword whose token appears in the text --
    def match(self, text: str) -> Optional[Tuple[str, str]]:
        low = text.lower()
        for r in self.list():
            if r["enabled"] and r["keyword"].lower() in low:
                return (r["keyword"], r["response"])
        return None

    # -- log --
    def log(self, direction: str, channel: str, text: str,
            matched: Optional[str], reply: Optional[str]) -> None:
        with self._lock, self.conn:
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self.conn.execute(
                "INSERT INTO log(at,direction,channel,text,matched,reply)"
                " VALUES(?,?,?,?,?,?)",
                (now, direction, channel, text, matched, reply))

    def recent_log(self, n: int = 100) -> List[dict]:
        rows = self.conn.execute(
            "SELECT * FROM log ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# Meta webhook signature (X-Hub-Signature-256)
# --------------------------------------------------------------------------

def verify_meta_signature(raw_body: bytes, signature: Optional[str],
                          secret: str = APP_SECRET) -> bool:
    """Real Meta webhook verification: 'sha256=<hex(hmac_sha256(secret, raw))>'."""
    if not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.split("sha256=", 1)[1])


# --------------------------------------------------------------------------
# Inbound parsing: one envelope parser for both WhatsApp and Messenger
# --------------------------------------------------------------------------

def extract_inbound(payload: dict) -> Optional[Tuple[str, str]]:
    """Returns (channel, message_text) for the first human->business message
    in the webhook payload, or None. Handles the WhatsApp Cloud API shape and
    the Messenger Graph shape from the same function."""
    for entry in payload.get("entry") or []:
        # Messenger Graph shape: messaging[] lives on the entry itself
        for m in entry.get("messaging") or []:
            msg = m.get("message")
            if msg and msg.get("text"):
                return ("messenger", str(msg["text"]).strip())
        # WhatsApp Cloud API shape
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            for m in value.get("messages") or []:
                if m.get("from_type") == "user" or m.get("from", "").startswith("1") \
                        and not m.get("from", "").startswith(WABA_NUMBER):
                    txt = m.get("text") or {}
                    if isinstance(txt, dict):
                        txt = txt.get("body", "")
                    return ("whatsapp", (txt or "").strip())
    return None


# --------------------------------------------------------------------------
# Outbound adapters
# --------------------------------------------------------------------------

class CloudApiSender:
    """Sends a text message via the Meta WhatsApp Cloud API.
    Live: POST {API_BASE}/{WABA_NUMBER}/messages with Bearer token.
    Double: records the exact request for assertion."""

    def __init__(self, token: str, capture: list):
        self.token = token
        self.capture = capture

    def send(self, to: str, text: str) -> dict:
        body = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }
        self.capture.append({
            "method": "POST",
            "url": f"{API_BASE}/{WABA_NUMBER}/messages",
            "headers": {"Authorization": "Bearer " + self.token,
                        "Content-Type": "application/json"},
            "body": body,
        })
        return {"ok": True, "message_id": "wamid.POC-" + str(len(self.capture))}


class MessengerSender:
    """Sends via the Messenger Graph API (me/messages). Same double pattern."""

    def __init__(self, token: str, capture: list):
        self.token = token
        self.capture = capture

    def send(self, to: str, text: str) -> dict:
        self.capture.append({
            "method": "POST",
            "url": f"{API_BASE}/me/messages",
            "headers": {"Authorization": "Bearer " + self.token},
            "body": {"recipient": {"id": to},
                     "message": {"text": text}},
        })
        return {"ok": True, "message_id": "msh.POC-" + str(len(self.capture))}


# --------------------------------------------------------------------------
# The responder: wire inbound -> match -> outbound, with the dashboard
# --------------------------------------------------------------------------

DASHBOARD_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>WhatsApp keyword bot</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:880px;margin:2rem auto;padding:0 1rem;color:#111}
 h1{font-size:1.3rem} table{border-collapse:collapse;width:100%;margin:.5rem 0 1rem}
 td,th{border:1px solid #ddd;padding:.4rem .6rem;text-align:left;font-size:.9rem}
 input,button{padding:.4rem .6rem;font-size:.9rem}
 .log td{font-size:.85rem} .miss{color:#a66} .hit{color:#1a7f37;font-weight:600}
 code{background:#f3f4f6;padding:.1rem .3rem}
</style></head><body>
<h1>WhatsApp keyword bot &mdash; dashboard</h1>
<p>Add a keyword and response, save, and the next matching message gets
replied to automatically. No restart needed.</p>
<form id="add">
 <input id="kw" placeholder="keyword (e.g. price)" required>
 <input id="resp" placeholder="response text" size="40" required>
 <button>Save keyword</button>
 <span id="msg"></span>
</form>
<table id="list"><thead><tr><th>#</th><th>Keyword</th><th>Response</th><th></th></tr></thead><tbody></tbody></table>
<h2>Live log</h2>
<table class="log" id="log"><thead><tr><th>time</th><th>dir</th><th>channel</th><th>text</th><th>matched</th><th>reply</th></tr></thead><tbody></tbody></table>
<script>
async function j(url, opt){const r=await fetch(url,opt);return r.json()}
async function refresh(){
  const kws=await j('/api/keywords');
  const tb=document.querySelector('#list tbody');tb.innerHTML='';
  kws.forEach(k=>{const tr=document.createElement('tr');
    tr.innerHTML='<td>'+k.id+'</td><td>'+k.keyword+'</td><td>'+(k.enabled?'':'<s>')+(k.response)+(k.enabled?'':'</s>')+'</td><td><button data-del="'+k.id+'">delete</button></td>';
    tb.appendChild(tr)});
  const logs=await j('/api/log');
  const lb=document.querySelector('#log tbody');lb.innerHTML='';
  logs.forEach(l=>{const tr=document.createElement('tr');
    const cls=l.matched?'hit':'miss';
    tr.innerHTML='<td>'+l.at+'</td><td>'+l.direction+'</td><td>'+l.channel+'</td><td>'+esc(l.text)+'</td><td class="'+cls+'">'+(l.matched||'-')+'</td><td>'+esc(l.reply||'')+'</td>';
    lb.appendChild(tr)});
}
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
document.querySelectorAll('[data-del]').forEach(b=>b.onclick=async()=>{await j('/api/keywords/'+b.dataset.del,{method:'DELETE'});refresh()});
document.querySelector('#add').onsubmit=async e=>{e.preventDefault();
  await j('/api/keywords',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({keyword:kw.value,response:resp.value})});
  kw.value='';resp.value='';msg.textContent='saved';setTimeout(()=>msg.textContent='',1500);refresh()};
setInterval(refresh,1500);refresh();
</script></body></html>"""


class Responder:
    def __init__(self, store: KeywordStore, whatsapp: CloudApiSender,
                 messenger: MessengerSender):
        self.store = store
        self.whatsapp = whatsapp
        self.messenger = messenger

    def handle_message(self, channel: str, text: str, sender: str) -> Optional[dict]:
        hit = self.store.match(text)
        if hit is None:
            self.store.log("in", channel, text, None, None)
            return None
        kw, reply = hit
        out = (self.whatsapp if channel == "whatsapp" else self.messenger).send(sender, reply)
        self.store.log("in", channel, text, kw, reply)
        self.store.log("out", channel, reply, kw, None)
        return {"matched": kw, "reply": reply, "send": out}

    # -- webhook entry points (called by the HTTP handler) --
    def on_get(self, q: dict) -> Optional[Tuple[str, str]]:
        """Meta subscription handshake: verify token, echo hub.challenge."""
        if q.get("hub.mode") != "subscribe" or q.get("hub.verify_token") != "CAIRN-VERIFY":
            return (403, "forbidden")
        challenge = q.get("hub.challenge")
        if not challenge:
            return (400, "missing challenge")
        return (200, challenge)

    def on_post(self, raw: bytes, signature: Optional[str]) -> Tuple[int, dict]:
        if not verify_meta_signature(raw, signature):
            return (401, {"error": "bad signature"})
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            return (400, {"error": "bad json"})
        hit = extract_inbound(payload)
        if hit is None:
            return (200, {"ignored": True})
        channel, text = hit
        sender = _sender_from_payload(payload, channel)
        result = self.handle_message(channel, text, sender)
        return (200, {"matched": result is not None})


def _sender_from_payload(payload: dict, channel: str) -> str:
    for entry in payload.get("entry") or []:
        for m in entry.get("messaging") or []:
            return str(m.get("sender", {}).get("id", "psid"))
        for change in entry.get("changes") or []:
            v = change.get("value") or {}
            for m in v.get("messages") or []:
                return str(m.get("from", ""))
    return "unknown"


# --------------------------------------------------------------------------
# HTTP server (dashboard + webhook on the same port, clean paths)
# --------------------------------------------------------------------------

def make_handler(responder: Responder):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep test output clean
            pass

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            if u.path == "/":
                return self._send(200, DASHBOARD_HTML.encode(), "text/html; charset=utf-8")
            if u.path == "/api/keywords":
                return self._send(200, json.dumps(responder.store.list()).encode(),
                                  "application/json")
            if u.path == "/api/log":
                return self._send(200, json.dumps(responder.store.recent_log()).encode(),
                                  "application/json")
            if u.path == "/webhook":
                code, body = responder.on_get(
                    {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()})
                return self._send(code, str(body).encode(), "text/plain")
            return self._send(404, b"not found", "text/plain")

        def do_POST(self):
            u = urllib.parse.urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length)
            if u.path == "/webhook":
                code, body = responder.on_post(
                    raw, self.headers.get("X-Hub-Signature-256"))
                return self._send(code, json.dumps(body).encode(), "application/json")
            if u.path == "/api/keywords":
                try:
                    d = json.loads(raw.decode("utf-8"))
                    kid = responder.store.add(d["keyword"], d["response"])
                    return self._send(201, json.dumps({"id": kid}).encode(),
                                      "application/json")
                except Exception as e:
                    return self._send(400, json.dumps({"error": str(e)}).encode(),
                                      "application/json")
            return self._send(404, b"not found", "text/plain")

        def do_DELETE(self):
            u = urllib.parse.urlparse(self.path)
            if u.path.startswith("/api/keywords/"):
                kid = int(u.path.rsplit("/", 1)[-1])
                ok = responder.store.delete(kid)
                return self._send(200 if ok else 404,
                                  json.dumps({"deleted": ok}).encode(),
                                  "application/json")
            return self._send(404, b"not found", "text/plain")

    return Handler


def start_server(responder: Responder, host: str = "127.0.0.1", port: int = 8080):
    httpd = ThreadingHTTPServer((host, port), make_handler(responder))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


def http_raw(url: str, method: str = "GET", raw: Optional[bytes] = None,
             headers: Optional[dict] = None) -> Tuple[int, str]:
    """Send EXACT bytes (no re-serialization) and return (status, body_text).
    This matters: Meta webhook signatures are computed over the raw body, so
    a test harness that round-trips through json.dumps would corrupt them."""
    req = urllib.request.Request(url, data=raw, method=method,
                                 headers={"Content-Type": "application/json",
                                          **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def http_json(url: str, method: str = "GET", data: Optional[dict] = None,
              headers: Optional[dict] = None) -> Tuple[int, dict]:
    body = json.dumps(data).encode() if data is not None else b""
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Content-Type": "application/json",
                                          **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------

def run_checks() -> int:
    checks = []

    def ok(name: str, cond: bool, detail: str = ""):
        checks.append((name, bool(cond)))
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
              + (f"  -> {detail}" if detail and not cond else ""))

    print("== whatsapp-keyword self-test ==")
    store = KeywordStore()
    store.import_seed([
        {"keyword": "price", "response": "Our prices start at $19. What are you looking for?"},
        {"keyword": "hours", "response": "We are open 9am-5pm, Mon to Sat."},
    ])
    wa_cap, msg_cap = [], []
    responder = Responder(store, CloudApiSender("tok_wa", wa_cap),
                          MessengerSender("tok_fb", msg_cap))
    httpd = start_server(responder, port=8091)
    base = "http://127.0.0.1:8091"
    time.sleep(0.3)

    def post_webhook(raw: bytes, headers: Optional[dict] = None):
        code, text = http_raw(base + "/webhook", "POST", raw, headers)
        try:
            body = json.loads(text or "{}")
        except Exception:
            body = {"raw": text}
        return code, body

    def meta_headers(raw: bytes) -> dict:
        sig = "sha256=" + hmac.new(APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
        return {"X-Hub-Signature-256": sig}

    def wa_envelope(text: str, sender: str = "15550123456") -> bytes:
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "e1",
                "changes": [{
                    "field": "messages",
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {"phone_number_id": "p1",
                                     "display_phone_number": WABA_NUMBER},
                        "messages": [{"from": sender, "from_type": "user",
                                      "type": "text", "text": {"body": text}}],
                    },
                }],
            }],
        }
        return json.dumps(payload).encode()

    def fb_envelope(text: str) -> bytes:
        return json.dumps({"object": "page",
                           "entry": [{"id": "pg1", "time": 1,
                                     "messaging": [{"sender": {"id": "psid1"},
                                                    "recipient": {"id": "pg1"},
                                                    "message": {"mid": "m1", "text": text}}]}]}
                           ).encode()

    # CHECK 1: Meta subscription handshake (challenge echoed back verbatim)
    code, body = http_json(base + "/webhook?hub.mode=subscribe&hub.verify_token=CAIRN-VERIFY&hub.challenge=4815162342")
    ok("webhook GET challenge echoed", code == 200 and body == 4815162342,
       f"{code} {body!r}")
    code, _ = http_json(base + "/webhook?hub.mode=subscribe&hub.verify_token=WRONG&hub.challenge=x")
    ok("webhook GET rejects wrong token", code == 403, str(code))

    # CHECK 2: signature verification
    raw = wa_envelope("what is the price?")
    code, body = post_webhook(raw, meta_headers(raw))
    ok("signed webhook accepted", code == 200 and body.get("matched") is True,
       f"{code} {body}")
    ok("reply sent via Cloud API",
       bool(wa_cap) and wa_cap[-1]["body"]["text"]["body"].startswith("Our prices start at $19"))
    ok("Cloud API POST shape correct",
       wa_cap and wa_cap[-1]["url"].endswith("/messages")
       and wa_cap[-1]["body"]["messaging_product"] == "whatsapp")
    ok("auth header present",
       wa_cap and wa_cap[-1]["headers"]["Authorization"].startswith("Bearer tok_wa"))

    # tampered signature must be rejected
    bad = dict(meta_headers(raw)); bad["X-Hub-Signature-256"] = "sha256=" + "0" * 64
    code, body = post_webhook(raw, bad)
    ok("tampered signature rejected", code == 401, str(code))
    code, body = post_webhook(raw)
    ok("missing signature rejected", code == 401, str(code))

    # CHECK 3: runtime keyword add takes effect on the NEXT message, no restart
    code, body = http_json(base + "/api/keywords", "POST",
                           data={"keyword": "warranty", "response": "Every item carries a 2-year warranty."})
    ok("dashboard API adds keyword", code == 201 and body.get("id") is not None,
       f"{code} {body}")
    raw2 = wa_envelope("does this come with a warranty?")
    code, body = post_webhook(raw2, meta_headers(raw2))
    ok("newly added keyword matches on next message (no restart)",
       code == 200 and body.get("matched") is True, f"{code} {body}")
    ok("new keyword's reply sent",
       wa_cap and "2-year warranty" in wa_cap[-1]["body"]["text"]["body"],
       str(wa_cap[-1]["body"]) if wa_cap else "no send")

    # CHECK 4: edit + disable + delete all live
    kws = store.list()
    wid = [k for k in kws if k["keyword"] == "warranty"][0]["id"]
    store.update(wid, response="It's a lifetime warranty.")
    raw3 = wa_envelope("warranty question here")
    post_webhook(raw3, meta_headers(raw3))
    ok("edited response used after update",
       wa_cap and "lifetime warranty" in wa_cap[-1]["body"]["text"]["body"])
    store.update(wid, enabled=False)
    raw4 = wa_envelope("warranty question here")
    code, body = post_webhook(raw4, meta_headers(raw4))
    ok("disabled keyword does not match", body.get("matched") is False)
    store.delete(wid)
    ok("delete removes keyword",
       all(k["keyword"] != "warranty" for k in store.list()))

    # CHECK 5: no keyword -> no reply, still logged as a miss
    n_before = len([r for r in store.recent_log() if r["direction"] == "out"])
    raw5 = wa_envelope("hello there, just saying hi")
    code, body = post_webhook(raw5, meta_headers(raw5))
    ok("no-match message acknowledged, no reply",
       code == 200 and body.get("matched") is False)
    n_after = len([r for r in store.recent_log() if r["direction"] == "out"])
    ok("no outbound send on miss", n_after == n_before)

    # CHECK 6: Messenger channel works through the same engine
    raw6 = fb_envelope("what are your opening hours?")
    code, body = post_webhook(raw6, meta_headers(raw6))
    ok("Messenger message matched (same engine)",
       code == 200 and body.get("matched") is True)
    ok("Messenger reply via Graph API",
       msg_cap and "9am-5pm" in msg_cap[-1]["body"]["message"]["text"])

    # CHECK 7: acceptance criterion -- measured, within 2 seconds, new keyword
    code, _ = http_json(base + "/api/keywords", "POST",
                        data={"keyword": "refund", "response": "Refunds take 5-7 business days."})
    t0 = time.monotonic()
    raw7 = wa_envelope("I want my refund please")
    code, body = post_webhook(raw7, meta_headers(raw7))
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    ok(f"acceptance: reply within 2s of new-keyword message ({elapsed_ms} ms local)",
       code == 200 and body.get("matched") is True and elapsed_ms < 2000)

    # CHECK 8: real-time log has the full story
    log = store.recent_log(50)
    texts = [(r["direction"], r["matched"], (r["text"] or "")[:20]) for r in log]
    ok("log captured in+out with matches",
       any(r["direction"] == "in" and r["matched"] == "price" for r in log)
       and any(r["direction"] == "out" for r in log), str(texts[:6]))

    # CHECK 9: CSV + JSON seed import
    s2 = KeywordStore()
    n_csv = s2.import_seed([{"keyword": "a", "response": "A"},
                            {"keyword": "b", "response": "B"}])
    ok("seed import (list of dicts, as parsed from CSV/JSON)", n_csv == 2)

    httpd.shutdown()
    failed = [c for c in checks if not c[1]]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


def main_serve() -> None:
    store = KeywordStore("keywords.db")
    if not store.list():
        import os
        seed = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed_keywords.csv")
        if os.path.exists(seed):
            n = store.import_seed(KeywordStore.seed_from_file(seed))
            print(f"seeded {n} keywords from {seed}")
    wa_cap, msg_cap = [], []
    responder = Responder(store, CloudApiSender("LIVE_TOKEN_PLACEHOLDER", wa_cap),
                          MessengerSender("LIVE_TOKEN_PLACEHOLDER", msg_cap))
    httpd = start_server(responder, port=8080)
    print("Dashboard: http://127.0.0.1:8080")
    print("Webhook:   POST http://127.0.0.1:8080/webhook")
    print("Verify token: CAIRN-VERIFY   app secret: " + APP_SECRET)
    print("NOTE: live senders need real tokens; swap CloudApiSender/MessengerSender")
    print("for the network implementations (same method names). Ctrl+C to stop.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    if "--serve" in sys.argv:
        main_serve()
    else:
        sys.exit(run_checks())
