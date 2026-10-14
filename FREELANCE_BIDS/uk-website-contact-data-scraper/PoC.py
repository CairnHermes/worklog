#!/usr/bin/env python3
"""UK Website Contact Data Scraper -- proof of concept.

One-time extraction of contact details (email, UK phone, postal address)
from a paginated, publicly-accessible website, normalised and exported to
a single tidy .xlsx workbook.

Design (matches the client's brief):
  * Crawls result pages by following pagination (either a "next" link or a
    numeric ?page=N / ?start=N query parameter).
  * Extracts emails (regex + RFC-lite validation), UK phone numbers
    (normalised to +44 where the digits allow), and postal addresses
    (heuristic multi-line block detection).
  * Deduplicates records, trims whitespace, enforces one-record-per-row.
  * Exports a clean .xlsx (header row + one record per line) via openpyxl.
  * Respects the site: polite delay between requests, a max-page cap, and
    NO captcha bypass (if a challenge page is seen it stops and reports).

CLI:
  python PoC.py selftest   # spins up a LOCAL paginated directory site and
                           # runs the full crawl -> validate -> xlsx path.
  python PoC.py demo       # selftest, plus prints the extracted rows.
  python PoC.py crawl <url>  # real run against a target URL (client-provided)
"""
from __future__ import annotations

import argparse
import html
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urljoin, urlparse

# --------------------------------------------------------------------------- #
# Extraction primitives
# --------------------------------------------------------------------------- #

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# UK phone candidates: +44/0044 forms, or 0[123] local formats (01xxx, 02x
# area codes, 03 non-geo). Captured loosely, validated by digit count.
PHONE_CAND_RE = re.compile(
    r"tel:([+\d][+\d\s\-()]{6,})"
    r"|\+44[\s\-]?\d{9,10}"
    r"|0044\d{9,10}"
    r"|(?<!\d)0[123][\d\s\-]{7,11}\d(?!\d)"
)

EMAIL_BAD = re.compile(r"^(noreply|no-reply|do-not-reply|donotreply)@", re.I)
# common image-filename / tracking noise
EMAIL_SKIP = re.compile(r"\.(png|jpe?g|gif|webp|svg|css|js|woff2?)$", re.I)


def clean(s: str) -> str:
    """Trim + collapse internal whitespace runs."""
    return re.sub(r"\s+", " ", html.unescape(s or "")).strip()


def valid_email(addr: str) -> bool:
    """RFC-lite: one @, local + domain, no spaces, TLD present."""
    addr = addr.strip()
    if "@" not in addr or addr.startswith(".") or addr.endswith("."):
        return False
    local, _, dom = addr.partition("@")
    if not local or not dom or " " in addr:
        return False
    if EMAIL_BAD.match(addr) or EMAIL_SKIP.search(dom):
        return False
    if not re.match(r"^[A-Za-z0-9._%+\-]+$", local):
        return False
    if not re.match(r"^[A-Za-z0-9.\-]+(\.[A-Za-z]{2,})+$", dom):
        return False
    return True


def normalise_uk_phone(raw: str) -> str:
    """Best-effort normalise a UK phone string to +44 form.

    Returns the raw (cleaned) string if it can't be confidently read as a
    UK number -- we never fabricate digits we don't have.
    """
    digits = re.sub(r"[^\d]", "", raw)
    # Already +44 / 0044
    m = re.search(r"(?:\+44|0044)(\d{9,10})", raw)
    if m:
        return "+44" + m.group(1)
    # 09 / 01 leading local formats
    if digits.startswith("0") and 9 <= len(digits) <= 11:
        return "+44" + digits[1:]
    return clean(raw)


def looks_like_uk_phone(raw: str) -> bool:
    d = re.sub(r"[^\d]", "", raw)
    return bool(
        re.search(r"^\+44\d{9,10}$", d)
        or re.search(r"^0044\d{9,10}$", d)
        or (d.startswith("0") and 9 <= len(d) <= 11)
    )


def extract_emails(text: str) -> list[str]:
    out: list[str] = []
    for m in EMAIL_RE.findall(text):
        if valid_email(m) and m.lower() not in out:
            out.append(m)
    return out


def extract_phones(text: str) -> list[str]:
    out: list[str] = []
    for m in PHONE_CAND_RE.finditer(text):
        raw = m.group(0)
        if raw.startswith("tel:"):
            raw = raw[4:]
        if looks_like_uk_phone(raw):
            norm = normalise_uk_phone(raw)
            if norm and norm not in out:
                out.append(norm)
    return out


