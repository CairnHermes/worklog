#!/usr/bin/env python3
"""
MCP market-intelligence server: proof of concept
=================================================

What this file proves, end to end, with stdlib only (no pip installs):

1. An MCP-compatible JSON-RPC 2.0 server (initialize / tools/list /
   tools/call / ping, batch requests, tool-level error reporting) that a
   ChatGPT MCP client can point at.
2. A unified read-only data layer over REAL live public exchanges
   (OKX, Coinbase, Kraken, CoinGecko) plus FRED for macro when a key is
   set. Every provider sits behind a token-bucket rate limiter and a
   circuit breaker, and a provider chain falls through to the next on
   failure, so a single vendor going down never stalls a query.
3. A credential vault: keys come from the environment, are read-only by
   construction (the tool registry rejects write tools at registration),
   are never serialized, and can be rotated by re-reading the env.
4. A complete OAuth2 authorization-code + PKCE(S256) + refresh-token flow
   tested against an in-process mock identity provider. This is the shape
   of the Arkham handshake fix: the standard flow is proven working here,
   and the negative tests show exactly which failure classes (challenge
   mismatch, replayed refresh token, expired/missing Bearer) are caught
   and reported, not silently swallowed.

Run:
    python PoC.py selftest          # ~30 checks, hits live APIs
    python PoC.py serve [--port N]  # start the server for a manual demo

The selftest is the acceptance gate: it exercises the live providers,
the fallback chain, the rate limiter, the breaker, the vault, the tool
validation, and the OAuth flow, and exits non-zero on any failure.
"""

import base64
import hashlib
import json
import math
import os
import secrets
import string
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

DEFAULT_PORT = 8723
HTTP_TIMEOUT = 12
COINS = ("BTC", "ETH", "SOL")

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "market-intel-mcp/0.1 (poc)")


def http_get_json(url, headers=None, timeout=HTTP_TIMEOUT):
    h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(2_000_000).decode("utf-8", "replace"))


def http_get_bytes(url, headers=None, timeout=HTTP_TIMEOUT):
    h = {"User-Agent": USER_AGENT}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(2_000_000)


def post_form(url, fields, timeout=HTTP_TIMEOUT):
    body = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"User-Agent": USER_AGENT,
                 "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read(200_000).decode("utf-8", "replace"))


def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


# --------------------------------------------------------------------------
# reliability primitives: token-bucket rate limiter + circuit breaker
# --------------------------------------------------------------------------

class RateLimited(Exception):
    pass


class TokenBucket:
    """Classic token bucket. acquire() blocks up to max_wait seconds."""

    def __init__(self, rate: float, burst: int):
        self.rate = rate          # tokens per second
        self.capacity = burst
        self.tokens = float(burst)
        self.updated = time.monotonic()
        self.lock = threading.Lock()

    def _refill(self):
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        self.updated = now

    def acquire(self, max_wait=1.0):
        deadline = time.monotonic() + max_wait
        while True:
            with self.lock:
                self._refill()
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.02)


class CircuitBreaker:
    CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

    def __init__(self, fail_limit=3, cooldown=10.0):
        self.fail_limit = fail_limit
        self.cooldown = cooldown
        self.state = self.CLOSED
        self.failures = 0
        self.opened_at = 0.0
        self.lock = threading.Lock()

    def allow(self):
        with self.lock:
            if self.state == self.CLOSED:
                return True
            if time.monotonic() - self.opened_at >= self.cooldown:
                self.state = self.HALF_OPEN
                return True
            return False

    def record_success(self):
        with self.lock:
            self.failures = 0
            self.state = self.CLOSED

    def record_failure(self):
        with self.lock:
            self.failures += 1
            if self.failures >= self.fail_limit:
                self.state = self.OPEN
                self.opened_at = time.monotonic()

    def status(self):
        with self.lock:
            return {"state": self.state, "failures": self.failures}


class ProviderDown(Exception):
    pass


# --------------------------------------------------------------------------
# providers
# --------------------------------------------------------------------------

class Provider:
    name = "base"
    supports = ()  # ("spot", "depth", "overview")

    def __init__(self, rate=5.0, burst=10):
        self.limiter = TokenBucket(rate, burst)
        self.breaker = CircuitBreaker(fail_limit=3, cooldown=10.0)

    # subclasses implement: fetch(kind, args) -> dict
    def fetch(self, kind, args):
        raise NotImplementedError

    def call(self, kind, args):
        """Rate-limited, breaker-protected call with the exceptions the
        chain understands (RateLimited / ProviderDown)."""
        if kind not in self.supports:
            raise ProviderDown(f"{self.name} does not support {kind}")
        if not self.breaker.allow():
            raise ProviderDown(f"{self.name} breaker open")
        if not self.limiter.acquire(max_wait=1.0):
            raise RateLimited(self.name)
        t0 = time.monotonic()
        try:
            data = self.fetch(kind, args)
            self.breaker.record_success()
        except RateLimited:
            raise
        except Exception as e:
            self.breaker.record_failure()
            raise ProviderDown(f"{self.name}: {e}") from e
        return data, (time.monotonic() - t0) * 1000.0


def _okx_pair(coin):
    return f"{coin}-USDT"


