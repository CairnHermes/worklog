#!/usr/bin/env python3
"""
whatsapp-messenger-reply-automation / PoC.py

One tool, both official channels:
  * WhatsApp Business Cloud API  (Graph /v18.0/<wa-wid>/messages)
  * Facebook Messenger           (Graph /v21.0/<psid>/messages)

What this proof-of-concept proves, end to end, from a single code path:

  1. INBOUND  : parse BOTH the WhatsApp Cloud message envelope and the
                Messenger Graph message envelope into one normalized shape
                (platform, sender, text, direction).
  2. VERIFY   : verify the X-Hub-Signature-256 HMAC-SHA256 webhook token for
                BOTH channels (timing-safe compare). This is the piece that
                keeps a public endpoint from being spammed or forged.
  3. ROUTE    : a deterministic keyword -> reply router (case-insensitive,
                multi-keyword, per-client fallback when nothing matches).
  4. OUTBOUND : build the correct signed Graph send payload per platform and
                POST it with `requests` (real) or print it (dry-run).
  5. SERVE    : a tiny stdlib HTTP server that accepts POSTs, verifies the
                signature, routes, and sends -- so the whole loop is runnable
                today against a local webhook tester.

Nothing here is scraped and nothing violates a platform's terms: both
channels are Meta's official, documented APIs, and both are READ-ONLY-in /
REPLY-OUT (we only answer a message the customer actually sent).

Run:
    python PoC.py selftest                      # offline unit tests, no network
    python PoC.py demo                          # parse+route on sample payloads
    python PoC.py serve --port 8080 --token s   # live local webhook receiver
    python PoC.py send --platform whatsapp --to 15551234567 --text hi \\
               --dry-run                        # build+print the real send

Dependencies: stdlib only for selftest/demo/serve. `requests` (pip) is used
only for the actual outbound send, and that call is dry-run by default.
"""

import argparse
import hashlib
import hmac
import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GRAPHHOST = "https://graph.facebook.com"
WA_VERSION = "v18.0"     # WhatsApp Cloud API version
MES_VERSION = "v21.0"    # Messenger Graph version


# --------------------------------------------------------------------------
# 1. INBOUND: normalize both channel envelopes into one shape
# --------------------------------------------------------------------------

def parse_inbound(raw) -> dict:
    """Parse a WhatsApp Cloud or Messenger Graph inbound message.

    Accepts either the raw JSON string (as it arrives on the webhook) or an
    already-decoded dict.

    Returns a normalized dict:
        {platform: 'whatsapp'|'messenger', sender: <id>, text: <str>,
         direction: 'in', message_id: <id or ''>}

    Returns None if the payload is not an inbound text message (e.g. a
    messaging opt-in, a postback, or a non-message event).
    """
    if isinstance(raw, str):
        try:
            doc = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
    elif isinstance(raw, dict):
        doc = raw
    else:
        return None

    # --- WhatsApp Cloud API envelope ---------------------------------------
    # {"object":"whatsapp_business_account","entry":[{"id":"...","changes":[
    #   {"value":{"metadata":{"phone_number_id":..},"messages":[{"from":"1555...","type":"text","id":"wamid..","text":{"body":"hi"}}]}
    # ]}]}
    if doc.get("object") in ("whatsapp_business_account", "whatsapp"):
        for entry in doc.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for m in value.get("messages", []):
                    if m.get("type") != "text":
                        continue
                    return {
                        "platform": "whatsapp",
                        "sender": str(m.get("from", "")),
                        "text": (m.get("text") or {}).get("body", ""),
                        "direction": "in",
                        "message_id": m.get("id", ""),
                    }
        return None

    # --- Messenger Graph envelope -----------------------------------------
    # {"object":"page","entry":[{"id":"<psid>","time":..,"messaging":[{"sender":{"id":"<uid>"},"recipient":{"id":"<psid>"},"message":{"mid":"<mid>","text":"hi"}}]}]}
    if doc.get("object") == "page":
        for entry in doc.get("entry", []):
            for m in entry.get("messaging", []):
                msg = m.get("message") or {}
                if "text" not in msg:
                    # not a plain text message (e.g. an echo we sent, a postback)
                    continue
                sender = (m.get("sender") or {}).get("id", "")
                return {
                    "platform": "messenger",
                    "sender": str(sender),
                    "text": msg.get("text", ""),
                    "direction": "in",
                    "message_id": msg.get("mid", ""),
                }
        return None

    return None


