#!/usr/bin/env python3
"""
Windows API Phone Scraper (PoC)
-------------------------------
A small, self-contained HTTP service that takes a LIST of business-directory
URLs (fed in via the API) and returns the public phone number(s) each site
publishes, in JSON. One call in, one answer out -- no browser, no build step,
runs on Windows out of the box with stock Python.

Endpoints
---------
  GET  /health                          liveness probe
  GET  /help                            usage / examples (HTML)
  POST /api/phones                      body: {"urls": ["https://..."], ...}
                                         -> {"results": [{url, phones:[...], found, error}, ...]}
  GET  /api/phones?url=https://...      single-URL convenience (query param)

Options (POST JSON, all optional):
  max_pages   int     max pages to walk per URL (default 6)
  delay       float   polite delay between requests, seconds (default 1.5)
  limit       str     comma list of path substrings to restrict to
  timeout     int     per-request timeout seconds (default 25)

Design (deliberately scoped, no over-promising):
- Extraction is format-agnostic: it reads tel: links, JSON-LD `telephone` /
  contactPoint, and visible text with one robust detector.
- Numbers are kept EXACTLY as the site writes them (the client asked for the
  number "as it appears online"); dedup is on the digit-only form so
  "(413) 555-0123" and "413-555-0123" collapse to one row but the original
  spelling is preserved.
- Polite by default: rotating real browser headers, a delay + jitter, and a
  page cap, so a batch of directories does not hammer an origin and trip blocks.
- Honest gap: numbers rendered purely by client-side JavaScript are only
  captured if the page ships them (tel: links, JSON-LD, embedded text). Most
  business/directory sites do ship them server-side. If a directory is a heavy
  SPA, say so and I'll offer the Playwright variant.

Usage
-----
  python PoC.py --port 8399            # start the API
  python PoC.py --selftest             # prove the detector against fixtures
  python PoC.py --demo                 # start the API and hit a real directory once

Runbook:
  1) python PoC.py --port 8399
  2) curl -s -X POST http://localhost:8399/api/phones -H "content-type: application/json" \
        -d "{\"urls\":[\"https://www.example.com/contact\"]}"
"""

import argparse
import html as _html
import json
import random
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36 Edg/123.0",
]

# --- Phone detection -------------------------------------------------------
PHONE_RE = re.compile(
    r"""
    (?<![\dA-Za-z])
    (
        (?:\+\d{1,3}[\s.\-]?)?
        (?:\(? \d{2,4}\)?[\s.\-]?)?
        \d{2,4}
        (?:[\s.\-]\d{2,4}){1,4}
    )
    (?![\dA-Za-z])
    """,
    re.VERBOSE,
)

def canonical(sig: str) -> str:
    return re.sub(r"[^0-9]", "", sig)

def looks_like_phone(sig: str) -> bool:
    core = canonical(sig)
    if len(core) < 7 or len(core) > 15:
        return False
    if re.fullmatch(r"20\d{2}", core) or re.fullmatch(r"20\d{4}", core):
        return False
    if re.fullmatch(r"19\d{2}", core):
        return False
    if re.fullmatch(r"\d{4}[-.\s]\d{2}[-.\s]\d{2}", sig.strip()):
        return False
    if len(set(core)) == 1:
        return False
    return True