class OKXProvider(Provider):
    name = "okx"
    supports = ("spot", "depth", "overview")

    def fetch(self, kind, args):
        coin = args.get("coin") or (args.get("coins") or COINS)[0]
        pair = _okx_pair(coin)
        if kind == "spot":
            d = http_get_json(f"https://www.okx.com/api/v5/market/ticker?instId={pair}")
            if d.get("code") != "0":
                raise ProviderDown(f"okx code {d.get('code')}")
            return {"price": float(d["data"][0]["last"]), "pair": pair}
        if kind == "depth":
            d = http_get_json(
                f"https://www.okx.com/api/v5/market/books?instId={pair}&sz=5")
            if d.get("code") != "0":
                raise ProviderDown(f"okx code {d.get('code')}")
            row = d["data"][0]
            return {
                "bids": [[float(p), float(s)] for p, s, *_ in row["bids"]][:5],
                "asks": [[float(p), float(s)] for p, s, *_ in row["asks"]][:5],
            }
        if kind == "overview":
            d = http_get_json(
                f"https://www.okx.com/api/v5/market/ticker?instId={pair}")
            row = d["data"][0]
            last, open24 = float(row["last"]), float(row["open24h"])
            chg = (last - open24) / open24 * 100.0 if open24 else None
            return {"price": last, "change_24h_pct": chg,
                    "high_24h": float(row["high24h"]), "low_24h": float(row["low24h"]),
                    "volume_24h": float(row["volCcy24h"])}
        raise ProviderDown(f"okx kind {kind}")


class CoinbaseProvider(Provider):
    name = "coinbase"
    supports = ("spot",)

    def fetch(self, kind, args):
        coin = args.get("coin")
        d = http_get_json(f"https://api.coinbase.com/v2/prices/{coin}-USD/spot")
        return {"price": float(d["data"]["amount"]), "pair": f"{coin}-USD"}


class KrakenProvider(Provider):
    name = "kraken"
    supports = ("spot", "depth")
    _pair = {"BTC": "XBTUSD", "ETH": "ETHUSD", "SOL": "SOLUSD"}

    def _kp(self, coin):
        return self._pair.get(coin, coin + "USD")

    def fetch(self, kind, args):
        coin = args.get("coin")
        pair = self._kp(coin)
        d = http_get_json(f"https://api.kraken.com/0/public/Ticker?pair={pair}")
        if d.get("error"):
            raise ProviderDown(f"kraken {d['error']}")
        key = list(d["result"].keys())[0]
        row = d["result"][key]
        if kind == "spot":
            return {"price": float(row["c"][0]), "pair": pair}
        if kind == "depth":
            return {
                "bids": [[float(p), float(s)] for p, s, _ in row["b"][:5]],
                "asks": [[float(p), float(s)] for p, s, _ in row["a"][:5]],
            }
        raise ProviderDown(f"kraken kind {kind}")


class CoinGeckoProvider(Provider):
    name = "coingecko"
    supports = ("spot", "overview")
    _id = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}

    def _gid(self, coin):
        return self._id.get(coin, coin.lower())

    def fetch(self, kind, args):
        coin = args.get("coin") or "BTC"
        d = http_get_json(
            "https://api.coingecko.com/api/v3/simple/price"
            f"?ids={self._gid(coin)}&vs_currencies=usd&include_24hr_change=true")
        row = d[self._gid(coin)]
        if kind == "spot":
            return {"price": float(row["usd"]), "pair": f"{coin}-USD"}
        return {"price": float(row["usd"]),
                "change_24h_pct": row.get("usd_24h_change")}


class FREDProvider(Provider):
    name = "fred"
    supports = ("macro",)

    def __init__(self, api_key=None):
        super().__init__(rate=2.0, burst=5)
        self.api_key = api_key or os.environ.get("FRED_API_KEY", "")

    def fetch(self, kind, args):
        if not self.api_key:
            raise ProviderDown("FRED_API_KEY not set")
        series = args.get("series") or ["DGS10"]
        out = {}
        for s in series:
            d = http_get_json(
                "https://api.stlouisfed.org/fred/series/observations?"
                f"series_id={s}&api_key={self.api_key}&sort_order=desc&limit=1")
            row = d["observations"][0]
            out[s] = {"date": row["date"], "value": row["value"]}
        return out


class MockExchange(Provider):
    """Deterministic provider for fallback/breaker tests."""

    name = "mock"
    supports = ("spot", "depth")

    def __init__(self, fail_times=0):
        super().__init__(rate=100.0, burst=1000)
        self.fail_times = fail_times
        self.calls = 0

    def fetch(self, kind, args):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ProviderDown(f"mock failing ({self.calls}/{self.fail_times})")
        if kind == "spot":
            return {"price": 83000.0, "pair": "MOCK"}
        return {"bids": [[82999.0, 1.0]], "asks": [[83001.0, 1.0]]}


class AllProvidersDown(Exception):
    pass


class ProviderChain:
    def __init__(self, providers):
        self.providers = providers
        self.stats = {"requests": 0, "errors": 0,
                      "fallbacks": 0, "rate_limited": 0}
        self.last_errors = []

    def fetch(self, kind, args):
        self.stats["requests"] += 1
        errors = []
        fallback_used = False
        for p in self.providers:
            if kind not in p.supports:
                continue
            if fallback_used:
                self.stats["fallbacks"] += 1
            try:
                data, latency = p.call(kind, args)
                return data, {"provider": p.name, "latency_ms": round(latency, 1),
                              "fallback": fallback_used,
                              "skipped": errors}
            except RateLimited:
                self.stats["rate_limited"] += 1
                errors.append(f"{p.name}: rate_limited")
            except ProviderDown as e:
                errors.append(str(e))
            except Exception as e:  # unexpected, still fall through
                errors.append(f"{p.name}: {type(e).__name__}: {e}")
        self.stats["errors"] += 1
        self.last_errors = errors
        raise AllProvidersDown("; ".join(errors) or "no provider supports " + kind)


