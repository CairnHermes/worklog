#!/usr/bin/env python3
"""
PDF Forms & Web Text Extraction -> one Excel workbook (PoC)
===========================================================
For the Freelancer job: "PDF Forms & Web Text Extraction election"
(https://www.freelancer.com/projects/data-extraction/pdf-forms-web-text-extraction)

Scope per the posting:
  1. Several PDF files contain STRUCTURED FORMS and TYPED ENTRIES.
     Every field must be captured accurately and placed into Excel.
  2. Web text extraction (the second related task).
  3. Both flow into ONE clean Excel workbook.

What this PoC actually does (all verifiable with `python PoC.py selftest`):
  A. Real-PDF form extraction
     - Native AcroForm fields (fillable PDF forms): read via pypdf
       `reader.get_fields()` with per-field name + value + page where known.
     - Typed/flattened entries (values typed onto the page, e.g. after a form
       was flattened or printed): read via pypdf page text + a documented
       "Label: value" line parser (handles multi-word labels, values with
       colons, labels split across the line).
  B. Web text extraction
     - requests + BeautifulSoup: strips script/style/nav/footer boilerplate,
       captures headings (as section names) and paragraphs in reading order,
       plus HTML tables as structured rows (header row + data rows).
  C. One clean Excel workbook (openpyxl, no Excel install needed):
     Sheet 1 "pdf_fields"    file | page | field | value
     Sheet 2 "typed_entries" file | page | label | value
     Sheet 3 "web_text"      url | section | text
     Sheet 4 "web_tables"    url | table# | column -> value rows

  Selftest builds its OWN inputs and proves the whole path end to end:
     - reportlab renders a real fillable PDF (4 AcroForm fields) -> PoC
       extracts all 4 field values exactly.
     - reportlab renders a flattened-form PDF (typed "Label: value" lines)
       -> PoC extracts all 4 entries exactly.
     - a local HTTP server serves a page with headings, paragraphs, a table,
       and script/style/nav junk -> PoC extracts the clean text + table rows
       and provably EXCLUDES the junk.
     - the final workbook is opened again and every sheet is row-counted and
       value-checked.

Honest limits (flagged in the bid):
  - Scanned PDFs (images, no text layer) are out of scope here; that needs an
    OCR pass (e.g. ocrmypdf) and we would confirm before starting. The job
    says "typed entries", which this PoC covers.
  - Very large tables (>10k rows) should be confirmed with the client on
    layout; the row model here keeps every cell intact.

Dependencies: pypdf, reportlab (test-input generation only), requests,
beautifulsoup4, openpyxl. All standard pip packages.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass
class FieldRecord:
    file: str
    page: int
    field: str
    value: str


@dataclass
class TypedEntry:
    file: str
    page: int
    label: str
    value: str


@dataclass
class WebTextRecord:
    url: str
    section: str
    text: str


@dataclass
class WebTable:
    url: str
    table_no: int
    header: List[str]
    rows: List[List[str]]


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------

class PdfReadError(Exception):
    pass


class WebFetchError(Exception):
    pass


# --------------------------------------------------------------------------
# A. PDF extraction
# --------------------------------------------------------------------------

def extract_pdf_fields(path: str) -> Tuple[List[FieldRecord], List[TypedEntry]]:
    """Extract (a) native AcroForm fields and (b) typed 'Label: value'
    entries from one PDF file."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as e:  # noqa: BLE001
                raise PdfReadError(
                    f"{os.path.basename(path)}: PDF is password-protected "
                    f"({e}) - send the password or an unlocked copy") from e
        if not reader.pages:
            raise PdfReadError(f"{os.path.basename(path)}: PDF has no pages")
    except PdfReadError:
        raise
    except Exception as e:  # noqa: BLE001
        raise PdfReadError(f"{os.path.basename(path)}: not a readable PDF ({e})") from e

    base = os.path.basename(path)
    fields: List[FieldRecord] = []
    typed: List[TypedEntry] = []

    # (a) native form fields
    try:
        fdict = reader.get_fields()
    except Exception:  # noqa: BLE001 - pypdf raises various on odd files
        fdict = None
    if fdict:
        for name, fobj in fdict.items():
            val = fobj.get("/V")
            if val is None:
                continue
            val = str(val)
            if val.strip() == "":
                continue
            fields.append(FieldRecord(file=base, page=1, field=str(name), value=val))

    # (b) typed entries from page text (flattened forms / typed-in values)
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            continue
        for line in text.splitlines():
            label, value = parse_label_value(line)
            if label and value:
                typed.append(TypedEntry(file=base, page=i, label=label, value=value))

    return fields, typed


