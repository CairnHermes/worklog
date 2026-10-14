#!/usr/bin/env python3
"""
Google SERP URL collector.

A compact, dependency-free Python script that collects the organic result
URLs for a list of keywords across the first N result pages and writes them
to an Excel workbook (one sheet per keyword, plus a flat "All" sheet) and a
CSV fallback.

Design
------
* Zero third-party dependencies. The .xlsx is written with the standard
  library only (zipfile + hand-built OOXML with inline strings), so the
  script runs anywhere CPython runs.
* Provider-abstracted. The parsing/filtering path is shared by every
  provider, so verifying the deterministic mock verifies the exact logic the
  live Google provider uses.
* The default live provider is the OFFICIAL Google Custom Search JSON API
  (the ToS-clean way to pull Google results). We deliberately do NOT scrape
  google.com HTML: that is a ToS gray area and bot-walled from datacenter
  IPs, so a "working" scrape built that way would not be an honest
  deliverable.
* Clean failures. Missing API credentials raise a single, readable
  SerpError with the exact config needed, instead of a traceback.

Usage
-----
  # Self-contained, zero-network, zero-credential demo (mock provider):
  python PoC.py "best espresso machine" "kubernetes helm" \
      --provider mock --pages 5 --out demo

  # Live via the official Google Custom Search JSON API:
  export GOOGLE_CSE_KEY=your_api_key
  export GOOGLE_CSE_CX=your_search_engine_id
  python PoC.py "best espresso machine" --provider google --pages 5 --out demo

  # Run the assertion suite (offline, deterministic):
  python PoC.py selftest
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import random
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from xml.sax.saxutils import escape as _xml_escape


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class SerpError(RuntimeError):
    """A single, readable failure with the exact fix (no traceback)."""


# ---------------------------------------------------------------------------
# Row model
# ---------------------------------------------------------------------------
@dataclass
class Row:
    keyword: str
    position: int      # 1-based within the keyword, after ads/dups removed
    page: int          # the page the result appeared on
    url: str

    def as_list(self):
        return [self.keyword, str(self.position), str(self.page), self.url]


HEADER = ["keyword", "position", "page", "url"]


# ---------------------------------------------------------------------------
# Shared parsing/filtering path (used by BOTH the live and mock providers)
# ---------------------------------------------------------------------------
# Known ad/affil host fragments that must never leak into "organic" results
# even if a provider mislabels them.
_AD_HOST_HINTS = (
    "googleads.", "doubleclick.net", "googleadservices.com",
    "googlesyndication.com", "ads.", "adservice.", "google.com/ads",
)


def _is_ad_url(url: str) -> bool:
    host = (urllib.parse.urlparse(url).netloc or "").lower()
    return any(h in host for h in _AD_HOST_HINTS)


def parse_cse_response(payload: dict):
    """Split a Google Custom Search JSON payload into (organic, ads).

    `organic`  -> list of {"title": str, "url": str}
    `ads`      -> list of {"title": str, "url": str}

    This is the ONLY place ads / related-search / snippet items are filtered
    out, and it is shared by the live and mock providers.
    """
    organic, ads = [], []
    # Dedicated ads array (present on some CSE responses / the ads API).
    for a in payload.get("ads", []) or []:
        if a.get("link"):
            ads.append({"title": a.get("title", ""), "url": a["link"]})
    # Organic items.
    for it in payload.get("items", []) or []:
        link = it.get("link")
        if not link:
            continue
        if it.get("adDetail") or _is_ad_url(link):
            ads.append({"title": it.get("title", ""), "url": link})
            continue
        organic.append({"title": it.get("title", ""), "url": link})
    # relatedSearches / featured-snippet style entries are deliberately NOT
    # consumed at all (they are not `items`).
    return organic, ads


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------
class SerpProvider:
    """Returns the raw CSE-shaped JSON payload for one keyword + page."""

    name = "base"

    def fetch(self, keyword: str, page: int) -> dict:  # pragma: no cover - interface
        raise NotImplementedError


class GoogleCSEProvider(SerpProvider):
    """Live provider: official Google Custom Search JSON API (ToS-clean)."""

    name = "google"
    ENDPOINT = "https://www.googleapis.com/customsearch/v1"
    PER_PAGE = 10  # CSE cap for start offset math

    def __init__(self):
        self.key = os.environ.get("GOOGLE_CSE_KEY", "").strip()
        self.cx = os.environ.get("GOOGLE_CSE_CX", "").strip()
        if not self.key or not self.cx:
            raise SerpError(
                "Google Custom Search needs two env vars: "
                "GOOGLE_CSE_KEY (API key) and GOOGLE_CSE_CX (search engine id). "
                "Create a free one at console.cloud.google.com -> Custom Search "
                "JSON API. For a zero-credential demo, use --provider mock."
            )

    def fetch(self, keyword: str, page: int) -> dict:
        start = (page - 1) * self.PER_PAGE + 1
        qs = urllib.parse.urlencode({
            "key": self.key, "cx": self.cx, "q": keyword,
            "start": start, "num": self.PER_PAGE,
        })
        url = f"{self.ENDPOINT}?{qs}"
        try:
            with urllib.request.urlopen(url, timeout=25) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            raise SerpError(
                f"Google Custom Search returned HTTP {e.code} for "
                f"q={keyword!r} page={page}: {body}"
            )
        except urllib.error.URLError as e:
            raise SerpError(f"Network error calling Google Custom Search: {e.reason}")


class MockCSEProvider(SerpProvider):
    """Deterministic offline provider.

    Returns the exact same payload shape as the live API (items, ads,
    relatedSearches, searchInformation) so it exercises the identical
    parse/filter path. Two fixed keywords have a hand-built index with known
    ad / duplicate / page counts for crisp assertions; any other keyword gets
    a deterministic seeded index (so any input runs, reproducibly).
    """

    name = "mock"

    # Fixed, hand-built index for "best espresso machine":
    #   22 unique organic URLs; one cross-page duplicate (U1 on page 2);
    #   4 ads across pages; 2 relatedSearches per page.
    _FIXED = {
        "best espresso machine": {
            "unique": [f"https://site{i}.example/organic/{i}" for i in range(1, 23)],
            "pages": {
                1: {"items": range(1, 11), "ads": 2, "dup": []},
                2: {"items": range(11, 20), "ads": 1, "dup": [1]},   # dup of U1
                3: {"items": range(20, 23), "ads": 1, "dup": []},
            },
        }
    }

    def fetch(self, keyword: str, page: int) -> dict:
        key = keyword.lower().strip()
        if key in self._FIXED:
            return self._fetch_fixed(key, page)
        return self._fetch_seeded(keyword, page)

    # -- fixed index -------------------------------------------------------
    def _fetch_fixed(self, key: str, page: int) -> dict:
        spec = self._FIXED[key]
        if page not in spec["pages"]:
            # Empty page -> caller stops paging.
            return {"items": [], "ads": [], "relatedSearches": [],
                    "searchInformation": {"totalResults": 22}}
        p = spec["pages"][page]
        items = []
        for n in list(p["dup"]) + list(p["items"]):
            items.append({"title": f"Result {n}", "link": spec["unique"][n - 1]})
        ads = [{"title": f"Ad {page}.{i}",
                "link": f"https://googleads.example/{page}/{i}"}
               for i in range(1, p["ads"] + 1)]
        return {
            "items": items,
            "ads": ads,
            "relatedSearches": [f"related {page}.{i}" for i in range(1, 3)],
            "searchInformation": {"totalResults": 22},
        }

    # -- seeded index (any keyword) ---------------------------------------
    def _fetch_seeded(self, keyword: str, page: int) -> dict:
        seed = int(hashlib.sha1(keyword.lower().encode()).hexdigest(), 16)
        rng = random.Random(seed)
        domains = [f"vendor{d}.com" for d in rng.sample(range(1, 999), 12)]
        total_pages = 4
        per_page = 6
        total = total_pages * per_page
        if page > total_pages:
            return {"items": [], "ads": [], "relatedSearches": [],
                    "searchInformation": {"totalResults": total}}
        base = (page - 1) * per_page
        items = []
        for k in range(per_page):
            d = domains[(base + k) % len(domains)]
            items.append({
                "title": f"{keyword} on {d}",
                "link": f"https://{d}/page/{page}/item/{k}",
            })
        ads = [{"title": f"Ad {page}", "link": f"https://ads.example/{page}"}]
        return {
            "items": items, "ads": ads,
            "relatedSearches": [f"related {page}"],
            "searchInformation": {"totalResults": total},
        }


# ---------------------------------------------------------------------------
# Collector (shared pipeline)
# ---------------------------------------------------------------------------
def collect(keyword: str, provider: SerpProvider, max_pages: int):
    """Collect de-duplicated organic URLs for one keyword.

    Returns (rows, fetch_count) where rows are Row objects in position order.
    Stops paging as soon as a page returns zero new organic results.
    """
    rows: list[Row] = []
    seen: set[str] = set()
    fetches = 0
    for page in range(1, max_pages + 1):
        payload = provider.fetch(keyword, page)
        fetches += 1
        organic, _ads = parse_cse_response(payload)
        new_this_page = 0
        for it in organic:
            url = it["url"].rstrip("/")
            if url in seen:
                continue
            seen.add(url)
            rows.append(Row(keyword=keyword, position=len(rows) + 1,
                            page=page, url=url))
            new_this_page += 1
        if new_this_page == 0:
            break  # empty page -> stop paging
    return rows, fetches


def collect_all(keywords, provider, max_pages):
    """Collect for every keyword; returns {keyword: [Row...]} preserving order."""
    out = {}
    for kw in keywords:
        rows, _ = collect(kw, provider, max_pages)
        out[kw] = rows
    return out


# ---------------------------------------------------------------------------
# Export: minimal .xlsx (stdlib only) + CSV fallback
# ---------------------------------------------------------------------------
def _col_letter(idx: int) -> str:
    """0-based column index -> Excel column letter (A, B, ... Z, AA, ...)."""
    s = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        s = chr(65 + rem) + s
    return s


def _sheet_name(keyword: str, used: set[str]) -> str:
    """Sanitize a keyword into a valid, unique Excel sheet name (<=31 chars)."""
    name = re.sub(r'[:\\/?*\[\]]', " ", keyword)
    name = re.sub(r"\s+", " ", name).strip() or "Sheet"
    name = name[:31]
    base, i = name, 2
    while name in used:
        suffix = f" ({i})"
        name = base[: 31 - len(suffix)] + suffix
        i += 1
    used.add(name)
    return name


def _cell(ref: str, value) -> str:
    """One <c> element using an inline string (no sharedStrings needed)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"><v>{value}</v></c>'
    text = _xml_escape(str(value), {'"': "&quot;"})
    text = text.replace("\n", " ").replace("\r", " ")
    text = "".join(ch for ch in text if ord(ch) >= 32 or ch in "\t")
    return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'