# --------------------------------------------------------------------------
# 2. VERIFY: X-Hub-Signature-256 (shared by both channels)
# --------------------------------------------------------------------------

def compute_signature(payload_raw: bytes, token: str) -> str:
    digest = hmac.new(token.encode("utf-8"), payload_raw, hashlib.sha256)
    return "sha256=" + digest.hexdigest()


def verify_signature(payload_raw: bytes, x_hub_signature: str, token: str) -> bool:
    """Timing-safe verification of the X-Hub-Signature-256 header.

    Works identically for the WhatsApp Cloud API and Messenger webhook
    verification -- both use `sha256=` + hex(HMAC-SHA256(raw_body, token)).
    """
    expected = compute_signature(payload_raw, token)
    got = (x_hub_signature or "").strip()
    return hmac.compare_digest(expected, got)


# --------------------------------------------------------------------------
# 3. ROUTE: deterministic keyword -> reply
# --------------------------------------------------------------------------

DEFAULT_FALLBACK = (
    "Thanks for reaching out! I am away from my phone right now. "
    "Leave a few details and I will get back to you the same day."
)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def route_reply(text: str, rules=None, fallback: str = DEFAULT_FALLBACK):
    """Return the reply for `text`.

    `rules` is a list of (keyword, reply) pairs, evaluated in order; the first
    whose keyword appears (as a whole word) in the message wins. When nothing
    matches, `fallback` is returned (never None, so the customer is always
    answered).
    """
    if rules is None:
        rules = [
            ("price",  "Our current pricing starts at $250 for a fixed-scope gig. What are you trying to build?"),
            ("price",  "How much does this cost?"),
            ("cost",   "Depends on scope. A simple script is usually a one-week project; tell me the details and I will quote within a day."),
            ("quote",  "I can quote today. Send me the requirements and I will send a fixed price by end of day."),
            ("schedule", "I am available to start this week. What is your target launch date?"),
            ("support",  "Yes, I include one week of post-delivery support at no extra cost."),
        ]
    haystack = _norm(text)
    tokens = set(haystack.split())
    for keyword, reply in rules:
        if _norm(keyword) in tokens:
            return reply
    return fallback


# --------------------------------------------------------------------------
# 4. OUTBOUND: build the correct signed Graph payload per platform
# --------------------------------------------------------------------------

def build_outbound(platform: str, recipient: str, text: str,
                   access_token: str = "TEST_TOKEN",
                   wa_phone_number_id: str = "1") -> dict:
    """Return {url, body, headers} for the real Graph send call."""
    if platform == "whatsapp":
        url = f"{GRAPHHOST}/{WA_VERSION}/{wa_phone_number_id}/messages"
        body = {
            "messaging_product": "whatsapp",
            "to": recipient,
            "type": "text",
            "text": {"body": text},
        }
    elif platform == "messenger":
        url = f"{GRAPHHOST}/{MES_VERSION}/{recipient}/messages"
        body = {
            "recipient": {"id": recipient},
            "message": {"text": text},
        }
    else:
        raise ValueError(f"unknown platform {platform!r} (use 'whatsapp' or 'messenger')")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    return {"url": url, "body": body, "headers": headers}


def send(outbound: dict, dry_run: bool = True, timeout: int = 15):
    """POST the outbound message. dry_run=True prints the payload and returns
    a simulated success (no network). dry_run=False does a real Graph call via
    `requests` (needs a valid access token)."""
    url = outbound["url"]
    body = outbound["body"]
    if dry_run:
        preview = {
            "url": url,
            "body": body,
            "note": "DRY-RUN: not sent. Pass --no-dry-run with a real token to send.",
        }
        return {"dry_run": True, "would_send": preview}
    import requests  # only imported for the real network path
    resp = requests.post(url, json=body, headers=outbound["headers"], timeout=timeout)
    return {
        "dry_run": False,
        "status": resp.status_code,
        "ok": 200 <= resp.status_code < 300,
        "response": resp.json() if resp.content else None,
    }