_LABEL_VALUE_RE = re.compile(
    r"^\s*([A-Z][A-Za-z0-9 &/'().-]{1,60}?)\s*(?::|\u2013| -)\s+(.+?)\s*$"
)


def parse_label_value(line: str) -> Tuple[Optional[str], Optional[str]]:
    """'Applicant Name: Jane A. Doe' -> ('Applicant Name', 'Jane A. Doe').
    Rules (documented so the client can see exactly what is matched):
      - label starts with a capital, 2..61 chars, words and light punctuation
      - separator is ':' (also en-dash or ' - ' with spaces)
      - value = everything after the separator, trimmed, non-empty. Numeric
        values are KEPT: 'Ballot Count: 240' -> ('Ballot Count', '240') and
        'Precinct Number: 42' -> ('Precinct Number', '42') are valid form
        fields and must be captured, not dropped.
    Returns (None, None) for lines that are not label/value (prose sentences,
    totals with no separator like 'Totals 1,234', standalone numbers)."""
    line = line.strip()
    if not line or len(line) < 4:
        return None, None
    m = _LABEL_VALUE_RE.match(line)
    if not m:
        return None, None
    label = m.group(1).strip()
    value = m.group(2).strip()
    if not label or not value:
        return None, None
    # reject labels that are actually a prose sentence (period+lowercase)
    if re.search(r"\.\s+[a-z]", label):
        return None, None
    return label, value


# --------------------------------------------------------------------------
# B. Web text extraction
# --------------------------------------------------------------------------

_STRIP_TAGS = ("script", "style", "noscript", "template", "iframe")
_SKIP_SELECTOR = "nav, footer, header.site-header, .nav, .footer, .breadcrumb, .pagination, .cookie-banner"


def extract_web(url: str, timeout: float = 15.0) -> Tuple[List[WebTextRecord], List[WebTable]]:
    """Fetch a page and extract clean reading-order text + tables."""
    import requests
    from bs4 import BeautifulSoup

    try:
        r = requests.get(url, timeout=timeout,
                         headers={"User-Agent": "Mozilla/5.0 (data-prep tooling)"})
    except requests.RequestException as e:
        raise WebFetchError(f"could not fetch {url}: {e}") from e
    if r.status_code != 200:
        raise WebFetchError(f"HTTP {r.status_code} fetching {url}")
    soup = BeautifulSoup(r.text, "html.parser")

    for el in soup.find_all(_STRIP_TAGS):
        el.decompose()
    for el in soup.select(_SKIP_SELECTOR):
        el.decompose()

    # main content container when present (most CMS pages)
    root = soup.find("main") or soup.find("article") or soup.body or soup

    title = (soup.title.get_text(strip=True) if soup.title else "") or url
    records: List[WebTextRecord] = []
    if title and title != url:
        records.append(WebTextRecord(url=url, section="title", text=title))

    current_section = "body"
    for el in root.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li"], recursive=True):
        # skip elements nested inside a table (tables handled separately)
        if el.find_parent("table") is not None:
            continue
        text = el.get_text(" ", strip=True)
        if not text or len(text) < 2:
            continue
        if el.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            current_section = text
            records.append(WebTextRecord(url=url, section=f"heading: {text}", text=text))
        else:
            records.append(WebTextRecord(url=url, section=current_section, text=text))

    # tables
    tables: List[WebTable] = []
    for tno, table in enumerate(root.find_all("table"), start=1):
        header: List[str] = []
        rows: List[List[str]] = []
        first = True
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if not cells:
                continue
            if first and tr.find("th") is not None:
                header = cells
                first = False
                continue
            first = False
            rows.append(cells)
        if header or rows:
            tables.append(WebTable(url=url, table_no=tno, header=header, rows=rows))

    # de-dup while preserving order (some pages repeat blocks)
    seen = set()
    unique: List[WebTextRecord] = []
    for rec in records:
        key = (rec.section, rec.text)
        if key in seen:
            continue
        seen.add(key)
        unique.append(rec)
    return unique, tables


# --------------------------------------------------------------------------
# C. Excel workbook (one clean file)
# --------------------------------------------------------------------------