# Postal address: 2-4 consecutive non-empty lines that look like a UK address
# (line with a street token + a line with a town + optional postcode block).
POSTCODE_RE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b", re.I)
STREET_RE = re.compile(r"\b\d+\s+[A-Za-z]+(?:\s+[A-Za-z]+)?\s+(?:Street|St|Road|Rd|Avenue|Ave|Lane|Ln|Close|Crescent|Cres|Drive|Dr|Way|Gardens|Square|Sq|Terrace|Terr|Place|Pl|Highway|Heath|Hill|Row|Court|Ct|Walk|Park|Mill|Yard|Green)\b", re.I)
LABEL_RE = re.compile(r"^(email|phone|tel|fax|web|website|url|address)\s*:", re.I)


def extract_addresses(text: str) -> list[str]:
    """Heuristic: find contiguous line blocks that read as a UK postal address."""
    lines = [clean(l) for l in text.splitlines()]
    found: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if STREET_RE.search(line) and not LABEL_RE.search(line):
            block = [line]
            j = i + 1
            # up to 3 more lines, stopping at labeled fields (Email:/Phone:/...)
            while j < len(lines) and len(block) < 4 and lines[j] \
                    and not LABEL_RE.search(lines[j]):
                block.append(lines[j])
                j += 1
            joined = " ".join(block)
            if POSTCODE_RE.search(joined):
                addr = clean(joined)
                if addr not in found:
                    found.append(addr)
            i = j
        else:
            i += 1
    return found


@dataclass
class Record:
    source_url: str
    email: str = ""
    phone: str = ""
    address: str = ""
    raw_text: str = field(default="", repr=False)

    def key(self) -> tuple:
        return (self.email.lower(), self.phone, self.address.lower())


# --------------------------------------------------------------------------- #
# Crawler (pluggable fetcher)
# --------------------------------------------------------------------------- #

class Fetcher:
    """Base fetcher. Returns (html_text, final_url) for a URL."""

    def fetch(self, url: str) -> tuple[str, str]:
        raise NotImplementedError


class HttpFetcher(Fetcher):
    """Real HTTP via stdlib urllib (no third-party runtime deps)."""

    def __init__(self, user_agent: str, timeout: float = 15.0):
        self.ua = user_agent
        self.timeout = timeout

    def fetch(self, url: str) -> tuple[str, str]:
        import urllib.request
        req = urllib.request.Request(url, headers={
            "User-Agent": self.ua,
            "Accept": "text/html,application/xhtml+xml",
        })
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = resp.read().decode("utf-8", errors="replace")
            return data, resp.geturl()


def is_captcha_page(text: str) -> bool:
    lowered = text.lower()
    markers = ["recaptcha", "hcaptcha", "cf-challenge", "please verify you are not",
               "captcha", "unusual traffic", "access denied", "403 forbidden"]
    return any(m in lowered for m in markers)


def next_page_url(current: str, page_links: list[str]) -> str | None:
    """Pick the next page from discovered links (text 'next' or page param)."""
    cur = urlparse(current)
    curq = {k: v[0] for k, v in parse_qs(cur.query).items()}
    best = None
    best_n = 1
    for u in page_links:
        pu = urlparse(urljoin(current, u))
        if pu.netloc != cur.netloc:
            continue
        ptext = u.lower()
        if "next" in ptext:
            return urljoin(current, u)
        q = {k: v[0] for k, v in parse_qs(pu.query).items()}
        for key in ("page", "start", "offset", "p"):
            if key in q and q[key].isdigit():
                n = int(q[key])
                if n > int(curq.get(key, 1)) and n > best_n:
                    best = urljoin(current, u)
                    best_n = n
    return best


def crawl(base_url: str, fetcher: Fetcher, max_pages: int = 20,
          delay: float = 1.0, on_page=None) -> list[tuple[str, str]]:
    """Crawl result pages; return list of (url, html). Stops on captcha."""
    visited: set[str] = set()
    pages: list[tuple[str, str]] = []
    frontier = [base_url]
    while frontier and len(pages) < max_pages:
        url = frontier.pop(0)
        if url in visited:
            continue
        visited.add(url)
        text, final = fetcher.fetch(url)
        if is_captcha_page(text):
            raise RuntimeError(f"Captcha/challenge page detected at {final} -- "
                               f"stopping (no bypass by design)")
        pages.append((final, text))
        if on_page:
            on_page(final)
        # discover next
        link_re = re.compile(r"<a\b[^>]*href=[\"']([^\"']+)[\"']", re.I)
        links = [html.unescape(l) for l in link_re.findall(text)]
        nxt = next_page_url(final, links)
        if nxt and nxt not in visited:
            frontier.append(nxt)
        if delay > 0:
            time.sleep(delay)
    return pages


def _strip_tags(block: str) -> str:
    block = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", block, flags=re.I | re.S)
    out = re.sub(r"<[^>]+>", "\n", block)
    # collapse blank lines so block-based heuristics see contiguous lines
    out = re.sub(r"\n\s*\n+", "\n", out)
    return out