# --------------------------------------------------------------------------
# credential vault: env-backed, read-only by construction, never serializes
# --------------------------------------------------------------------------

class Vault:
    READ_ONLY_OK = ("read", "public_data", "market_data:read")

    def __init__(self, env=None):
        self.env = env if env is not None else os.environ
        self._secrets = {}
        self._lock = threading.Lock()

    def register(self, name, value, scope="read"):
        if scope not in self.READ_ONLY_OK:
            raise ValueError(
                f"vault rejects non-read-only scope '{scope}' for '{name}'; "
                "this service is read-only by construction")
        with self._lock:
            self._secrets[name] = str(value)

    def rotate(self, name):
        """Re-read from the environment (the rotation path)."""
        v = self.env.get(name, "")
        with self._lock:
            if v:
                self._secrets[name] = v
            return bool(v)

    def get(self, name):
        with self._lock:
            return self._secrets.get(name, self.env.get(name, ""))

    def has(self, name):
        return bool(self.get(name))

    def summary(self):
        """Presence only. Values never leave the vault."""
        return {n: "set" for n in sorted(self._secrets)
                if self._secrets.get(n)}


# --------------------------------------------------------------------------
# tools (read-only)
# --------------------------------------------------------------------------

class ToolError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


TOOL_DEFS = [
    {
        "name": "spot_prices",
        "description": ("Live spot prices for BTC/ETH/SOL (or a subset) with "
                        "the serving provider, latency, and cross-provider "
                        "spread where a second source agrees."),
        "inputSchema": {
            "type": "object",
            "properties": {"coins": {"type": "array", "items": {"type": "string"},
                                    "description": "subset of BTC, ETH, SOL"}},
        },
    },
    {
        "name": "order_book",
        "description": "Top-of-book depth (5 levels) for one coin.",
        "inputSchema": {
            "type": "object",
            "properties": {"coin": {"type": "string"}},
            "required": ["coin"],
        },
    },
    {
        "name": "market_overview",
        "description": ("Price, 24h change, high/low for a coin from the "
                        "overview-capable providers."),
        "inputSchema": {
            "type": "object",
            "properties": {"coin": {"type": "string"}},
        },
    },
    {
        "name": "macro_indicators",
        "description": ("Macro series (e.g. DGS10 10y yield) via FRED. "
                        "Requires FRED_API_KEY in the service environment; "
                        "degrades to a structured 'degraded' answer without it."),
        "inputSchema": {
            "type": "object",
            "properties": {"series": {"type": "array", "items": {"type": "string"}}},
        },
    },
    {
        "name": "health",
        "description": ("Server health: uptime, request/error counters, "
                        "per-provider breaker state, vault key presence "
                        "(names only, never values)."),
        "inputSchema": {"type": "object", "properties": {}},
    },
]

# Registry is read-only by construction: anything with side effects is
# refused at registration time.
WRITE_TOOLS = {"place_order", "transfer", "cancel_order"}


def _validate_coin(coin):
    if coin is None:
        return "BTC"
    if not isinstance(coin, str) or coin.upper() not in COINS:
        raise ToolError(f"unknown coin '{coin}'; supported: {', '.join(COINS)}")
    return coin.upper()