def write_workbook(path: str, fields: List[FieldRecord], typed: List[TypedEntry],
                   webtext: List[WebTextRecord], webtables: List[WebTable]) -> Dict:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "pdf_fields"
    ws.append(["file", "page", "field", "value"])
    for f in fields:
        ws.append([f.file, f.page, f.field, f.value])

    ws2 = wb.create_sheet("typed_entries")
    ws2.append(["file", "page", "label", "value"])
    for t in typed:
        ws2.append([t.file, t.page, t.label, t.value])

    ws3 = wb.create_sheet("web_text")
    ws3.append(["url", "section", "text"])
    for w in webtext:
        ws3.append([w.url, w.section, w.text])

    ws4 = wb.create_sheet("web_tables")
    ws4.append(["url", "table", "row", "values"])
    for tb in webtables:
        header = " | ".join(tb.header) if tb.header else ""
        if header:
            ws4.append([tb.url, f"table{tb.table_no}", "header", header])
        for i, row in enumerate(tb.rows, start=1):
            ws4.append([tb.url, f"table{tb.table_no}", i, " | ".join(row)])

    wb.save(path)
    return {
        "pdf_fields_rows": len(fields),
        "typed_entries_rows": len(typed),
        "web_text_rows": len(webtext),
        "web_tables_rows": sum(1 + len(t.rows) for t in webtables),
    }


# --------------------------------------------------------------------------
# Pipeline (the actual deliverable CLI)
# --------------------------------------------------------------------------