def extract_record_from_block(block: str) -> Record | None:
    """Pull one Record out of a single record block (card / row / item)."""
    visible = _strip_tags(block)
    email = extract_emails(visible)[0] if extract_emails(visible) else ""
    phone = extract_phones(visible)[0] if extract_phones(visible) else ""
    address = extract_addresses(visible)[0] if extract_addresses(visible) else ""
    if not (email or phone or address):
        return None
    return Record(source_url="", email=email, phone=phone, address=address)


def _record_blocks(html_text: str) -> list[str]:
    """Split a page into per-record blocks.

    Tries common directory containers (article, li, tr, and div/article with a
    class containing 'card'/'item'/'result'). Falls back to the whole body if
    none are found (still one record per email/phone/addr set below).
    """
    # most specific first
    for pattern in (
        r"<article\b.*?</article>",
        r"<li\b.*?</li>",
        r"<tr\b.*?</tr>",
        r"<div\b[^>]*class=[\"'][^\"']*(?:card|item|result|listing|org|business|company)[^\"']*[\"'][^>]*>.*?</div>",
    ):
        blocks = re.findall(pattern, html_text, flags=re.I | re.S)
        if len(blocks) >= 1:
            return blocks
    return [html_text]


def extract_from_page(url: str, text: str) -> list[Record]:
    """Extract Records from a page: one per business/card block when the page
    is a directory, else one combined record."""
    blocks = _record_blocks(text)
    out: list[Record] = []
    for b in blocks:
        rec = extract_record_from_block(b)
        if rec:
            rec.source_url = url
            out.append(rec)
    # fallback: if blocks gave us nothing, treat the whole page as one record
    if not out:
        rec = extract_record_from_block(text)
        if rec:
            rec.source_url = url
            out.append(rec)
    return out


def dedupe(records: list[Record]) -> list[Record]:
    seen: set[tuple] = set()
    out: list[Record] = []
    for r in records:
        k = r.key()
        if not any(k):
            continue
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def write_xlsx(records: list[Record], path: str) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "contacts"
    ws.append(["email", "phone", "address", "source_url"])
    for r in records:
        ws.append([r.email, r.phone, r.address, r.source_url])
    # sensible column widths
    for idx, w in enumerate([34, 16, 60, 40], start=1):
        ws.column_dimensions[chr(64 + idx)].width = w
    wb.save(path)


# --------------------------------------------------------------------------- #
# Local demo site (for selftest / demo)
# --------------------------------------------------------------------------- #

def build_sample_site() -> dict[str, str]:
    """A tiny paginated UK business directory (3 pages, some dupes + noise)."""
    rows = [
        # page 1
        ("Baker & Sons", "12 High Street", "Bath", "BA1 2AB",
         "orders@bakerandsons.co.uk", "01225 445 566"),
        ("Coastal Bakery", "4 Harbour Road", "Bristol", "BS1 6QW",
         "hello@coastalbakery.uk", "0117 946 0101"),
        # page 2 (one duplicate of Baker & Sons to test dedupe)
        ("Baker & Sons", "12 High Street", "Bath", "BA1 2AB",
         "orders@bakerandsons.co.uk", "01225 445 566"),
        ("Greenleaf Gardens", "88 Oak Lane", "York", "YO1 7GH",
         "sales@greenleafgardens.com", "01904 555 123"),
        # page 3
        ("Hilltop Deli", "3 King's Street", "Leeds", "LS1 4DD",
         "deli@hilltop.co.uk", "0113 244 8899"),
        ("Ivy Kitchen", "21 Mill Road", "Cardiff", "CF10 2EL",
         "info@ivykitchen.wales", "029 2089 1212"),
    ]

    def page_html(items, pageno, total):
        cards = []
        for name, street, town, pc, email, phone in items:
            cards.append(
                f'<div class="card"><h3>{name}</h3>'
                f'<p>{street}</p><p>{town}</p><p>{pc}</p>'
                f'<p>Email: <a href="mailto:{email}">{email}</a></p>'
                f'<p>Phone: <a href="tel:{phone}">{phone}</a></p></div>')
        nxt = ""
        if pageno < total:
            nxt = f'<a class="next" href="/dir?page={pageno+1}">Next</a>'
        return (
            "<!doctype html><html><body>"
            f'<h1>UK Business Directory - page {pageno}</h1>'
            + "".join(cards) + nxt +
            '<p>Tracking: noscript@tracker.example</p>'  # noise
            "</body></html>"
        )

    p1 = rows[0:2]
    p2 = rows[2:4]
    p3 = rows[4:6]
    return {
        "/dir": page_html(p1, 1, 3),
        "/dir?page=2": page_html(p2, 2, 3),
        "/dir?page=3": page_html(p3, 3, 3),
    }