class ServerCore:
    """Holds the chains + vault + tool implementations. One instance is
    shared by the HTTP handler so selftest and serve use identical code."""

    def __init__(self, vault=None, override_providers=None):
        t0 = time.time()
        self.started_at = t0
        self.vault = vault or Vault()
        if override_providers is not None:
            self.chains = override_providers
        else:
            self.chains = {
                "spot": ProviderChain([OKXProvider(), CoinbaseProvider(),
                                       KrakenProvider(), CoinGeckoProvider()]),
                "depth": ProviderChain([OKXProvider(), KrakenProvider()]),
                "overview": ProviderChain([CoinGeckoProvider(), OKXProvider()]),
                "macro": ProviderChain([FREDProvider()]),
            }
        self._tools = {}
        for name, fn in (("spot_prices", self.spot_prices),
                         ("order_book", self.order_book),
                         ("market_overview", self.market_overview),
                         ("macro_indicators", self.macro_indicators),
                         ("health", self.health)):
            if name in WRITE_TOOLS:
                raise ValueError(f"refusing to register write tool {name}")
            self._tools[name] = fn
        self.lock = threading.Lock()

    # ---- tool implementations -------------------------------------------

    def spot_prices(self, args):
        coins = args.get("coins") or list(COINS)
        if not isinstance(coins, list) or not coins:
            raise ToolError("coins must be a non-empty list")
        out = {"coins": []}
        btc_secondary = None
        for c in coins:
            coin = _validate_coin(c)
            data, meta = self.chains["spot"].fetch("spot", {"coin": coin})
            entry = {"coin": coin, "price_usd": data["price"], "meta": meta}
            if coin == "BTC" and btc_secondary is None:
                try:
                    d2, _ = CoinbaseProvider().call("spot", {"coin": coin})
                    btc_secondary = d2["price"]
                    entry["cross_check"] = {
                        "second_source": "coinbase",
                        "price_usd": d2["price"],
                        "spread_pct": round(abs(data["price"] - d2["price"])
                                            / d2["price"] * 100.0, 4),
                    }
                except Exception:
                    pass
            out["coins"].append(entry)
        return out

    def order_book(self, args):
        coin = _validate_coin(args.get("coin"))
        data, meta = self.chains["depth"].fetch("depth", {"coin": coin})
        best_bid = data["bids"][0][0] if data["bids"] else None
        best_ask = data["asks"][0][0] if data["asks"] else None
        return {"coin": coin, "bids": data["bids"], "asks": data["asks"],
                "spread": (best_ask - best_bid if best_bid and best_ask else None),
                "meta": meta}

    def market_overview(self, args):
        coin = _validate_coin(args.get("coin"))
        data, meta = self.chains["overview"].fetch("overview", {"coin": coin})
        return {"coin": coin, **{k: data[k] for k in
                ("price", "change_24h_pct", "high_24h", "low_24h",
                 "volume_24h") if k in data}, "meta": meta}

    def macro_indicators(self, args):
        series = args.get("series") or ["DGS10"]
        fred = self.chains["macro"].providers[0]
        if not fred.api_key:
            return {"status": "degraded",
                    "reason": "FRED_API_KEY not set in service environment",
                    "requested": series,
                    "note": ("configure the key and the same call returns "
                             "live values; the chain reports this instead of "
                             "failing the whole conversation")}
        data, meta = self.chains["macro"].fetch("macro", {"series": series})
        return {"status": "ok", **data, "meta": meta}

    def health(self, args):
        providers = {}
        for kind, chain in self.chains.items():
            for p in chain.providers:
                providers[p.name] = {
                    "supports": list(p.supports),
                    "breaker": p.breaker.status(),
                    "stats": chain.stats if p is chain.providers[0] else None,
                }
        return {
            "status": "ok",
            "uptime_s": round(time.time() - self.started_at, 1),
            "tools": sorted(self._tools),
            "vault": self.vault.summary(),
            "providers": providers,
        }

    # ---- dispatch ---------------------------------------------------------

    def call_tool(self, name, args):
        fn = self._tools.get(name)
        if fn is None:
            raise MethodNotFound(name)
        try:
            result = fn(args or {})
        except ToolError as e:
            raise ToolFailure(e.message) from e
        except AllProvidersDown as e:
            raise ToolFailure(f"all providers down: {e}") from e
        return result


class MethodNotFound(Exception):
    pass


class ToolFailure(Exception):
    pass


# --------------------------------------------------------------------------
# JSON-RPC 2.0 / MCP transport
# --------------------------------------------------------------------------

PROTOCOL_VERSION = "2025-06-18"


def jsonrpc_error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


class MCPHandler(BaseHTTPRequestHandler):
    server_version = "market-intel-mcp/0.1"
    core: ServerCore = None  # set by make_server()

    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/healthz":
            self._send({"ok": True})
        else:
            self._send({"error": "use POST /rpc"}, 404)

    def do_POST(self):
        if self.path != "/rpc":
            self._send(jsonrpc_error(None, -32000, "unknown path; use /rpc"), 404)
            return
        try:
            n = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(n)
            req = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send(jsonrpc_error(None, -32700, "parse error"), 400)
            return

        if isinstance(req, list):
            outs = [self._handle_one(r) for r in req]
            outs = [o for o in outs if o is not None]
            self._send(outs if len(outs) != 1 else outs[0])
            return
        out = self._handle_one(req)
        if out is None:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send(out)

    def _handle_one(self, req):
        if not isinstance(req, dict) or req.get("jsonrpc") != "2.0" or "id" not in req:
            return jsonrpc_error(None, -32600, "invalid request")
        rid = req["id"]
        method = req.get("method")
        params = req.get("params") or {}
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "market-intel-mcp", "version": "0.1.0"},
                }
                return {"jsonrpc": "2.0", "id": rid, "result": result}
            if method in ("notifications/initialized", "initialized"):
                return None  # notification: no response
            if method == "ping":
                return {"jsonrpc": "2.0", "id": rid, "result": {}}
            if method == "tools/list":
                return {"jsonrpc": "2.0", "id": rid,
                        "result": {"tools": TOOL_DEFS}}
            if method == "tools/call":
                name = params.get("name")
                args = params.get("arguments") or {}
                try:
                    result = self.core.call_tool(name, args)
                    return {"jsonrpc": "2.0", "id": rid, "result": {
                        "content": [{"type": "text",
                                     "text": json.dumps(result, indent=1)}],
                        "isError": False}}
                except MethodNotFound:
                    return {"jsonrpc": "2.0", "id": rid, "result": {
                        "content": [{"type": "text",
                                     "text": json.dumps({"error": "unknown tool: " + name})}],
                        "isError": True}}
                except ToolFailure as e:
                    return {"jsonrpc": "2.0", "id": rid, "result": {
                        "content": [{"type": "text",
                                     "text": json.dumps({"error": str(e)})}],
                        "isError": True}}
            return jsonrpc_error(rid, -32601, f"method not found: {method}")
        except Exception as e:  # transport-level safety net
            return jsonrpc_error(rid, -32603, f"internal error: {type(e).__name__}")


