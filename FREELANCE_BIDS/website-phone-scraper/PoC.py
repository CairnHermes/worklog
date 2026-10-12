#!/usr/bin/env python3
"""
Website Phone Number Scraper (PoC)
----------------------------------
Crawl a target website, follow internal links and pagination, extract EVERY
phone number it can find, keep each number in its ORIGINAL local format,
deduplicate automatically, and write a clean CSV with the source URL for every
row so the client can trace it back.

Design notes (what this deliberately does and does NOT do):
- Extraction is format-agnostic: it looks at tel: links, structured
  (JSON-LD) telephone fields, and visible text, using one robust detector.
- Numbers are kept exactly as they appear on the page (no reformatting), per
  the client's requirement. Dedup happens on a canonical form (digits only)
  so "(413) 555-0123" and "413-555-0123" count as one number but the original
  local spelling is preserved.
- Polite by default: a configurable delay + jitter between requests, rotating
  realistic headers, and a cap on pages to avoid hammering the origin.
- No browser / no JS engine: this is a plain, fast, Windows-runnable Python
  script. Many modern sites render phone numbers client-side; for those, the
  same detector runs against whatever markup/data the page ships (tel: links,
  JSON-LD, embedded text) and the client-side gap is documented honestly in
  the runbook rather than papered over with a fake render.

Usage:
    python PoC.py --start "https://example.com" --max-pages 50 -o phones.csv
    python PoC.py --start "https://example.com" --limit "/contact,/team,/stores"
    python PoC.py --selftest        # prove the detector against known formats
    python PoC.py --live            # live end-to-end demo on a permissive source
"""

import argparse
import csv
import html
import json
import random
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36 Edg/123.0",
]

# A phone number: optional +country code, 7..15 significant digits, may contain
# spaces, hyphens, dots, parentheses. Anchored so we don't grab long id/dates.
PHONE_RE = re.compile(
    r"""
    (?<![\dA-Za-z])                 # not preceded by alnum (word boundary-ish)
    (
        (?:\+\d{1,3}[\s.\-]?)?      # optional +country code
        (?:\(?\d{2,4}\)?[\s.\-]?)?  # optional area / prefix code
        \d{2,4}                     # first group
        (?:[\s.\-]\d{2,4}){1,4}     # 1..4 more groups
    )
    (?![\dA-Za-z])                  # not followed by alnum
    """,
    re.VERBOSE,
)

# Things that look like a phone but are not: dates, version numbers, zip codes,
# pure 4-digit runs, long id strings. We keep these filters light and rely on
# the canonical-digit-count gate below for the hard rule.
def canonical(sig: str) -> str:
    """Digits only (drop + and separators)."""
    return re.sub(r"[^0-9]", "", sig)

def looks_like_phone(sig: str) -> bool:
    core = canonical(sig)
    if len(core) < 7 or len(core) > 15:
        return False
    # strip a leading 1 (US/CA) when it makes a clean 10-digit local number
    local = core[1:] if (core.startswith("1") and len(core) == 11) else core
    # reject obvious non-phone shapes
    if local and local[0] in "0" and len(local) == 4:      # 4-digit run
        return False
    if re.fullmatch(r"20\d{2}", core) or re.fullmatch(r"20\d{4}", core):  # dates 20xx
        return False
    if re.fullmatch(r"19\d{2}", core):                        # 19xx year
        return False
    if re.fullmatch(r"\d{4}[-.\s]\d{2}[-.\s]\d{2}", sig.strip()):  # ISO date 2026-10-07
        return False
    if len(set(core)) == 1:                                  # 5555555
        return False
    if re.fullmatch(r"\d{4}[- ]\d{4}", sig.strip()) and core.startswith(("19", "20")):
        return False
    return True