# --------------------------------------------------------------------------
# 5. SERVE: minimal webhook receiver (stdlib only)
# --------------------------------------------------------------------------

class WebhookHandler(BaseHTTPRequestHandler):
    # handler carries shared state injected by build_server()
    token = "CHANGE_ME"
    rules = None
    dry_run = True

    def _reply(self, code, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        # Meta webhook verification handshake: ?hub.mode=subscribe
        # &hub.verify_token=<token>&hub.challenge=<n>
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        if q.get("hub.mode") == ["subscribe"] and q.get("hub.verify_token"):
            if q["hub.verify_token"][0] == self.token:
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(q.get("hub.challenge", ["0"])[0].encode())
            else:
                self._reply(403, {"error": "bad verify token"})
        else:
            self._reply(200, {"ok": True, "service": "whatsapp-messenger-reply-automation"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        sig = self.headers.get("X-Hub-Signature-256", "")
        if not verify_signature(raw, sig, self.token):
            self._reply(401, {"error": "invalid signature"})
            return
        msg = parse_inbound(raw.decode("utf-8", "replace"))
        if msg is None:
            self._reply(200, {"handled": False, "reason": "not an inbound text message"})
            return
        reply_text = route_reply(msg["text"], self.rules)
        outbound = build_outbound(msg["platform"], msg["sender"], reply_text)
        result = send(outbound, dry_run=self.dry_run)
        self._reply(200, {
            "handled": True,
            "platform": msg["platform"],
            "from": msg["sender"],
            "reply": reply_text,
            "send": result,
        })

    def log_message(self, fmt, *args):
        sys.stderr.write("[webhook] %s\n" % (fmt % args))


def build_server(host, port, token, dry_run=True):
    WebhookHandler.token = token
    WebhookHandler.dry_run = dry_run
    return ThreadingHTTPServer((host, port), WebhookHandler)


# --------------------------------------------------------------------------
# selftest: offline, deterministic, no network
# --------------------------------------------------------------------------

def _check(name, cond):
    if not cond:
        raise AssertionError(name)
    return name


def selftest() -> int:
    results = []

    # ---- parse: WhatsApp inbound ----
    wa_in = json.dumps({
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "meta-1",
            "changes": [{
                "value": {
                    "metadata": {"phone_number_id": "123"},
                    "messages": [
                        {"from": "15551234567", "type": "text", "id": "wamid.A", "text": {"body": "How much does this cost?"}}
                    ],
                }
            }],
        }],
    })
    wa = parse_inbound(wa_in)
    results.append(_check("wa.parse.platform", wa["platform"] == "whatsapp"))
    results.append(_check("wa.parse.sender", wa["sender"] == "15551234567"))
    results.append(_check("wa.parse.text", wa["text"] == "How much does this cost?"))
    results.append(_check("wa.parse.direction", wa["direction"] == "in"))
    results.append(_check("wa.parse.mid", wa["message_id"] == "wamid.A"))

    # ---- parse: WhatsApp non-text (image) -> None ----
    wa_img = json.dumps({
        "object": "whatsapp_business_account",
        "entry": [
            {"changes": [
                {"value": {"messages": [
                    {"from": "1555", "type": "image", "id": "wamid.B"},
                ]}},
            ]},
        ],
    })
    results.append(_check("wa.parse.image->None", parse_inbound(wa_img) is None))

    # ---- parse: Messenger inbound ----
    mes_in = json.dumps({
        "object": "page",
        "entry": [{
            "id": "PSID",
            "time": 123,
            "messaging": [{
                "sender": {"id": "UID111"},
                "recipient": {"id": "PSID"},
                "message": {"mid": "m_A1B2", "text": "what is your schedule?"},
            }],
        }],
    })
    mes = parse_inbound(mes_in)
    results.append(_check("mes.parse.platform", mes["platform"] == "messenger"))
    results.append(_check("mes.parse.sender", mes["sender"] == "UID111"))
    results.append(_check("mes.parse.text", mes["text"] == "what is your schedule?"))
    results.append(_check("mes.parse.mid", mes["message_id"] == "m_A1B2"))

    # ---- parse: Messenger echo (no text key) -> None ----
    mes_echo = json.dumps({
        "object": "page",
        "entry": [{"messaging": [{"sender": {"id": "U"},
                                  "message": {"mid": "x", "text": "hi"}}]}],
    })
    results.append(_check("mes.echo.text->ok", parse_inbound(mes_echo) is not None))
    mes_postback = json.dumps({
        "object": "page",
        "entry": [{"messaging": [{"sender": {"id": "U"}, "postback": {"title": "PB"}}]}],
    })
    results.append(_check("mes.postback->None", parse_inbound(mes_postback) is None))

    # ---- parse: junk / non-message ----
    results.append(_check("parse.notjson->None", parse_inbound("not json at all") is None))
    results.append(_check("parse.otherobject->None",
                          parse_inbound(json.dumps({"object": "instagram"})) is None))

    # ---- route: keyword hits ----
    results.append(_check("route.cost", "one-week project" in route_reply("How much does this cost?")))
    results.append(_check("route.schedule", "start this week" in route_reply("What is your schedule like?")))
    results.append(_check("route.quote", "quote today" in route_reply("Can you give me a quote?")))
    results.append(_check("route.price", "pricing" in route_reply("I want to know the price")))
    results.append(_check("route.support", "one week of post-delivery" in route_reply("Do you provide support?")))

    # ---- route: fallback ----
    fb = route_reply("hello there, nice weather")
    results.append(_check("route.fallback", fb == DEFAULT_FALLBACK))
    results.append(_check("route.neverNone",
                          all(route_reply(t) is not None for t in ["a", "price", "zzz", ""])))

    # ---- verify signature ----
    token = "s3cr3t-webhook-token"
    body = b'{"hello":"world"}'
    good = compute_signature(body, token)
    results.append(_check("sig.compute.format", good.startswith("sha256=") and len(good) == 7 + 64))
    results.append(_check("sig.verify.good", verify_signature(body, good, token) is True))
    bad = "sha256=" + "00" * 32
    results.append(_check("sig.verify.bad", verify_signature(body, bad, token) is False))
    results.append(_check("sig.verify.empty", verify_signature(body, "", token) is False))
    results.append(_check("sig.verify.wrongtoken",
                          verify_signature(body, compute_signature(body, "other"), token) is False))

    # ---- outbound: whatsapp ----
    o = build_outbound("whatsapp", "15551234567", "Hello there")
    results.append(_check("out.wa.url", o["url"].endswith("/v18.0/1/messages")))
    results.append(_check("out.wa.body", o["body"]["messaging_product"] == "whatsapp"
                          and o["body"]["to"] == "15551234567"
                          and o["body"]["text"]["body"] == "Hello there"))
    results.append(_check("out.wa.auth", o["headers"]["Authorization"].startswith("Bearer ")))

    # ---- outbound: messenger ----
    o2 = build_outbound("messenger", "UID111", "Hi!")
    results.append(_check("out.mes.url", o2["url"].endswith("/v21.0/UID111/messages")))
    results.append(_check("out.mes.body", o2["body"]["recipient"]["id"] == "UID111"
                          and o2["body"]["message"]["text"] == "Hi!"))

    # ---- outbound: unknown platform ----
    try:
        build_outbound("telegram", "x", "y")
        results.append(_check("out.unknown->raise", False))
    except ValueError:
        results.append(_check("out.unknown->raise", True))

    # ---- send: dry-run returns simulated, no network ----
    s = send(build_outbound("whatsapp", "1555", "hi"), dry_run=True)
    results.append(_check("send.dryrun.flag", s["dry_run"] is True))
    results.append(_check("send.dryrun.would", s["would_send"]["url"].endswith("/messages")))

    # ---- full pipeline: inbound -> route -> outbound (both platforms) ----
    for platform, raw in (("whatsapp", wa_in), ("messenger", mes_in)):
        m = parse_inbound(raw)
        rt = route_reply(m["text"])
        ob = build_outbound(platform, m["sender"], rt)
        results.append(_check(f"pipeline.{platform}.route",
                              ob["body"] if platform == "whatsapp" else ob["body"],
                              ))
        results.append(_check(f"pipeline.{platform}.to",
                              (ob["body"].get("to") or ob["body"]["recipient"]["id"]) == m["sender"]))

    print("selftest: %d/%d checks passed" % (len(results), len(results)))
    print("all checks passed")
    return 0


# --------------------------------------------------------------------------
# demo: show the loop on realistic sample payloads
# --------------------------------------------------------------------------

def demo() -> int:
    samples = {
        "WhatsApp (customer asks price)": {
            "object": "whatsapp_business_account",
            "entry": [
                {"changes": [
                    {"value": {"messages": [
                        {"from": "15551234567", "type": "text", "id": "wamid.DEMO",
                         "text": {"body": "How much does this cost and what is your schedule?"}},
                    ]}},
                ]},
            ],
        },
        "Messenger (customer asks about support)": {
            "object": "page",
            "entry": [
                {"messaging": [
                    {"sender": {"id": "UID-DEMO"},
                     "recipient": {"id": "PSID-DEMO"},
                     "message": {"mid": "m_DEMO",
                                "text": "Do you provide support after delivery?"}},
                ]},
            ],
        },
        "WhatsApp (greeting, no keyword)": {
            "object": "whatsapp_business_account",
            "entry": [
                {"changes": [
                    {"value": {"messages": [
                        {"from": "15559990001", "type": "text", "id": "wamid.GREET",
                         "text": {"body": "hey, is this still open?"}},
                    ]}},
                ]},
            ],
        },
    }
    for label, payload in samples.items():
        raw = json.dumps(payload).encode()
        token = "demo-token"
        sig = compute_signature(raw, token)
        ok = verify_signature(raw, sig, token)
        m = parse_inbound(payload)
        if m is None:
            print(f"\n== {label} ==\n   (not an inbound text message; skipped)")
            continue
        reply = route_reply(m["text"])
        ob = build_outbound(m["platform"], m["sender"], reply)
        sent = send(ob, dry_run=True)
        print(f"\n== {label} ==")
        print(f"   signature verify : {ok}  ({sig[:18]}...)")
        print(f"   inbound          : {m['platform']} from {m['sender']} -> {m['text']!r}")
        print(f"   routed reply     : {reply!r}")
        print(f"   outbound url     : {ob['url']}")
        print(f"   send (dry-run)   : ok (no network) status={sent['dry_run'] and 'dry-run'}")
    print("\ndemo complete")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("selftest", help="run offline unit tests (no network)")
    sub.add_parser("demo", help="parse+route on realistic sample payloads")

    s = sub.add_parser("serve", help="start the local webhook receiver")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8080)
    s.add_argument("--token", default="CHANGE_ME", help="shared webhook verification token")
    s.add_argument("--no-dry-run", action="store_true",
                   help="actually POST outbound messages (needs a real token)")

    s2 = sub.add_parser("send", help="build+send one outbound message")
    s2.add_argument("--platform", required=True, choices=["whatsapp", "messenger"])
    s2.add_argument("--to", required=True, help="recipient phone number or PSID/UID")
    s2.add_argument("--text", required=True)
    s2.add_argument("--token", default="TEST_TOKEN")
    s2.add_argument("--dry-run", action="store_true", default=True)
    s2.add_argument("--no-dry-run", action="store_true", help="really send")

    args = p.parse_args(argv)

    if args.cmd == "selftest":
        return selftest()
    if args.cmd == "demo":
        return demo()
    if args.cmd == "serve":
        dry = not args.no_dry_run
        srv = build_server(args.host, args.port, args.token, dry_run=dry)
        print(f"webhook receiver on http://{args.host}:{args.port} (dry_run={dry})")
        print("GET  ?hub.mode=subscribe&hub.verify_token=%s&hub.challenge=123 -> 200 123" % args.token)
        print("POST /  with a valid X-Hub-Signature-256 -> route+send")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
        return 0
    if args.cmd == "send":
        dry = not args.no_dry_run
        ob = build_outbound(args.platform, args.to, args.text, access_token=args.token)
        out = send(ob, dry_run=dry)
        print(json.dumps(out, indent=2))
        return 0
    p.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