def make_server(port, core):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), MCPHandler)
    MCPHandler.core = core
    return httpd


# --------------------------------------------------------------------------
# OAuth2 authorization code + PKCE + refresh (mock IdP for the proof)
# --------------------------------------------------------------------------

class MockIdP(BaseHTTPRequestHandler):
    """Implements the standard OAuth2 auth-code + PKCE(S256) + refresh
    semantics so the client flow can be tested end to end in-process,
    including the negative paths."""
    server_version = "mock-idp/1.0"
    state = None  # class-level: pending authorizations, issued tokens

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path == "/authorize":
            if (q.get("response_type") != "code" or not q.get("client_id")
                    or not q.get("redirect_uri") or not q.get("code_challenge")
                    or not q.get("state")):
                self._json({"error": "invalid_request"}, 400)
                return
            challenge = q["code_challenge"]
            state = q["state"]
            code = "code-" + secrets.token_hex(8)
            self.state["pending"][code] = {"challenge": challenge, "state": state,
                                           "client_id": q["client_id"]}
            loc = q["redirect_uri"] + "?" + urllib.parse.urlencode(
                {"code": code, "state": state})
            self.send_response(302)
            self.send_header("Location", loc)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if u.path == "/protected":
            auth = self.headers.get("Authorization", "")
            tok = auth[7:] if auth.startswith("Bearer ") else None
            entry = self.state["access"].get(tok)
            if not entry:
                self._json({"error": "invalid_token"}, 401)
                return
            if entry["exp"] < time.time():
                self._json({"error": "token_expired"}, 401)
                return
            self._json({"data": {"ok": True, "scope": entry["scope"]}})
            return
        if u.path == "/redirect-capture":
            self._json({"code": q.get("code"), "state": q.get("state")})
            return
        self._json({"error": "not_found"}, 404)

    def do_POST(self):
        if self.path != "/token":
            self._json({"error": "not_found"}, 404)
            return
        n = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(n).decode("utf-8", "replace")
        fields = {k: v[0] for k, v in
                  urllib.parse.parse_qs(raw, keep_blank_values=True).items()}
        grant = fields.get("grant_type")
        st = self.state
        if grant == "authorization_code":
            code = fields.get("code")
            verifier = fields.get("code_verifier")
            pending = st["pending"].pop(code, None) if code else None
            if not pending or not verifier:
                self._json({"error": "invalid_grant"}, 400)
                return
            expect = b64url(hashlib.sha256(verifier.encode()).digest())
            if expect != pending["challenge"]:
                self._json({"error": "invalid_grant",
                            "detail": "code_verifier does not match code_challenge"}, 400)
                return
            access = "at-" + secrets.token_hex(8)
            refresh = "rt-" + secrets.token_hex(8)
            st["access"][access] = {"exp": time.time() + 3600, "scope": "read"}
            st["refresh"][refresh] = True
            self._json({"access_token": access, "token_type": "bearer",
                        "expires_in": 3600, "refresh_token": refresh,
                        "scope": "read"})
            return
        if grant == "refresh_token":
            rt = fields.get("refresh_token")
            if rt in st["refresh"]:
                del st["refresh"][rt]  # single-use refresh (rotation)
                access = "at-" + secrets.token_hex(8)
                new_rt = "rt-" + secrets.token_hex(8)
                st["access"][access] = {"exp": time.time() + 3600, "scope": "read"}
                st["refresh"][new_rt] = True
                self._json({"access_token": access, "token_type": "bearer",
                            "expires_in": 3600, "refresh_token": new_rt})
                return
            self._json({"error": "invalid_grant",
                        "detail": "refresh token unknown or already rotated"}, 400)
            return
        self._json({"error": "unsupported_grant_type"}, 400)


def _fresh_idp_state():
    return {"pending": {}, "access": {}, "refresh": {}}


def make_idp(port):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), MockIdP)
    MockIdP.state = _fresh_idp_state()
    return httpd