class LocalFetcher(Fetcher):
    """Serves the sample directory over a real local HTTP server."""

    def __init__(self, routes: dict[str, str], base: str):
        self.routes = routes
        self.base = base
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        routes = self.routes

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def do_GET(self):  # noqa
                path = urlparse(self.path).path
                q = parse_qs(urlparse(self.path).query)
                key = path + (f"?page={q['page'][0]}" if "page" in q else "")
                body = routes.get(key)
                if body is None:
                    # try bare path
                    body = routes.get(path)
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        port = self._server.server_address[1]
        self.base = f"http://127.0.0.1:{port}/dir"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()

    def fetch(self, url: str) -> tuple[str, str]:
        import urllib.request
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.read().decode("utf-8"), resp.geturl()


# --------------------------------------------------------------------------- #
# Self-test / demo
# --------------------------------------------------------------------------- #

def run(selftest_path: str = "out/uk_contacts.xlsx") -> list[Record]:
    routes = build_sample_site()
    fetcher = LocalFetcher(routes, "")
    fetcher.start()
    try:
        pages = crawl(fetcher.base, fetcher, max_pages=10, delay=0.05,
                      on_page=lambda u: print(f"  [crawl] {u}"))
        print(f"  crawled {len(pages)} pages")
        records: list[Record] = []
        for url, text in pages:
            records.extend(extract_from_page(url, text))
        cleaned = dedupe(records)
        # write
        import os
        os.makedirs(os.path.dirname(selftest_path) or ".", exist_ok=True)
        write_xlsx(cleaned, selftest_path)
        print(f"  wrote {selftest_path} with {len(cleaned)} rows")
        return cleaned
    finally:
        fetcher.stop()


def selftest() -> int:
    print("== selftest: local paginated UK directory -> xlsx ==")
    path = "selftest_out/uk_contacts.xlsx"
    records = run(path)
    # checks
    assert len(records) == 5, f"expected 5 unique records, got {len(records)}"
    emails = {r.email for r in records}
    assert "orders@bakerandsons.co.uk" in emails
    assert "hello@coastalbakery.uk" in emails
    assert "deli@hilltop.co.uk" in emails
    # noise email excluded
    assert not any("tracker.example" in r.email for r in records), "noise email leaked"
    # phones normalised to +44
    phones = [r.phone for r in records if r.phone]
    assert all(p.startswith("+44") for p in phones), f"non +44 phones: {phones}"
    # dedupe: baker & sons appears once
    bakers = [r for r in records if r.email == "orders@bakerandsons.co.uk"]
    assert len(bakers) == 1, f"dedupe failed: {len(bakers)} baker rows"
    # addresses present with postcode
    addrs = [r.address for r in records if r.address]
    assert any("12 High Street" in a and "BA1 2AB" in a for a in addrs), "addr missing"
    # read back the xlsx to prove it is a valid workbook
    from openpyxl import load_workbook
    wb = load_workbook(path)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0] == ("email", "phone", "address", "source_url"), f"bad header {rows[0]}"
    assert len(rows) == 1 + 5, f"expected 6 rows (hdr+5), got {len(rows)}"
    print(f"  OK: {len(records)} unique records, {len(phones)} phones all +44, "
          f"dedupe+noise checks pass, xlsx valid (hdr + {len(rows)-1} data rows)")
    print("== selftest PASSED ==")
    return 0


def demo() -> int:
    rc = selftest()
    if rc != 0:
        return rc
    print("\n== demo: extracted rows ==")
    for r in run("demo_out/uk_contacts.xlsx"):
        print(f"  {r.email or '(no email)':36} | {r.phone or '(no phone)':15} | {r.address}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("cmd", choices=["selftest", "demo", "crawl"])
    p.add_argument("url", nargs="?", default=None, help="target base URL for crawl")
    p.add_argument("--max-pages", type=int, default=20)
    p.add_argument("--delay", type=float, default=1.0)
    p.add_argument("--out", default="out/uk_contacts.xlsx")
    args = p.parse_args(argv)
    if args.cmd == "selftest":
        return selftest()
    if args.cmd == "demo":
        return demo()
    if args.cmd == "crawl":
        if not args.url:
            print("crawl: a target URL is required", file=sys.stderr)
            return 2
        fetcher = HttpFetcher(user_agent="ContactScrape/1.0 (polite, one-time)")
        print(f"== crawl {args.url} (max {args.max_pages} pages, delay {args.delay}s) ==")
        pages = crawl(args.url, fetcher, max_pages=args.max_pages, delay=args.delay,
                      on_page=lambda u: print(f"  [crawl] {u}"))
        records: list[Record] = []
        for url, text in pages:
            records.extend(extract_from_page(url, text))
        cleaned = dedupe(records)
        import os
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        write_xlsx(cleaned, args.out)
        print(f"done: {len(pages)} pages -> {len(cleaned)} unique records -> {args.out}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