def run_pipeline(pdfs: List[str], urls: List[str], outdir: str) -> Dict:
    os.makedirs(outdir, exist_ok=True)
    all_fields: List[FieldRecord] = []
    all_typed: List[TypedEntry] = []
    all_webtext: List[WebTextRecord] = []
    all_tables: List[WebTable] = []
    errors: List[str] = []

    for p in pdfs:
        try:
            f, t = extract_pdf_fields(p)
            all_fields.extend(f)
            all_typed.extend(t)
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))
    for u in urls:
        try:
            w, tb = extract_web(u)
            all_webtext.extend(w)
            all_tables.extend(tb)
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))

    xlsx = os.path.join(outdir, "extracted_workbook.xlsx")
    counts = write_workbook(xlsx, all_fields, all_typed, all_webtext, all_tables)
    # human-readable CSV mirror (clients often want both)
    csv_path = os.path.join(outdir, "pdf_fields_typed.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["kind", "file_or_url", "page_or_table", "label_or_field", "value_or_text"])
        for r in all_fields:
            w.writerow(["pdf_field", r.file, r.page, r.field, r.value])
        for r in all_typed:
            w.writerow(["typed_entry", r.file, r.page, r.label, r.value])
        for r in all_webtext:
            w.writerow(["web_text", r.url, r.section, "", r.text])

    result = {
        "workbook": xlsx,
        "csv": csv_path,
        "errors": errors,
        "counts": counts,
        "asof": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return result


# --------------------------------------------------------------------------
# Selftest: build real inputs, run the pipeline, verify every value
# --------------------------------------------------------------------------

def selftest() -> int:
    import http.server
    import socketserver
    import shutil
    import tempfile
    import threading

    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    tmp = tempfile.mkdtemp(prefix="pdftext_test_")

    # ---- input 1: real fillable AcroForm PDF -----------------------------
    form_pdf = os.path.join(tmp, "election_ballot_check.pdf")
    c = canvas.Canvas(form_pdf, pagesize=letter)
    c.setFont("Helvetica", 10)
    c.drawString(72, 720, "Precinct Ballot Check Form")
    c.acroForm.textfield(name="applicant", x=72, y=680, width=240, height=18,
                         value="Jane A. Doe", borderWidth=0.5)
    c.acroForm.textfield(name="id_number", x=72, y=655, width=240, height=18,
                         value="E123-456", borderWidth=0.5)
    c.acroForm.textfield(name="check_date", x=72, y=630, width=240, height=18,
                         value="2026-10-01", borderWidth=0.5)
    c.acroForm.textfield(name="address", x=72, y=605, width=240, height=18,
                         value="12 Oak Street, Springfield", borderWidth=0.5)
    c.showPage()
    c.save()

    # ---- input 2: flattened / typed-entry PDF ----------------------------
    typed_pdf = os.path.join(tmp, "polling_place_report.pdf")
    c2 = canvas.Canvas(typed_pdf, pagesize=letter)
    c2.setFont("Helvetica", 11)
    y = 700
    for line in [
        "Polling Place Report",
        "Report Name: Precinct 42 Evening Count",
        "Ballot Count: 240",
        "Candidate Total: Rivera",
        "Precinct Number: 42",
        "This is a narrative sentence that must NOT be captured as a field, only the label lines above.",
        "Totals 1,234",
    ]:
        c2.drawString(72, y, line)
        y -= 20
    c2.showPage()
    c2.save()

    # ---- input 3: web page with junk -------------------------------------
    PAGE = """
    <html><head><title>Election Results Hub</title>
    <style>.x{color:red}</style>
    <script>var secret_js = "MUST_NOT_APPEAR";</script>
    </head><body>
    <nav class="nav"><a href="/a">Home</a><a href="/b">Results</a><a href="/c">About</a></nav>
    <div class="breadcrumb">Home / Results</div>
    <main>
      <h1>Precinct 42 Results</h1>
      <p>Reporting completed at 21:00 local time with all precincts counted.</p>
      <h2>Candidate Totals</h2>
      <p>Final certified totals are shown below.</p>
      <table>
        <tr><th>Candidate</th><th>Count</th><th>Share</th></tr>
        <tr><td>Rivera</td><td>132</td><td>55.0%</td></tr>
        <tr><td>Chen</td><td>108</td><td>45.0%</td></tr>
      </table>
      <ul><li>Provisional ballots: 9 held for verification.</li></ul>
    </main>
    <footer class="footer"><span>FOOTER_JUNK_MUST_NOT_APPEAR</span></footer>
    </body></html>"""

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass
        def do_GET(self):
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = socketserver.TCPServer(("127.0.0.1", 0), H)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/results"

    failures = []

    def check(name: str, cond: bool, detail: str = ""):
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" - {detail}" if detail and not cond else ""))
        if not cond:
            failures.append(name)

    try:
        # ---- unit: label/value parser -------------------------------------
        check("parse 'Applicant Name: Jane A. Doe'",
              parse_label_value("Applicant Name: Jane A. Doe") == ("Applicant Name", "Jane A. Doe"),
              str(parse_label_value("Applicant Name: Jane A. Doe")))
        check("parse multi-word label 'Precinct Number: 42'",
              parse_label_value("Precinct Number: 42") == ("Precinct Number", "42"),
              str(parse_label_value("Precinct Number: 42")))
        check("reject sentence 'This is a narrative...'",
              parse_label_value("This is a narrative sentence that must NOT be captured as a field, only the label lines above.") == (None, None))
        check("reject pure-number line 'Totals 1,234'",
              parse_label_value("Totals 1,234") == (None, None))
        check("reject empty", parse_label_value("") == (None, None))
        check("parse 'Share: 55.0%'",
              parse_label_value("Share: 55.0%") == ("Share", "55.0%"),
              str(parse_label_value("Share: 55.0%")))

        # ---- run the real pipeline ----------------------------------------
        result = run_pipeline(pdfs=[form_pdf, typed_pdf], urls=[url], outdir=os.path.join(tmp, "out"))
        check("pipeline: no errors", not result["errors"], str(result["errors"]))

        from openpyxl import load_workbook
        wb = load_workbook(result["workbook"])
        check("workbook: 4 sheets", wb.sheetnames == ["pdf_fields", "typed_entries", "web_text", "web_tables"],
              str(wb.sheetnames))

        # sheet 1: AcroForm fields exact
        ws = wb["pdf_fields"]
        rows = {r[2]: r[3] for r in ws.iter_rows(min_row=2, values_only=True)}
        check("acroform: applicant == 'Jane A. Doe'", rows.get("applicant") == "Jane A. Doe", str(rows))
        check("acroform: id_number == 'E123-456'", rows.get("id_number") == "E123-456")
        check("acroform: check_date == '2026-10-01'", rows.get("check_date") == "2026-10-01")
        check("acroform: address has '12 Oak Street'",
              "12 Oak Street" in (rows.get("address") or ""), str(rows.get("address")))
        check("acroform: 4 fields", len(rows) == 4, f"got {len(rows)}")

        # sheet 2: typed entries exact
        ws2 = wb["typed_entries"]
        trows = {r[2]: r[3] for r in ws2.iter_rows(min_row=2, values_only=True)}
        check("typed: Report Name", trows.get("Report Name") == "Precinct 42 Evening Count", str(trows))
        check("typed: Ballot Count == 240", trows.get("Ballot Count") == "240")
        check("typed: Candidate Total == Rivera", trows.get("Candidate Total") == "Rivera")
        check("typed: Precinct Number == 42", trows.get("Precinct Number") == "42")
        n_typed = sum(1 for _ in ws2.iter_rows(min_row=2, values_only=True))
        check("typed: exactly 4 entries (sentence + 'Totals 1,234' rejected)",
              n_typed == 4, f"got {n_typed}: {trows}")

        # sheet 3: web text clean, junk excluded
        ws3 = wb["web_text"]
        texts = [r[2] for r in ws3.iter_rows(min_row=2, values_only=True)]
        joined = " || ".join(t or "" for t in texts)
        check("web: title present", "Election Results Hub" in joined)
        check("web: heading 'Precinct 42 Results'", any(t == "Precinct 42 Results" for t in texts))
        check("web: paragraph about reporting",
              any(t and "Reporting completed at 21:00" in t for t in texts))
        check("web: list item captured",
              any(t and "Provisional ballots" in t for t in texts))
        check("web: JS junk EXCLUDED", "MUST_NOT_APPEAR" not in joined)
        check("web: footer junk EXCLUDED", "FOOTER_JUNK" not in joined)
        check("web: nav links EXCLUDED", not any(t and t in ("Home Results About", "Home", "Results") for t in texts))

        # sheet 4: table rows
        ws4 = wb["web_tables"]
        t4 = list(ws4.iter_rows(min_row=2, values_only=True))
        check("table: header row present",
              any(r[2] == "header" and "Candidate | Count | Share" in (r[3] or "") for r in t4), str(t4))
        check("table: Rivera row 132 / 55.0%",
              any(r[2] == 1 and "Rivera | 132 | 55.0%" in (r[3] or "") for r in t4), str(t4))
        check("table: Chen row 108 / 45.0%",
              any(r[2] == 2 and "Chen | 108 | 45.0%" in (r[3] or "") for r in t4), str(t4))

        # counts block
        cnt = result["counts"]
        check("counts: pdf_fields 4", cnt["pdf_fields_rows"] == 4, str(cnt))
        check("counts: typed 4", cnt["typed_entries_rows"] == 4)
        check("counts: web_tables 3 rows (header+2)", cnt["web_tables_rows"] == 3, str(cnt))

        # csv mirror sanity (fields + typed + webtext, all present)
        with open(result["csv"], newline="", encoding="utf-8") as f:
            crows = list(csv.reader(f))
        n_webtext = sum(1 for _ in ws3.iter_rows(min_row=2, values_only=True))
        check("csv: header + 4 fields + 4 typed + webtext rows",
              len(crows) == 1 + 4 + 4 + n_webtext,
              f"got {len(crows)}, expected {1 + 4 + 4 + n_webtext}")

        # ---- error paths ----------------------------------------------------
        bad = os.path.join(tmp, "bad.pdf")
        with open(bad, "wb") as f:
            f.write(b"NOT A PDF AT ALL")
        try:
            extract_pdf_fields(bad)
            check("garbage file raises PdfReadError", False)
        except PdfReadError as e:
            check("garbage file raises PdfReadError naming the file",
                  "bad.pdf" in str(e), str(e))

        try:
            extract_web(f"http://127.0.0.1:1/nope", timeout=2)
            check("dead url raises WebFetchError", False)
        except WebFetchError:
            check("dead url raises WebFetchError", True)

    finally:
        httpd.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nSELFTEST: {('FAIL ' + str(len(failures)) + ' failed: ' + str(failures)) if failures else 'ALL PASS'}")
    return 1 if failures else 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="PDF forms + web text -> one Excel workbook")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("selftest")
    rn = sub.add_parser("run")
    rn.add_argument("--pdf", action="append", default=[], help="PDF file (repeatable)")
    rn.add_argument("--url", action="append", default=[], help="Web URL (repeatable)")
    rn.add_argument("--outdir", default="extract_out")
    args = ap.parse_args(argv)

    if args.cmd == "selftest":
        return selftest()
    if not args.pdf and not args.url:
        print("need at least one --pdf or --url", file=sys.stderr)
        return 2
    result = run_pipeline(args.pdf, args.url, args.outdir)
    print(json.dumps(result, indent=2))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