def _worksheet_xml(grid) -> str:
    rows_xml = []
    for r_i, row in enumerate(grid, start=1):
        cells = "".join(_cell(f"{_col_letter(c)}{r_i}", v)
                        for c, v in enumerate(row))
        rows_xml.append(f'<row r="{r_i}">{cells}</row>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{"".join(rows_xml)}</sheetData></worksheet>')


def write_xlsx(path: str, sheets) -> None:
    """Write a multi-sheet .xlsx. `sheets` = list of (sheet_name, grid).

    `grid` is a list of rows; each row is a list of cell values (str/int).
    Uses inline strings so no sharedStrings part is required.
    """
    n = len(sheets)
    used: set[str] = set()
    # Enforce uniqueness of sanitized names.
    named = []
    for name, grid in sheets:
        named.append((_sheet_name(name, used), grid))

    content_types = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>']
    for i in range(1, n + 1):
        content_types.append(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
    content_types.append('</Types>')

    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" '
                 'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                 'Target="xl/workbook.xml"/></Relationships>')

    sheet_tags, wb_rels = [], []
    for i, (name, _grid) in enumerate(named, start=1):
        sheet_tags.append(f'<sheet name="{_xml_escape(name)}" sheetId="{i}" r:id="rId{i}"/>')
        wb_rels.append(
            f'<Relationship Id="rId{i}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{i}.xml"/>')
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                f'<sheets>{"".join(sheet_tags)}</sheets></workbook>')
    wb_rels_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   f'{"".join(wb_rels)}</Relationships>')

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "".join(content_types))
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels_xml)
        for i, (_name, grid) in enumerate(named, start=1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", _worksheet_xml(grid))


def write_csv(path: str, rows) -> None:
    with io.open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for r in rows:
            w.writerow(r.as_list())


# ---------------------------------------------------------------------------
# Self-test (offline, deterministic)
# ---------------------------------------------------------------------------
def _read_sheet_text(path: str, sheet: str) -> list[list[str]]:
    """Parse one worksheet back out of the xlsx and return cell text rows."""
    import xml.etree.ElementTree as ET
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(path) as z:
        wb = z.read("xl/workbook.xml").decode("utf-8")
        # find the sheet index for the name
        tree = ET.fromstring(wb)
        r_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
        target = None
        for i, sh in enumerate(tree.iter(ns + "sheet"), start=1):
            if sh.get("name") == sheet:
                target = i
                break
        if target is None:
            raise SerpError(f"sheet {sheet!r} not found in workbook")
        sheet_xml = z.read(f"xl/worksheets/sheet{target}.xml").decode("utf-8")
    st = ET.fromstring(sheet_xml)
    out = []
    for row in st.iter(ns + "row"):
        cells = []
        for c in row.iter(ns + "c"):
            # inline string: <c t="inlineStr"><is><t>..</t></is></c>
            # numeric:       <c><v>..</v></c>
            t = c.find(f".//{ns}t")
            v = c.find(f".//{ns}v")
            cells.append(t.text if t is not None else (v.text if v is not None else ""))
        out.append(cells)
    return out


def selftest() -> int:
    failures: list[str] = []

    def check(cond: bool, msg: str):
        if cond:
            print(f"  ok   - {msg}")
        else:
            failures.append(msg)
            print(f"  FAIL - {msg}")

    import tempfile
    prov = MockCSEProvider()
    kw = "best espresso machine"

    # -- pipeline: counts, ads excluded, dedupe, stop-early --------------
    rows, fetches = collect(kw, prov, max_pages=5)
    urls = [r.url for r in rows]
    check(len(rows) == 22, f"22 unique organic rows (got {len(rows)})")
    check(fetches == 4, f"paging stopped at empty page 4 (fetches={fetches})")
    check(all("googleads.example" not in u for u in urls),
          "no ad URLs leaked into organic results")
    # page distribution: 10 on p1, 9 new on p2 (1 dup dropped), 3 on p3
    from collections import Counter
    pagedist = Counter(r.page for r in rows)
    check(pagedist[1] == 10 and pagedist[2] == 9 and pagedist[3] == 3,
          f"page distribution 10/9/3 (got {dict(pagedist)})")
    check(len(urls) == len(set(urls)), "no duplicate URLs in result")
    check(all(r.position == i + 1 for i, r in enumerate(rows)),
          "positions are 1..N with no gaps")

    # -- xlsx export ------------------------------------------------------
    tmp = tempfile.mkdtemp(prefix="serp_poc_")
    xlsx = os.path.join(tmp, "results.xlsx")
    flat = [r for r in rows]
    sheets = [(kw, [HEADER] + [r.as_list() for r in rows])]
    sheets.append(("All", [HEADER] + [r.as_list() for r in flat]))
    write_xlsx(xlsx, sheets)
    check(os.path.getsize(xlsx) > 0, "xlsx file written (non-empty)")

    with zipfile.ZipFile(xlsx) as z:
        names = z.namelist()
        check("[Content_Types].xml" in names, "content-types part present")
        check("xl/workbook.xml" in names, "workbook part present")
        check("xl/worksheets/sheet1.xml" in names, "sheet1 present")
        check("xl/worksheets/sheet2.xml" in names, "sheet2 present")

    kw_sheet = _sheet_name(kw, set())
    grid = _read_sheet_text(xlsx, kw_sheet)
    check(grid[0] == HEADER, f"header row correct ({grid[0]})")
    body = grid[1:]
    check(len(body) == 22, f"sheet body has 22 data rows (got {len(body)})")
    check(body[0][0] == kw and body[0][1] == "1" and body[0][2] == "1",
          "first data row: keyword/position/page correct")
    check(body[0][3].startswith("https://"), "first data row url is absolute")
    all_urls = [c[3] for c in body]
    check(all("googleads.example" not in u for u in all_urls),
          "xlsx body contains no ad URLs")

    # -- csv fallback -----------------------------------------------------
    csvp = os.path.join(tmp, "results.csv")
    write_csv(csvp, flat)
    with io.open(csvp, encoding="utf-8") as f:
        lines = f.read().splitlines()
    check(lines[0] == "keyword,position,page,url", "csv header correct")
    check(len(lines) - 1 == 22, f"csv has 22 data rows (got {len(lines) - 1})")

    # -- live provider: clean failure with no credentials ----------------
    os.environ.pop("GOOGLE_CSE_KEY", None)
    os.environ.pop("GOOGLE_CSE_CX", None)
    try:
        GoogleCSEProvider()
        check(False, "GoogleCSEProvider should raise without credentials")
    except SerpError as e:
        check("GOOGLE_CSE_KEY" in str(e),
              "no-cred live provider raises a readable, actionable SerpError")

    # -- seeded provider (arbitrary keyword) runs deterministically -------
    a = collect("kubernetes helm chart", MockCSEProvider(), 5)
    b = collect("kubernetes helm chart", MockCSEProvider(), 5)
    check(len(a[0]) > 0 and len(a[0]) == len(b[0]),
          f"seeded provider returns results, deterministic ({len(a[0])} rows)")
    check(all("ads.example" not in r.url for r in a[0]),
          "seeded provider ads excluded")

    print()
    if failures:
        print(f"selftest FAILED: {len(failures)} check(s) failed")
        return 1
    print("all checks passed")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _keywords_from_args(args):
    """Keywords from positional args and/or a --keywords file (one per line)."""
    kws = list(args.positional)
    if args.keywords_file:
        with io.open(args.keywords_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    kws.append(line)
    # de-dupe, preserve order
    seen, out = set(), []
    for k in kws:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _make_parser():
    import argparse
    p = argparse.ArgumentParser(
        description="Collect organic Google SERP URLs per keyword to xlsx + csv.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("positional", nargs="*",
                   help="one or more keywords (or use --keywords FILE)")
    p.add_argument("--keywords", dest="keywords_file",
                   help="text file with one keyword per line")
    p.add_argument("--pages", type=int, default=5,
                   help="max result pages per keyword (default 5)")
    p.add_argument("--provider", choices=["google", "mock"], default="google",
                   help="google = live Custom Search API; mock = offline demo")
    p.add_argument("--out", default="serp_out",
                   help="output directory (default serp_out)")
    return p


def run(args) -> int:
    kws = _keywords_from_args(args)
    if not kws:
        print("No keywords given. Pass keywords as arguments or via "
              "--keywords FILE. Try: python PoC.py 'best espresso machine' "
              "--provider mock --pages 5 --out demo")
        return 2
    if args.pages < 1:
        print("--pages must be >= 1")
        return 2

    try:
        provider = (MockCSEProvider() if args.provider == "mock"
                    else GoogleCSEProvider())
    except SerpError as e:
        print(f"ERROR: {e}")
        return 3

    data = collect_all(kws, provider, args.pages)
    os.makedirs(args.out, exist_ok=True)
    xlsx_path = os.path.join(args.out, "serp_urls.xlsx")
    csv_path = os.path.join(args.out, "serp_urls.csv")

    flat = [r for rows in data.values() for r in rows]
    sheets = [(kw, [HEADER] + [r.as_list() for r in data[kw]]) for kw in kws]
    sheets.append(("All", [HEADER] + [r.as_list() for r in flat]))
    write_xlsx(xlsx_path, sheets)
    write_csv(csv_path, flat)

    total_ads_note = "(ads/related/snippets excluded by design)"
    print(f"provider   : {provider.name}")
    print(f"keywords   : {len(kws)}")
    print(f"total urls : {len(flat)} {total_ads_note}")
    for kw in kws:
        print(f"  - {kw!r}: {len(data[kw])} organic urls across "
              f"{max((r.page for r in data[kw]), default=0)} page(s)")
    print(f"xlsx       : {xlsx_path}")
    print(f"csv        : {csv_path}")
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "selftest":
        return selftest()
    args = _make_parser().parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