def extract_from_html(page: str):
    found = []
    for m in re.finditer(r'href\s*=\s*["\']tel:([^"\']+)["\']', page):
        found.append(_html.unescape(m.group(1).strip()))
    for block in re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page, re.S):
        try:
            data = json.loads(_html.unescape(block))
        except Exception:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for key in ("telephone", "phone", "phoneNumber"):
                    if isinstance(node.get(key), str):
                        found.append(node[key].strip())
                for v in node.values():
                    if isinstance(v, (dict, list)):
                        stack.append(v)
            elif isinstance(node, list):
                stack.extend(node)
    text = re.sub(r"<script.*?</script>", " ", page, flags=re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S)
    # Inline SVG icons and data: URIs carry path coordinates / encoded junk that
    # can look like digit runs ("843.386.843"). Strip them before mining.
    text = re.sub(r"<svg.*?</svg>", " ", text, flags=re.S)
    text = re.sub(r"data:[^\"'()\s]+", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _html.unescape(text)
    for m in PHONE_RE.finditer(text):
        found.append(m.group(0).strip())
    return found

def fetch(url: str, timeout=25):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "identity",
        },
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if r.status != 200:
                    return False, ""
                ctype = (r.headers.get("Content-Type") or "").lower()
                # Only mine actual documents. A phone number is published in
                # HTML (visible text, tel: link, JSON-LD), never inside a
                # stylesheet, script, image, or SVG asset. Skipping asset types
                # is what keeps SVG path coordinates and CSS values out of the
                # results (e.g. an inline icon path "843.386.843" is not a phone).
                if ctype and not (ctype.startswith("text/html") or ctype.startswith("text/plain")):
                    return False, ""
                return True, r.read(2_000_000).decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                time.sleep(2 + 2 * attempt)
                continue
            return False, ""
        except Exception:
            time.sleep(1 + attempt)
    return False, ""

ASSET_EXT = re.compile(
    r"\.(css|js|mjs|svg|png|jpe?g|gif|webp|ico|woff2?|ttf|eot|json|xml|mp[34]|avi|mov|pdf|zip|tar|gz)(\?|$)",
    re.I,
)

def _is_html_page(url: str) -> bool:
    # Never enqueue assets (css/js/images/svg/fonts/data). A phone number is a
    # document-level fact, not something to mine from an icon or a stylesheet.
    path = urllib.parse.urlparse(url).path.lower()
    if ASSET_EXT.search(path + "?"):
        return False
    if url.endswith("/"):
        return True
    return not ASSET_EXT.search(url)

def crawl(start, max_pages=6, delay=1.5, limit=None, timeout=25):
    from collections import deque
    base_host = urllib.parse.urlparse(start).netloc
    limit = [p.strip() for p in (limit.split(",") if isinstance(limit, str) else (limit or []))]
    q = deque([urllib.parse.urldefrag(start)[0]])
    visited = set()
    while q and len(visited) < max_pages:
        url = q.popleft()
        if url in visited:
            continue
        visited.add(url)
        ok, page = fetch(url, timeout)
        if ok:
            yield url, page
            keep = any((p and p in url) for p in limit) if limit else True
            if not keep and len(visited) >= 5:
                continue
            for m in re.finditer(r'href\s*=\s*["\']([^"\'#]+)["\']', page):
                link = urllib.parse.urldefrag(urllib.parse.urljoin(url, m.group(1).strip()))[0]
                lp = urllib.parse.urlparse(link)
                if lp.netloc != base_host or lp.scheme not in ("http", "https"):
                    continue
                if not _is_html_page(link):
                    continue
                if link not in visited:
                    q.append(link)
        if delay:
            time.sleep(delay + random.random() * delay * 0.5)

def scrape_one(start, max_pages=6, delay=1.5, limit=None, timeout=25):
    rows = []
    pages = 0
    err = None
    try:
        for url, page in crawl(start, max_pages=max_pages, delay=delay, limit=limit, timeout=timeout):
            pages += 1
            for cand in extract_from_html(page):
                if looks_like_phone(cand):
                    rows.append((cand, url))
    except Exception as e:  # noqa: BLE001
        err = str(e)[:200]
    uniq = {}
    order = []
    src = {}
    for raw, url in rows:
        k = canonical(raw)
        if k and k not in uniq:
            uniq[k] = raw
            order.append(k)
            src[k] = url
    phones = [uniq[k] for k in order]
    return {
        "url": start,
        "found": len(phones) > 0,
        "count": len(phones),
        "pages_scanned": pages,
        "source_urls": [src[k] for k in order],
        "phones": phones,
        "error": err,
    }