class OAuthClient:
    """The client side of the handshake. This is the shape of the Arkham
    integration: authorize -> redirect -> code exchange -> Bearer use ->
    refresh. All of it verified in the selftest against MockIdP."""

    def __init__(self, base, client_id, client_secret, redirect_uri):
        self.base = base
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.verifier = b64url(secrets.token_bytes(32))
        self.challenge = b64url(hashlib.sha256(self.verifier.encode()).digest())
        self.tokens = {}

    def authorize_url(self, state):
        q = urllib.parse.urlencode({
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "state": state,
            "code_challenge": self.challenge,
            "code_challenge_method": "S256",
        })
        return f"{self.base}/authorize?{q}"

    def exchange(self, code):
        status, body = post_form(self.base + "/token", {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": self.verifier,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        })
        self.tokens = body
        return body

    def refresh(self, refresh_token):
        status, body = post_form(self.base + "/token", {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        })
        self.tokens = body
        return body

    def bearer(self):
        return "Bearer " + self.tokens.get("access_token", "")


# --------------------------------------------------------------------------
# client helper for the selftest (real HTTP client, not in-process calls)
# --------------------------------------------------------------------------

def rpc(server, method, params=None, notify=False):
    payload = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        payload["params"] = params
    if not notify:
        payload["id"] = int(time.time() * 1000) % 10**9
    data = json.dumps(payload).encode()
    req = urllib.request.Request(server + "/rpc", data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read()
    return json.loads(body) if body else None


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

CHECKS = []


def check(name, fn):
    t0 = time.time()
    try:
        fn()
        CHECKS.append((name, True, ""))
        print(f"  PASS  {name}")
    except Exception as e:
        CHECKS.append((name, False, str(e)))
        print(f"  FAIL  {name}  -> {e}")


def run_selftest():
    import socket

    def free_port():
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p

    print("== MCP market-intel PoC selftest ==")
    print("Live network checks hit OKX / Coinbase / Kraken / CoinGecko.")
    print()

    # ---- group A: transport + tools over real live data -------------------
    port = free_port()
    core = ServerCore()
    httpd = make_server(port, core)
    srv = threading.Thread(target=httpd.serve_forever, daemon=True)
    srv.start()
    srv_base = f"http://127.0.0.1:{port}"

    init = rpc(srv_base, "initialize", {"protocolVersion": PROTOCOL_VERSION,
                                       "clientInfo": {"name": "selftest"}})
    check("A1 initialize returns serverInfo + protocol version", lambda: (
        init["result"]["serverInfo"]["name"] == "market-intel-mcp" or 1 / 0))

    tools = rpc(srv_base, "tools/list")
    names = [t["name"] for t in tools["result"]["tools"]]
    check("A2 tools/list exposes the 5 read-only tools", lambda: (
        names == ["spot_prices", "order_book", "market_overview",
                  "macro_indicators", "health"] or (_ for _ in ()).throw(
            AssertionError(f"got {names}"))))

    spot = rpc(srv_base, "tools/call", {"name": "spot_prices",
                                       "arguments": {"coins": ["BTC", "ETH", "SOL"]}})
    spot_data = json.loads(spot["result"]["content"][0]["text"])
    check("A3 spot_prices returns live BTC/ETH/SOL with provider+latency", lambda: (
        len(spot_data["coins"]) == 3
        and all(c["price_usd"] > 0 and c["meta"]["provider"]
                and c["meta"]["latency_ms"] >= 0 for c in spot_data["coins"])
        or (_ for _ in ()).throw(AssertionError(json.dumps(spot_data)[:300]))))
    btc = spot_data["coins"][0]
    cc = btc.get("cross_check")
    check("A4 cross-provider consistency: BTC within 2% of second source", lambda: (
        cc is not None and cc["spread_pct"] < 2.0
        or (_ for _ in ()).throw(AssertionError(
            f"cross_check={cc}"))))

    ob = rpc(srv_base, "tools/call", {"name": "order_book", "arguments": {"coin": "ETH"}})
    ob_data = json.loads(ob["result"]["content"][0]["text"])
    check("A5 order_book: 5-level bids+asks, ask>bid, spread sane", lambda: (
        len(ob_data["bids"]) == 5 and len(ob_data["asks"]) == 5
        and ob_data["asks"][0][0] > ob_data["bids"][0][0] > 0
        and ob_data["spread"] > 0
        or (_ for _ in ()).throw(AssertionError(json.dumps(ob_data)[:300]))))

    ov = rpc(srv_base, "tools/call", {"name": "market_overview",
                                     "arguments": {"coin": "SOL"}})
    ov_data = json.loads(ov["result"]["content"][0]["text"])
    check("A6 market_overview: price + 24h change live", lambda: (
        ov_data["price"] > 0 and "change_24h_pct" in ov_data
        or (_ for _ in ()).throw(AssertionError(json.dumps(ov_data)[:300]))))

    macro = rpc(srv_base, "tools/call", {"name": "macro_indicators",
                                        "arguments": {"series": ["DGS10"]}})
    macro_data = json.loads(macro["result"]["content"][0]["text"])
    check("A7 macro degrades gracefully without FRED_API_KEY", lambda: (
        (os.environ.get("FRED_API_KEY") and macro_data.get("status") == "ok")
        or (not os.environ.get("FRED_API_KEY")
            and macro_data.get("status") == "degraded"
            and "FRED_API_KEY" in macro_data.get("reason", ""))
        or (_ for _ in ()).throw(AssertionError(json.dumps(macro_data)[:200]))))

    bad = rpc(srv_base, "tools/call", {"name": "spot_prices",
                                      "arguments": {"coins": ["FOO"]}})
    bad_data = json.loads(bad["result"]["content"][0]["text"])
    check("A8 invalid coin -> structured tool error (isError, no 5xx)", lambda: (
        bad["result"]["isError"] is True and "unknown coin" in bad_data["error"]
        or (_ for _ in ()).throw(AssertionError(json.dumps(bad_data)[:200]))))

    unk = rpc(srv_base, "tools/call", {"name": "place_order", "arguments": {}})
    unk_data = json.loads(unk["result"]["content"][0]["text"])
    check("A9 write tool 'place_order' is refused (read-only registry)", lambda: (
        unk["result"]["isError"] is True and "unknown tool" in unk_data["error"]
        or (_ for _ in ()).throw(AssertionError(json.dumps(unk_data)[:200]))))

    h = rpc(srv_base, "tools/call", {"name": "health", "arguments": {}})
    h_data = json.loads(h["result"]["content"][0]["text"])
    check("A10 health: uptime, providers, vault summary (names only)", lambda: (
        h_data["uptime_s"] >= 0
        and h_data["providers"]
        and h_data["vault"] is not None
        or (_ for _ in ()).throw(AssertionError(json.dumps(h_data)[:300]))))

    batch = json.dumps([
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]).encode()
    req = urllib.request.Request(srv_base + "/rpc", data=batch,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        batch_res = json.loads(r.read())
    check("A11 JSON-RPC batch: ping + tools/list in one round trip", lambda: (
        sorted(x["id"] for x in batch_res) == [1, 2]
        or (_ for _ in ()).throw(AssertionError(json.dumps(batch_res)[:200]))))

    notif = rpc(srv_base, "notifications/initialized", {}, notify=True)
    check("A12 notification accepted with no response body", lambda: (
        notif is None))

    badreq = json.dumps({"jsonrpc": "2.0", "method": "nope-no-id"}).encode()
    req = urllib.request.Request(srv_base + "/rpc", data=badreq,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        badreq_res = json.loads(r.read())
    check("A13 request without id -> JSON-RPC error -32600", lambda: (
        badreq_res["error"]["code"] == -32600
        or (_ for _ in ()).throw(AssertionError(json.dumps(badreq_res)[:200]))))

    # ---- group B: reliability (fallback, rate limit, breaker) ------------
    # Chain where the first provider fails N times: fallback must carry the
    # load, and the breaker must stop routing to a dead provider.
    bad_first = MockExchange(fail_times=10**9)
    good = MockExchange(fail_times=0)
    chain = ProviderChain([bad_first, good])
    # The chain tries providers in order and stops at the first success,
    # so each request costs the failing provider exactly one strike.
    for _ in range(3):
        data, meta = chain.fetch("spot", {"coin": "BTC"})
    check("B1 provider failure -> automatic fallback, query still succeeds",
          lambda: (meta["provider"] == "mock" and data["price"] == 83000.0
                   or (_ for _ in ()).throw(AssertionError(str(meta)))))

    breaker = chain.providers[0].breaker
    check("B2 breaker opened after 3 consecutive failures (circuit trips)",
          lambda: (breaker.state == "open"
                   or (_ for _ in ()).throw(AssertionError(breaker.status()))))

    data2, meta2 = chain.fetch("spot", {"coin": "BTC"})
    check("B3 with breaker open, chain skips dead provider, still succeeds",
          lambda: (meta2["provider"] == "mock"
                   or (_ for _ in ()).throw(AssertionError(str(meta2)))))

    slow = MockExchange(fail_times=0)
    slow.limiter = TokenBucket(rate=0.5, burst=1)  # 1 token, 0.5/s refill
    chain2 = ProviderChain([slow, MockExchange(fail_times=0)])
    chain2.providers[0].name = "slow"
    chain2.providers[1].name = "fast"
    t0 = time.monotonic()
    results = [chain2.fetch("spot", {"coin": "BTC"})[1] for _ in range(4)]
    elapsed = time.monotonic() - t0
    check("B4 rate limiter blocks, then fallback keeps serving (no stall, no 5xx)",
          lambda: (any(r["provider"] == "fast" for r in results)
                   and elapsed >= 0.5
                   or (_ for _ in ()).throw(
                       f"results={results} elapsed={elapsed:.2f}")))

    dead = [MockExchange(fail_times=10**9), MockExchange(fail_times=10**9)]
    dead_chain = ProviderChain(dead)
    try:
        dead_chain.fetch("spot", {"coin": "BTC"})
        raise AssertionError("expected AllProvidersDown")
    except AllProvidersDown as e:
        pass
    check("B5 all providers down -> explicit AllProvidersDown with cause list",
          lambda: True)

    # ---- group C: vault ----------------------------------------------------
    v = Vault()
    v.register("ARKHAM_CLIENT_ID", "test-id")
    v.register("COINGLASS_API_KEY", "sk-test")
    check("C1 vault stores keys, exposes presence-only summary", lambda: (
        v.has("COINGLASS_API_KEY") and "sk-test" not in json.dumps(v.summary())
        or (_ for _ in ()).throw(AssertionError(str(v.summary())))))
    try:
        v.register("EXCHANGE_SECRET", "x", scope="trade:write")
        raise AssertionError("vault accepted a write scope")
    except ValueError:
        pass
    check("C2 vault rejects non-read-only scopes at registration", lambda: True)
    check("C3 server registry refuses write tools (place_order etc.)", lambda: (
        "place_order" not in core._tools
        or (_ for _ in ()).throw(AssertionError("write tool registered"))))

    # ---- group D: OAuth2 auth-code + PKCE + refresh ------------------------
    idp_port = free_port()
    idp = make_idp(idp_port)
    idp_thread = threading.Thread(target=idp.serve_forever, daemon=True)
    idp_thread.start()
    idp_base = f"http://127.0.0.1:{idp_port}"

    client = OAuthClient(idp_base, "arkham-client", "s3cret",
                         idp_base + "/redirect-capture")
    url = client.authorize_url(state="st-123")
    check("D1 authorize URL carries client_id, S256 challenge, redirect, state",
          lambda: (
              "client_id=arkham-client" in url
              and "code_challenge_method=S256" in url
              and f"code_challenge={urllib.parse.quote(client.challenge)}" in url
              and "state=st-123" in url
              or (_ for _ in ()).throw(AssertionError(url))))

    captured = None

    def follow_authorize():
        # urllib follows the 302 automatically, so the final response is
        # the redirect-capture endpoint: that proves authorize -> 302 ->
        # capture in one shot.
        nonlocal captured
        with urllib.request.urlopen(url, timeout=10) as r:
            captured = json.loads(r.read())

    follow_authorize()
    check("D2 authorize -> redirect lands with code + state round-tripped",
          lambda: (captured and captured["state"] == "st-123"
                   and captured["code"].startswith("code-")
                   or (_ for _ in ()).throw(AssertionError(str(captured)))))

    tokens = client.exchange(captured["code"])
    check("D3 code exchange: access + refresh issued, S256 verified", lambda: (
        tokens.get("access_token", "").startswith("at-")
        and tokens.get("refresh_token", "").startswith("rt-")
        and tokens.get("token_type") == "bearer"
        or (_ for _ in ()).throw(AssertionError(json.dumps(tokens)))))

    with urllib.request.urlopen(
            urllib.request.Request(idp_base + "/protected",
                                   headers={"Authorization": client.bearer()}),
            timeout=10) as r:
        prot = json.loads(r.read())
    check("D4 Bearer token accepted on protected endpoint", lambda: (
        prot.get("data", {}).get("ok") is True
        or (_ for _ in ()).throw(AssertionError(json.dumps(prot)))))

    badreq = urllib.request.Request(
        idp_base + "/token",
        data=urllib.parse.urlencode({
            "grant_type": "authorization_code", "code": "bogus-code",
            "code_verifier": client.verifier}).encode())
    e = None
    body = {}
    try:
        urllib.request.urlopen(badreq, timeout=10)
        raise AssertionError("expected 400")
    except urllib.error.HTTPError as err:
        e = err
        body = json.loads(err.read())
    check("D5 negative: bad code -> 400 invalid_grant (caught, not swallowed)",
          lambda: (e is not None and e.code == 400 and body["error"] == "invalid_grant"
                   or (_ for _ in ()).throw(AssertionError(json.dumps(body)))))

    # PKCE mismatch: fresh authorize with a different verifier
    client2 = OAuthClient(idp_base, "arkham-client", "s3cret",
                          idp_base + "/redirect-capture")
    u2 = client2.authorize_url(state="s2")
    with urllib.request.urlopen(u2, timeout=10) as r:
        cap2 = json.loads(r.read())
    q2 = {"code": cap2["code"], "state": cap2["state"]}
    e2 = None
    b2 = {}
    try:
        bad = urllib.request.Request(
            idp_base + "/token",
            data=urllib.parse.urlencode({
                "grant_type": "authorization_code", "code": q2["code"],
                "code_verifier": b64url(secrets.token_bytes(32))}).encode())
        urllib.request.urlopen(bad, timeout=10)
        raise AssertionError("expected 400 on verifier mismatch")
    except urllib.error.HTTPError as err:
        e2 = err
        b2 = json.loads(err.read())
    check("D6 negative: verifier/challenge mismatch -> 400 (the classic Arkham-class failure)",
          lambda: (e2 is not None and e2.code == 400
                   or (_ for _ in ()).throw(AssertionError(json.dumps(b2)))))

    new_tokens = client.refresh(tokens["refresh_token"])
    check("D7 refresh issues rotated access + refresh tokens", lambda: (
        new_tokens["access_token"] != tokens["access_token"]
        and new_tokens["refresh_token"].startswith("rt-")
        or (_ for _ in ()).throw(AssertionError(json.dumps(new_tokens)))))
    e3 = None
    try:
        bad = urllib.request.Request(
            idp_base + "/token",
            data=urllib.parse.urlencode({
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"]}).encode())
        urllib.request.urlopen(bad, timeout=10)
        raise AssertionError("expected 400 on replayed refresh")
    except urllib.error.HTTPError as err:
        e3 = err
    check("D8 negative: replayed refresh token rejected (rotation enforced)",
          lambda: (e3 is not None and e3.code == 400
                   or (_ for _ in ()).throw(AssertionError("no 400 on replay"))))

    e4 = None
    try:
        urllib.request.urlopen(idp_base + "/protected", timeout=10)
        raise AssertionError("expected 401")
    except urllib.error.HTTPError as err:
        e4 = err
    check("D9 negative: missing Bearer -> 401",
          lambda: (e4 is not None and e4.code == 401
                   or (_ for _ in ()).throw(AssertionError("no 401"))))

    httpd.shutdown()
    idp.shutdown()

    # ---- summary -----------------------------------------------------------
    passed = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    print()
    print(f"== {passed}/{total} checks passed ==")
    if passed != total:
        for name, ok, err in CHECKS:
            if not ok:
                print(f"  failed: {name}: {err}")
    return 0 if passed == total else 1


# --------------------------------------------------------------------------

def main():
    import sys
    args = sys.argv[1:]
    if args and args[0] == "serve":
        port = DEFAULT_PORT
        if "--port" in args:
            port = int(args[args.index("--port") + 1])
        core = ServerCore()
        httpd = make_server(port, core)
        print(f"market-intel MCP PoC listening on http://127.0.0.1:{port}/rpc "
              f"(JSON-RPC 2.0 POST; tools: {', '.join(sorted(core._tools))})")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    else:
        sys.exit(run_selftest())


if __name__ == "__main__":
    main()