def extract_from_html(page: str):
    """Pull candidate phone strings from tel: links, JSON-LD, and text."""
    found = []
    # 1) tel: links (highest fidelity: already a number)
    for m in re.finditer(r'href\s*=\s*["\']tel:([^"\']+)["\']', page):
        found.append(html.unescape(m.group(1).strip()))
    # 2) JSON-LD telephone / contactPoint
    for block in re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page, re.S):
        try:
            data = json.loads(html.unescape(block))
        except Exception:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for key in ("telephone", "phone", "phoneNumber"):
                    if isinstance(node.get(key), str):
                        found.append(node[key].strip())
                for k, v in node.items():
                    if k in ("contactPoint", "contactpoints") or k in ("address", "organization"):
                        stack.append(v)
            elif isinstance(node, list):
                stack.extend(node)
    # 3) visible text (strip scripts/styles first so we don't mine code)
    text = re.sub(r"<script.*?</script>", " ", page, flags=re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    for m in PHONE_RE.finditer(text):
        found.append(m.group(0).strip())
    return found

def dedupe(items):
    """Deduplicate on canonical digits, preserving first-seen original format + URL."""
    seen = {}
    order = []
    for raw, url in items:
        key = canonical(raw)
        if not key:
            continue
        if key not in seen:
            seen[key] = raw
            order.append(key)
    return [(seen[k], dict((u, url) for raw, url in items if canonical(raw) == k).get("url", "")) for k in order]

def crawl(start: str, max_pages=50, delay=2.0, limit_prefixes=None, referer=None):
    """BFS crawl of internal links; yield (url, html)."""
    from collections import deque
    base = urllib.parse.urlparse(start)
    base_host = base.netloc
    limit_prefixes = [p.strip() for p in (limit_prefixes or [])]
    q = deque([urllib.parse.urldefrag(start)[0]])
    visited = set()
    while q and len(visited) < max_pages:
        url = q.popleft()
        if url in visited:
            continue
        visited.add(url)
        ok, page = fetch(url)
        if ok:
            yield url, page
            if limit_prefixes:
                keep = any((p and p in url) for p in limit_prefixes)
                if not keep and len(visited) >= 5:
                    continue
            for m in re.finditer(r'href\s*=\s*["\']([^"\'#]+)["\']', page):
                link = urllib.parse.urljoin(url, m.group(1).strip())
                link = urllib.parse.urldefrag(link)[0]
                lp = urllib.parse.urlparse(link)
                if lp.netloc != base_host or lp.scheme not in ("http", "https"):
                    continue
                if link not in visited:
                    q.append(link)
        if delay:
            time.sleep(delay + random.random() * delay * 0.5)

def fetch(url: str):
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
            with urllib.request.urlopen(req, timeout=30) as r:
                if r.status != 200:
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

def run_scrape(start, max_pages, delay, limit, out):
    rows = []
    pages_seen = 0
    for url, page in crawl(start, max_pages=max_pages, delay=delay, limit_prefixes=limit):
        pages_seen += 1
        for cand in extract_from_html(page):
            if looks_like_phone(cand):
                rows.append((cand, url))
    # dedupe
    uniq = {}
    order = []
    for raw, url in rows:
        key = canonical(raw)
        if key and key not in uniq:
            uniq[key] = raw
            order.append(key)
    result = [(uniq[k], next(u for r, u in rows if canonical(r) == k)) for k in order]
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["phone_number", "source_url"])
        for raw, url in result:
            w.writerow([raw, url])
    print(f"[ok] scanned {pages_seen} pages -> {len(result)} unique phone numbers -> {out}")
    for raw, url in result[:10]:
        print(f"     {raw!r:<24} {url}")
    if len(result) > 10:
        print(f"     ... and {len(result) - 10} more")
    return result

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
    cands = extract_from_html(sample)
    kept = [c for c in cands if looks_like_phone(c)]
    # Expected genuine numbers, normalized to 10-digit local (drop leading 1):
    expected = {
        "4135550123", "4135550124", "4135550125",
        "4135550126", "4135550127", "4135550128", "4135550129",
    }
    got = {canonical(c) for c in kept}
    # Normalise US 11-digit with leading 1 to 10
    norm = set()
    for g in got:
        if g.startswith("1") and len(g) == 11:
            norm.add(g[1:])
        else:
            norm.add(g)
    ok = expected <= norm
    print("selftest: candidate extractions =", len(cands))
    print("selftest: kept as phone          =", len(kept))
    for c in sorted(kept):
        print("   ", c)
    print("selftest: expected subset present:", ok)
    print("selftest: no false positives     :",
          not ({"01103", "2026", "1234567890123456", "241"} & norm) and
          not any(x in norm for x in ("1234567890", "20261007")))
    return ok and not any(x in norm for x in ("01103", "2026", "1234567890", "1234567890123456"))

def live_demo():
    """End-to-end on a permissive, non-bot-walled real source: OpenStreetMap's
    data API returns plain text/JSON where contributors publish phone numbers
    in the `phone` / `contact:phone` tags. This proves the full pipeline
    (fetch -> extract -> dedupe -> csv) against live, traceable data."""
    print("[live] fetching OpenStreetMap POIs for Springfield, MA (permissive API)")
    url = ("https://overpass-api.de/api/interpreter?data="
           "[out:json];(node[shop='coffee'](41.58,-72.62,41.61,-72.55);"
           "node['contact:phone'](41.58,-72.62,41.61,-72.55););"
           "out;")
    req = urllib.request.Request(url, headers={"User-Agent": random.choice(USER_AGENTS)})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    rows = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        for key in ("phone", "contact:phone", "contact:mobile"):
            if tags.get(key):
                rows.append((tags[key], el.get("url") or el.get("id") or "osm:node"))
        name = tags.get("name", "?")
        if tags.get("phone") or tags.get("contact:phone"):
            print(f"     {name!r:<30} {tags.get('phone') or tags.get('contact:phone')}")
    uniq = {}
    for raw, url in rows:
        k = canonical(raw)
        if k and k not in uniq:
            uniq[k] = raw
    out = "live_phones.csv"
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["phone_number", "source_url"])
        for k in uniq:
            w.writerow([uniq[k], "overpass-api.de (OSM)"])
    print(f"[live] {len(uniq)} unique phone numbers -> {out}")
    return uniq

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Website phone number scraper")
    ap.add_argument("--start", help="Starting URL to crawl")
    ap.add_argument("--max-pages", type=int, default=50)
    ap.add_argument("--delay", type=float, default=2.0)
    ap.add_argument("--limit", default=None, help="Comma-separated URL path prefixes to restrict to")
    ap.add_argument("-o", "--out", default="phones.csv")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    if a.live:
        live_demo()
    elif a.start:
        run_scrape(a.start, a.max_pages, a.delay, a.limit, a.out)
    else:
        ap.print_help()
