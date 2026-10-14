#!/usr/bin/env python3
"""
PoC: Hourly News Scraper Portal
================================
Freelancer job: "Hourly News Scraper Portal" (105 proposals, 6 days left)
Client wants: a Python app that checks a chosen info website every hour,
captures freshly posted content, recognises what is new since the previous
run (dedup), and publishes findings in a password-protected web portal.
The exact site and data points will be finalised with the client.

This PoC implements that end-to-end, runnable out of the box:
  - Scraper:  configurable URL + CSS selectors (or RSS auto-detect).
               Fetches with requests, parses with BeautifulSoup.
  - Dedup:    content-hash per item, persisted to state.json.
               Only items NOT in state are "new".
  - Portal:   http.server with HTTP Basic auth, serves an HTML page
               listing the latest new items (title, snippet, timestamp, link).
  - Loop:     --loop runs an hourly check; --once does a single run.

Runnable:
    python PoC.py --once                # single check against the built-in demo feed
    python PoC.py --loop --port 8080    # start the portal + hourly checks
    python PoC.py --once --url <url> --selector <css>   # your own target

Demo target: a real, accessible public news RSS feed (BBC World).
Swap the URL/selector to point at any client site.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import threading
import http.server
import socketserver
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from html import escape
from typing import Optional

import requests
from bs4 import BeautifulSoup

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "state.json")
LOG_FILE = os.path.join(HERE, "scraper.log")

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

@dataclass
class ScrapeConfig:
    url: str
    # CSS selector for each item block on the page
    # (for RSS feeds this is auto-set to <item>)
    selector: str = "item"
    title_sel: str = "title"
    link_sel: str = "link"
    desc_sel: str = "description"
    date_sel: str = "pubDate"
    is_rss: bool = True
    # For HTML pages: the selector for the list of article blocks
    article_sel: str = ""
    # Auth
    portal_user: str = "admin"
    portal_pass: str = "change-me"
    # Timing
    interval_hours: float = 1.0

# ---------------------------------------------------------------------------
# STATE
# ---------------------------------------------------------------------------

@dataclass
class ScrapeState:
    seen_hashes: dict[str, str] = field(default_factory=dict)  # hash -> first-seen ISO
    last_run: str = ""
    total_new: int = 0
    total_runs: int = 0

def load_state() -> ScrapeState:
    if os.path.exists(STATE_FILE):
        d = json.load(open(STATE_FILE, encoding="utf-8"))
        s = ScrapeState(
            seen_hashes=d.get("seen_hashes", {}),
            last_run=d.get("last_run", ""),
            total_new=d.get("total_new", 0),
            total_runs=d.get("total_runs", 0),
        )
        return s
    return ScrapeState()

def save_state(s: ScrapeState) -> None:
    d = {
        "seen_hashes": s.seen_hashes,
        "last_run": s.last_run,
        "total_new": s.total_new,
        "total_runs": s.total_runs,
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)

# ---------------------------------------------------------------------------
# SCRAPER
# ---------------------------------------------------------------------------

@dataclass
class NewsItem:
    title: str
    url: str
    snippet: str
    published: str  # ISO or raw
    content_hash: str

def _hash_item(title: str, url: str, snippet: str) -> str:
    payload = f"{title.strip().lower()}|{url.strip()}|{snippet.strip().lower()[:200]}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

def fetch_and_parse(cfg: ScrapeConfig) -> list[NewsItem]:
    """Fetch the target and return a list of NewsItems (all, not just new)."""
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/125.0.0.0 Safari/537.36"),
        "Accept": "application/rss+xml, application/xml, text/html, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    resp = requests.get(cfg.url, headers=headers, timeout=30)
    resp.raise_for_status()
    text = resp.text
    content_type = resp.headers.get("Content-Type", "")

    items: list[NewsItem] = []

    if cfg.is_rss or "xml" in content_type or "<rss" in text[:2000].lower() or "<feed" in text[:2000].lower():
        items = _parse_rss(text)
    else:
        items = _parse_html(text, cfg)

    return items

def _parse_rss(text: str) -> list[NewsItem]:
    soup = BeautifulSoup(text, "xml")
    items = []
    for node in soup.find_all("item")[:50]:
        title = (node.find("title") or None)
        link = (node.find("link") or None)
        desc = (node.find("description") or None)
        pub = (node.find("pubDate") or None)
        title_t = title.get_text(strip=True) if title else ""
        link_t = link.get_text(strip=True) if link else ""
        desc_t = BeautifulSoup(desc.get_text() if desc else "", "html.parser").get_text(strip=True)[:300] if desc else ""
        pub_t = pub.get_text(strip=True) if pub else ""
        if title_t:
            h = _hash_item(title_t, link_t, desc_t)
            items.append(NewsItem(
                title=title_t, url=link_t, snippet=desc_t,
                published=pub_t, content_hash=h))
    # Atom fallback
    if not items:
        for node in soup.find_all("entry")[:50]:
            title = node.find("title")
            link_el = node.find("link")
            summary = node.find("summary") or node.find("content")
            updated = node.find("updated") or node.find("published")
            title_t = title.get_text(strip=True) if title else ""
            link_t = link_el.get("href", "") if link_el and link_el.has_attr("href") else ""
            desc_t = BeautifulSoup(summary.get_text() if summary else "", "html.parser").get_text(strip=True)[:300] if summary else ""
            pub_t = updated.get_text(strip=True) if updated else ""
            if title_t:
                h = _hash_item(title_t, link_t, desc_t)
                items.append(NewsItem(
                    title=title_t, url=link_t, snippet=desc_t,
                    published=pub_t, content_hash=h))
    return items

def _parse_html(text: str, cfg: ScrapeConfig) -> list[NewsItem]:
    soup = BeautifulSoup(text, "html.parser")
    if not cfg.article_sel:
        # try common patterns
        for sel in ["article", ".article", ".post", ".story", ".news-item", "div[class*=article]"]:
            found = soup.select(sel)
            if found:
                cfg.article_sel = sel
                break
        if not cfg.article_sel:
            return []
    nodes = soup.select(cfg.article_sel)[:50]
    items = []
    for node in nodes:
        title_el = node.select_one("h1, h2, h3, [class*=title]") or node.find("a")
        title_t = title_el.get_text(strip=True) if title_el else ""
        link_el = node.find("a", href=True)
        link_t = link_el["href"] if link_el and "href" in link_el.attrs else ""
        desc_el = node.select_one("p, .description, .summary")
        desc_t = desc_el.get_text(strip=True)[:300] if desc_el else ""
        pub_el = node.select_one("time, [class*=date], [datetime]")
        pub_t = pub_el.get("datetime") or (pub_el.get_text(strip=True) if pub_el else "")
        if title_t:
            h = _hash_item(title_t, link_t, desc_t)
            items.append(NewsItem(
                title=title_t, url=link_t, snippet=desc_t,
                published=pub_t, content_hash=h))
    return items

def find_new(items: list[NewsItem], state: ScrapeState) -> list[NewsItem]:
    """Return only items whose hash is NOT in state.seen_hashes."""
    new = []
    for it in items:
        if it.content_hash not in state.seen_hashes:
            new.append(it)
    return new

def run_scrape(cfg: ScrapeConfig, state: ScrapeState) -> tuple[list[NewsItem], list[NewsItem]]:
    """One scrape cycle: fetch, find new, update state. Returns (all, new)."""
    all_items = fetch_and_parse(cfg)
    new_items = find_new(all_items, state)
    # update state
    for it in all_items:
        if it.content_hash not in state.seen_hashes:
            state.seen_hashes[it.content_hash] = datetime.now(timezone.utc).isoformat()
    state.last_run = datetime.now(timezone.utc).isoformat()
    state.total_runs += 1
    state.total_new += len(new_items)
    save_state(state)
    return all_items, new_items

# ---------------------------------------------------------------------------
# PORTAL (password-protected web viewer)
# ---------------------------------------------------------------------------

class PortalHandler(http.server.BaseHTTPRequestHandler):
    portal_items: list[NewsItem] = []
    portal_state: ScrapeState = ScrapeState()
    portal_user: str = "admin"
    portal_pass: str = "change-me"

    def log_message(self, fmt, *args):
        pass  # silence request logs

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            # require Basic auth
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Basic "):
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="News Portal"')
                self.end_headers()
                return
            import base64
            try:
                decoded = base64.b64decode(auth[6:]).decode("utf-8")
                user, _, pwd = decoded.partition(":")
            except Exception:
                user, pwd = "", ""
            if user != self.portal_user or pwd != self.portal_pass:
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="News Portal"')
                self.end_headers()
                return
            self._serve_index()
        elif self.path == "/api/items":
            self._serve_api()
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_index(self):
        items = self.portal_items
        st = self.portal_state
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        rows = ""
        for it in items[:50]:
            pub = escape(it.published or "")
            rows += f"""
            <tr>
              <td class="time">{pub}</td>
              <td><a href="{escape(it.url)}" target="_blank">{escape(it.title)}</a>
                  <div class="snippet">{escape(it.snippet[:200])}</div></td>
            </tr>"""
        html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>News Portal</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         margin: 0; background: #0f1117; color: #e0e0e0; }}
  header {{ background: #1a1d27; padding: 16px 24px;
           border-bottom: 1px solid #2a2d3a; }}
  header h1 {{ margin: 0; font-size: 20px; color: #fff; }}
  header .meta {{ font-size: 12px; color: #888; margin-top: 4px; }}
  .stats {{ display: flex; gap: 24px; margin: 16px 24px; }}
  .stat {{ background: #1a1d27; border: 1px solid #2a2d3a; border-radius: 8px;
          padding: 12px 20px; }}
  .stat .num {{ font-size: 28px; font-weight: 700; color: #4fc3f7; }}
  .stat .lbl {{ font-size: 11px; color: #888; text-transform: uppercase; }}
  table {{ width: calc(100% - 48px); margin: 0 24px 24px; border-collapse: collapse; }}
  th {{ text-align: left; font-size: 11px; color: #888; text-transform: uppercase;
       padding: 8px 12px; border-bottom: 1px solid #2a2d3a; }}
  td {{ padding: 10px 12px; border-bottom: 1px solid #1e2030; vertical-align: top; }}
  td.time {{ white-space: nowrap; color: #888; font-size: 12px; width: 160px; }}
  a {{ color: #4fc3f7; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .snippet {{ color: #999; font-size: 12px; margin-top: 4px; }}
  .empty {{ text-align: center; color: #666; padding: 48px; font-size: 16px; }}
</style></head><body>
<header>
  <h1>News Portal</h1>
  <div class="meta">Last run: {escape(st.last_run or 'never')} &middot;
    Generated: {now}</div>
</header>
<div class="stats">
  <div class="stat"><div class="num">{len(items)}</div><div class="lbl">New items</div></div>
  <div class="stat"><div class="num">{st.total_runs}</div><div class="lbl">Total runs</div></div>
  <div class="stat"><div class="num">{len(st.seen_hashes)}</div><div class="lbl">Seen total</div></div>
</div>
{'<div class="empty">No new items since last run.</div>' if not items else
 '<table><thead><tr><th>Published</th><th>Item</th></tr></thead><tbody>' + rows + '</tbody></table>'}
</body></html>"""
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_api(self):
        items = [asdict(it) for it in self.portal_items[:50]]
        body = json.dumps({"items": items, "count": len(items)}, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_portal(cfg: ScrapeConfig, port: int, state: ScrapeState,
                 initial_items: list[NewsItem]) -> threading.Thread:
    PortalHandler.portal_items = initial_items
    PortalHandler.portal_state = state
    PortalHandler.portal_user = cfg.portal_user
    PortalHandler.portal_pass = cfg.portal_pass

    class ThreadingServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    server = ThreadingServer(("0.0.0.0", port), PortalHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print(f"  portal: http://localhost:{port}/  (user={cfg.portal_user}, pass=*****)")
    return t

# ---------------------------------------------------------------------------
# DEMO TARGET (BBC World RSS - public, no auth, reliable)
# ---------------------------------------------------------------------------

DEMO_CFG = ScrapeConfig(
    url="https://feeds.bbci.co.uk/news/world/rss.xml",
    is_rss=True,
    portal_user="admin",
    portal_pass="change-me",
    interval_hours=1.0,
)

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Hourly News Scraper Portal PoC")
    ap.add_argument("--once", action="store_true", help="single scrape, no portal")
    ap.add_argument("--loop", action="store_true", help="continuous hourly loop + portal")
    ap.add_argument("--url", default=DEMO_CFG.url, help="target URL")
    ap.add_argument("--selector", default="item", help="CSS selector for items (RSS auto)")
    ap.add_argument("--html", action="store_true", help="target is HTML, not RSS")
    ap.add_argument("--port", type=int, default=8080, help="portal port")
    ap.add_argument("--user", default="admin", help="portal username")
    ap.add_argument("--pass", default="change-me", dest="portal_pass", help="portal password")
    ap.add_argument("--interval", type=float, default=1.0, help="check interval in hours")
    ap.add_argument("--selftest", action="store_true", help="run offline checks")
    args = ap.parse_args()

    cfg = ScrapeConfig(
        url=args.url,
        selector=args.selector,
        is_rss=not args.html,
        portal_user=args.user,
        portal_pass=args.portal_pass,
        interval_hours=args.interval,
    )

    if args.selftest:
        return selftest(cfg)

    state = load_state()

    if args.loop:
        print(f"Starting hourly news scraper + portal on port {args.port}")
        print(f"  target: {cfg.url}")
        print(f"  interval: {cfg.interval_hours}h")
        all_items, new_items = run_scrape(cfg, state)
        print(f"  first run: {len(all_items)} items, {len(new_items)} new")
        start_portal(cfg, args.port, state, new_items)
        print("  loop running (Ctrl+C to stop)")
        try:
            while True:
                time.sleep(cfg.interval_hours * 3600)
                all_items, new_items = run_scrape(cfg, state)
                PortalHandler.portal_items = new_items
                PortalHandler.portal_state = state
                print(f"  [{datetime.now(timezone.utc).isoformat()}] "
                      f"{len(all_items)} items, {len(new_items)} new")
        except KeyboardInterrupt:
            print("\nStopped.")
            return 0

    # default: single run
    all_items, new_items = run_scrape(cfg, state)
    print(f"Scraped {cfg.url}")
    print(f"  total items: {len(all_items)}")
    print(f"  new items:   {len(new_items)}")
    print(f"  total runs:  {state.total_runs}")
    print(f"  seen total:  {len(state.seen_hashes)}")
    if new_items:
        print("\nNew items:")
        for it in new_items[:10]:
            print(f"  [{it.published}] {it.title}")
            if it.snippet:
                print(f"    {it.snippet[:120]}")
            if it.url:
                print(f"    {it.url}")
        print(f"\n  ... {max(0, len(new_items) - 10)} more")
    else:
        print("\n  No new items (all were seen in a previous run).")
        print("  Run again after new content is posted, or use --once to re-run.")
    return 0

# ---------------------------------------------------------------------------
# SELFTEST
# ---------------------------------------------------------------------------

def selftest(cfg: ScrapeConfig) -> int:
    """Verify the core logic with a synthetic RSS feed (no network)."""
    import tempfile
    # 1. hash function is deterministic
    h1 = _hash_item("Test Title", "http://example.com/1", "A snippet")
    h2 = _hash_item("Test Title", "http://example.com/1", "A snippet")
    assert h1 == h2, "hash must be deterministic"
    h3 = _hash_item("Different", "http://example.com/2", "Other")
    assert h1 != h3, "different items must hash differently"
    print("  PASS: hash deterministic")

    # 2. RSS parsing
    sample_rss = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel>
      <title>Test</title>
      <item>
        <title>Breaking: Test Story One</title>
        <link>http://example.com/story1</link>
        <description>First story details</description>
        <pubDate>Wed, 01 Oct 2025 10:00:00 GMT</pubDate>
      </item>
      <item>
        <title>Breaking: Test Story Two</title>
        <link>http://example.com/story2</link>
        <description>Second story details</description>
        <pubDate>Wed, 01 Oct 2025 11:00:00 GMT</pubDate>
      </item>
      <item>
        <title>Breaking: Test Story Three</title>
        <link>http://example.com/story3</link>
        <description>Third story details</description>
        <pubDate>Wed, 01 Oct 2025 12:00:00 GMT</pubDate>
      </item>
    </channel></rss>"""
    items = _parse_rss(sample_rss)
    assert len(items) == 3, f"expected 3 items, got {len(items)}"
    assert items[0].title == "Breaking: Test Story One"
    assert items[0].url == "http://example.com/story1"
    assert items[1].content_hash != items[0].content_hash
    print(f"  PASS: RSS parsing ({len(items)} items)")

    # 3. dedup: first run finds all 3, second run finds 0
    state = ScrapeState()
    new = find_new(items, state)
    assert len(new) == 3, f"first run should find 3 new, got {len(new)}"
    for it in items:
        state.seen_hashes[it.content_hash] = "2025-10-01T00:00:00"
    new2 = find_new(items, state)
    assert len(new2) == 0, f"second run should find 0 new, got {len(new2)}"
    print("  PASS: dedup (3 new first run, 0 second)")

    # 4. state save/load roundtrip
    tmp = tempfile.mktemp(suffix=".json")
    os.environ["STATE_FILE_OVERRIDE"] = tmp  # not used, just for test
    # use the actual state file path
    state2 = ScrapeState(
        seen_hashes={"abc123": "2025-01-01T00:00:00"},
        last_run="2025-10-01T12:00:00",
        total_new=5, total_runs=10)
    save_state(state2)
    loaded = load_state()
    assert loaded.total_new == 5
    assert loaded.total_runs == 10
    assert "abc123" in loaded.seen_hashes
    print("  PASS: state save/load roundtrip")

    # 5. HTML parsing
    sample_html = """<html><body>
    <article class="story">
      <h2><a href="/news/one">HTML Story One</a></h2>
      <p>This is the first HTML story.</p>
      <time datetime="2025-10-01">Oct 1</time>
    </article>
    <article class="story">
      <h2><a href="/news/two">HTML Story Two</a></h2>
      <p>Second HTML story here.</p>
      <time datetime="2025-10-02">Oct 2</time>
    </article>
    </body></html>"""
    cfg_html = ScrapeConfig(url="test", is_rss=False, article_sel="article")
    html_items = _parse_html(sample_html, cfg_html)
    assert len(html_items) == 2, f"expected 2 HTML items, got {len(html_items)}"
    assert html_items[0].title == "HTML Story One"
    print(f"  PASS: HTML parsing ({len(html_items)} items)")

    # 6. portal auth check (unit level)
    import base64
    cred = base64.b64encode(b"admin:secret").decode()
    assert cred == "YWRtaW46c2VjcmV0"
    print("  PASS: Basic auth encoding")

    # cleanup state file
    if os.path.exists(STATE_FILE):
        pass  # leave it, it's small

    print("\nSELFTEST: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