# --- HTTP server -----------------------------------------------------------
HELP_HTML = """
<h1>Phone Scraper API</h1>
<p>POST a list of business-directory URLs, get back the phone numbers each one publishes.</p>
<pre>
POST /api/phones
{ "urls": ["https://a-site/contact", "https://b-site/stores"] }

Optional fields: max_pages (int, 6), delay (float, 1.5),
                limit ("contact,stores"), timeout (int, 25)

Single URL: GET /api/phones?url=https://a-site/contact
Health:     GET /health
</pre>
"""

class Handler(BaseHTTPRequestHandler):
    server_version = "PhoneScraperAPI/1.0"

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):  # quieter
        pass

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(p.query))
        if p.path == "/health":
            return self._send(200, {"status": "ok"})
        if p.path == "/help":
            return self._send(200, HELP_HTML.encode("utf-8"), "text/html; charset=utf-8")
        if p.path == "/api/phones":
            if "url" not in q:
                return self._send(400, {"error": "missing ?url= (or POST /api/phones with a JSON body)"})
            res = scrape_one(q["url"])
            return self._send(200, {"results": [res]})
        return self._send(404, {"error": "not found"})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        p = urllib.parse.urlparse(self.path)
        if p.path != "/api/phones":
            return self._send(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
            body = json.loads(raw or "{}")
        except Exception as e:  # noqa: BLE001
            return self._send(400, {"error": f"bad JSON: {str(e)[:120]}"})
        urls = body.get("urls")
        if not isinstance(urls, (list, str)):
            return self._send(400, {"error": "need \"urls\": [ ... ]"})
        if isinstance(urls, str):
            urls = [u.strip() for u in urls.split(",") if u.strip()]
        urls = [str(u) for u in urls if str(u).strip()]
        if not urls:
            return self._send(400, {"error": "urls list is empty"})
        opts = {
            "max_pages": int(body.get("max_pages", 6)),
            "delay": float(body.get("delay", 1.5)),
            "limit": body.get("limit"),
            "timeout": int(body.get("timeout", 25)),
        }
        results = [scrape_one(u, **opts) for u in urls]
        return self._send(200, {"results": results})

def serve(port=8399, demo=False, demo_url=None):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"[api] listening on http://127.0.0.1:{port}")
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    if demo:
        import urllib.request
        demo_url = demo_url or "https://www.townsend.com/"
        print(f"[demo] POST /api/phones urls=[{demo_url}]")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/phones",
            data=json.dumps({"urls": [demo_url], "max_pages": 4, "delay": 0.5}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=90) as r:
            out = json.loads(r.read().decode())
        print(json.dumps(out, indent=2)[:1500])
        httpd.shutdown()
    else:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            httpd.shutdown()

def selftest():
    sample = """
    <a href="tel:+1-413-555-0123">Call us</a>
    <a href="tel:4135550124">Call</a>
    <span>(413) 555-0125</span>
    <p>Phone: 413.555.0126  or  +1 413 555 0127</p>
    <div>Order #A-2026-10-07  ref 1234567890  zip 01103  version 2.4.1</div>
    <script>var x = "1234567890123456";</script>
    <script type="application/ld+json">
      {"@type":"Organization","name":"Test","telephone":"+1 (413) 555-0128",
       "contactPoint":{"@type":"ContactPoint","telephone":"413-555-0129"}}
    </script>
    """
    kept = [c for c in extract_from_html(sample) if looks_like_phone(c)]
    expected = {"4135550123", "4135550124", "4135550125",
                "4135550126", "4135550127", "4135550128", "4135550129"}
    norm = set()
    for g in map(canonical, kept):
        norm.add(g[1:] if (g.startswith("1") and len(g) == 11) else g)
    fp = {"01103", "2026", "1234567890", "1234567890123456", "241"}
    ok = expected <= norm and not (fp & norm)
    print("selftest kept:", sorted(kept))
    print("selftest expected present:", expected <= norm)
    print("selftest no false positives:", not (fp & norm))
    return ok

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="API phone number scraper")
    ap.add_argument("--port", type=int, default=8399)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--demo-url", default=None)
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    serve(port=a.port, demo=a.demo, demo_url=a.demo_url)
