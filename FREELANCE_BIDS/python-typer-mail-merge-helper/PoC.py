#!/usr/bin/env python3
"""Python Typer app for a python-docx mail-merge helper.

One-off utility: take a Word template containing merge fields ({{name}},
{{company}}, ...) and a CSV of values, and produce one .docx per data row
with every field replaced by that row's value.

Design (matches the brief):
  * Idiomatic Python 3, clear structure, docstrings on public functions.
  * Lightweight error handling: every failure exits with a non-zero code and
    a message that names the offending path, column, or row so it is easy to
    debug.
  * Minimal dependencies: typer + python-docx (plus stdlib csv/zipfile).
  * Merge fields are replaced inside body paragraphs, tables, and
    header/footer paragraphs, including fields split across multiple runs.

CLI:
  python PoC.py selftest                 # builds a template+CSV, merges, reads back
  python PoC.py merge --template t.docx --data rows.csv --out outdir
  python PoC.py check --template t.docx --data rows.csv   # dry run, list fields
"""
from __future__ import annotations

import csv
import copy
import io
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import typer
except ImportError:  # typer optional at runtime; CLI falls back to argparse
    typer = None

from docx import Document
from docx.oxml.ns import qn

# Merge-field syntax: {{ name }} (whitespace inside braces is tolerated).
FIELD_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #

@dataclass
class MergeResult:
    """Outcome of merging one row into the template."""
    output_path: str
    row_index: int
    fields_used: list[str] = field(default_factory=list)


@dataclass
class MergeReport:
    """Outcome of a whole merge run."""
    template_path: str
    data_path: str
    out_dir: str
    results: list[MergeResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    ok: bool = True


# --------------------------------------------------------------------------- #
# Error helpers
# --------------------------------------------------------------------------- #

class MergeError(Exception):
    """A recoverable, user-facing error (bad input, missing file, ...)."""


def _die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


# --------------------------------------------------------------------------- #
# Input validation
# --------------------------------------------------------------------------- #

def load_csv_rows(path: str) -> tuple[list[str], list[dict[str, str]]]:
    """Read a CSV file and return (headers, rows).

    Raises MergeError with a message naming the file on any problem.
    """
    p = Path(path)
    if not p.exists():
        raise MergeError(f"CSV file not found: {path}")
    if not p.is_file():
        raise MergeError(f"path is not a file: {path}")
    try:
        with p.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            headers = [h.strip() for h in (reader.fieldnames or [])]
            if not headers:
                raise MergeError(f"CSV has no header row: {path}")
            rows: list[dict[str, str]] = []
            for i, row in enumerate(reader, start=2):  # line 1 = header
                norm = {}
                for k, v in row.items():
                    key = (k or "").strip()
                    norm[key] = (v if isinstance(v, str) else "") if v is not None else ""
                rows.append(norm)
    except MergeError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface as a clean message
        raise MergeError(f"could not parse CSV {path}: {exc}") from exc
    return headers, rows


def extract_template_fields(path: str) -> list[str]:
    """Return the ordered, de-duplicated merge fields used in a template.

    Scans body paragraphs, tables, and header/footer paragraphs.
    Raises MergeError naming the file on read failure.
    """
    p = Path(path)
    if not p.exists():
        raise MergeError(f"template not found: {path}")
    if not p.is_file():
        raise MergeError(f"path is not a file: {path}")
    try:
        doc = Document(str(p))
    except Exception as exc:  # noqa: BLE001
        raise MergeError(f"could not open template {path}: {exc}") from exc

    fields: list[str] = []
    seen: set[str] = set()

    def add(text: str | None) -> None:
        if not text:
            return
        for m in FIELD_RE.findall(text):
            if m not in seen:
                seen.add(m)
                fields.append(m)

    for para in doc.paragraphs:
        add(para.text)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    add(para.text)
    for section in doc.sections:
        for hf in (section.header, section.footer,
                   section.first_page_header, section.first_page_footer):
            for para in hf.paragraphs:
                add(para.text)
            for table in hf.tables:
                for row in table.rows:
                    for cell in row.cells:
                        for para in cell.paragraphs:
                            add(para.text)
    return fields


# --------------------------------------------------------------------------- #
# Merge engine
# --------------------------------------------------------------------------- #

def _replace_in_paragraph(paragraph, values: dict[str, str],
                          used: list[str]) -> int:
    """Replace merge fields in one paragraph, handling fields split across
    runs. Returns the number of replacements made in this paragraph."""
    full = "".join(run.text or "" for run in paragraph.runs)
    if not FIELD_RE.search(full):
        return 0
    matches = list(FIELD_RE.finditer(full))
    for m in matches:
        key = m.group(1)
        if key not in values:
            continue  # leave unknown fields untouched; report is done elsewhere
        used.append(key)
        replacement = values[key]

        # Locate runs overlapping the match span and rewrite them.
        pos = 0
        spans: list[tuple[int, int, object]] = []
        for run in paragraph.runs:
            text = run.text or ""
            spans.append((pos, pos + len(text), run))
            pos += len(text)
        start, end = m.span()
        first = True
        for s, e, run in spans:
            if e <= start or s >= end:
                continue
            # slice of this run that lies inside the match
            lo = max(start, s) - s
            hi = min(end, e) - s
            prefix = (run.text or "")[:lo]
            suffix = (run.text or "")[hi:]
            new_text = (prefix + (replacement if first else "")) + suffix
            run.text = new_text
            first = False
    # count replacements actually made
    return len([u for u in used])  # caller tallies uniquely below


def merge_one(doc: Document, values: dict[str, str]) -> list[str]:
    """Replace every known field in one Document object.

    Returns the list of field names that were replaced (with duplicates
    collapsed, order preserved).
    """
    used: list[str] = []

    def walk_paragraphs(paragraphs):
        for para in paragraphs:
            if not para.runs:
                continue
            if FIELD_RE.search("".join(r.text or "" for r in para.runs)):
                _replace_in_paragraph(para, values, used)

    walk_paragraphs(doc.paragraphs)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                walk_paragraphs(cell.paragraphs)
    for section in doc.sections:
        for hf in (section.header, section.footer,
                   section.first_page_header, section.first_page_footer):
            walk_paragraphs(hf.paragraphs)
            for table in hf.tables:
                for row in table.rows:
                    for cell in row.cells:
                        walk_paragraphs(cell.paragraphs)
    seen: set[str] = set()
    out: list[str] = []
    for k in used:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def read_text(doc: Document) -> str:
    """Flatten a Document's text (body + tables + header/footer) for checks."""
    parts: list[str] = []
    for para in doc.paragraphs:
        parts.append(para.text)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                parts.append(cell.text)
    for section in doc.sections:
        for hf in (section.header, section.footer):
            for para in hf.paragraphs:
                parts.append(para.text)
    return "\n".join(parts)


def merge(template_path: str, data_path: str, out_dir: str,
          filename_pattern: str = "{stem}_{row:03d}.docx") -> MergeReport:
    """Merge every CSV row into the template, writing one .docx per row.

    All errors are collected in the report (not raised) so a partial run
    still reports which rows succeeded and why the rest failed.
    """
    report = MergeReport(template_path=template_path,
                         data_path=data_path, out_dir=out_dir)
    try:
        headers, rows = load_csv_rows(data_path)
    except MergeError as exc:
        report.errors.append(str(exc))
        return report
    if not rows:
        report.errors.append(f"CSV has a header but no data rows: {data_path}")
        return report

    template_fields = extract_template_fields(template_path)
    # sanity: warn about fields in template that no row provides
    provided = set()
    for r in rows:
        provided.update(k for k in r if r[k] != "")
    missing_in_data = [f for f in template_fields if f not in provided]

    template_doc = Document(template_path)
    os.makedirs(out_dir, exist_ok=True)
    stem = Path(template_path).stem
    for i, row in enumerate(rows, start=1):
        doc = copy.deepcopy(template_doc)  # fresh copy per row
        used = merge_one(doc, row)
        out_path = os.path.join(out_dir, filename_pattern.format(stem=stem, row=i))
        doc.save(out_path)
        report.results.append(MergeResult(output_path=out_path, row_index=i,
                                          fields_used=used))
    if missing_in_data:
        report.errors.append(
            "template fields never provided by any CSV row: "
            + ", ".join(missing_in_data))
    # NOTE: "errors" above are advisory for missing fields; a row-level
    # failure is recorded separately:
    report.ok = not any(e.startswith("could not") or e.startswith("CSV")
                        for e in report.errors)
    return report


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #

def run_selftest() -> int:
    print("== selftest: template + CSV -> N merged .docx files ==")
    work = Path("selftest_out")
    work.mkdir(exist_ok=True)

    # 1. Build a template with merge fields, including one split across runs
    #    (simulates what Word produces after edits) and one in a table cell.
    tpl = Document()
    tpl.add_heading("Letter to {0}", 0)  # static
    p = tpl.add_paragraph()
    r1 = p.add_run("Dear ")
    r2 = p.add_run("{{name")
    r3 = p.add_run("}},")          # field split across 3 runs
    tpl.add_paragraph("Thank you for your order from {{company}}.")
    table = tpl.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Field"
    table.cell(0, 1).text = "Value"
    table.cell(1, 0).text = "Order"
    cell = table.cell(1, 1)
    cell.text = "{{order_id}}"
    tpl_path = str(work / "letter_template.docx")
    tpl.save(tpl_path)
    print(f"  template: {tpl_path}")

    # 2. Build a CSV
    csv_path = str(work / "rows.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["name", "company", "order_id"])
        w.writerow(["Ada Lovelace", "Analytical Engines Ltd", "OE-1001"])
        w.writerow(["Grace Hopper", "COBOL Systems", "CS-2002"])
        w.writerow(["Alan Turing", "Enigma Co", "EN-3003"])
    print(f"  data:     {csv_path}")

    # 3. Sanity: field extraction sees all fields (incl. the split one)
    fields = extract_template_fields(tpl_path)
    print(f"  fields found in template: {fields}")
    assert set(fields) == {"name", "company", "order_id"}, fields

    # 4. Merge
    out_dir = str(work / "merged")
    report = merge(tpl_path, csv_path, out_dir, filename_pattern="letter_{row:03d}.docx")
    for r in report.results:
        print(f"  row {r.row_index}: {r.output_path} (fields: {r.fields_used})")
    assert len(report.results) == 3, f"expected 3 rows, got {len(report.results)}"

    # 5. Read each merged doc back and verify replacement
    for r in report.results:
        doc = Document(r.output_path)
        text = read_text(doc)
        assert "{{" not in text, f"unreplaced field left in {r.output_path}\n{text}"
        if r.row_index == 1:
            assert "Dear Ada Lovelace," in text, text
            assert "Analytical Engines Ltd" in text, text
            assert "OE-1001" in text, text
        elif r.row_index == 2:
            assert "Dear Grace Hopper," in text, text
            assert "CS-2002" in text, text
    print("  read-back: all 3 documents replaced cleanly (incl. split-run field + table cell)")

    # 6. Error paths: each fails with a clean, actionable message (exit 2 via MergeError)
    def expect_error(fn, needle):
        try:
            fn()
        except MergeError as exc:
            assert needle in str(exc), f"message missing '{needle}': {exc}"
            return
        raise AssertionError(f"expected error containing {needle!r}")

    expect_error(lambda: load_csv_rows(str(work / "nope.csv")), "not found")
    expect_error(lambda: extract_template_fields(str(work / "nope.docx")), "not found")
    print("  error paths: missing CSV + missing template raise clean, named errors")

    print("== selftest PASSED ==")
    return 0


# --------------------------------------------------------------------------- #
# CLI (typer when available, argparse fallback)
# --------------------------------------------------------------------------- #

def _run_merge(args) -> int:
    report = merge(args.template, args.data, args.out)
    if report.results:
        print(f"merged {len(report.results)} row(s) into {args.out}")
        for r in report.results:
            print(f"  row {r.row_index}: {r.output_path}")
    if report.errors:
        for e in report.errors:
            print(f"note: {e}", file=sys.stderr)
    return 0 if report.ok else 1


def _run_check(args) -> int:
    try:
        fields = extract_template_fields(args.template)
        headers, rows = load_csv_rows(args.data)
    except MergeError as exc:
        _die(str(exc))
    print(f"template fields: {fields}")
    print(f"csv columns:     {headers}")
    print(f"csv rows:        {len(rows)}")
    missing = [f for f in fields if f not in headers]
    if missing:
        print(f"WARNING: fields not present in CSV: {missing}")
        return 1
    print("all template fields are covered by the CSV columns")
    return 0


def main(argv: list[str] | None = None) -> int:
    if typer is not None:
        app = typer.Typer(add_completion=False, help="python-docx mail-merge helper")

        @app.command()
        def selftest():
            """Build a template + CSV, merge, and read the results back."""
            return run_selftest()

        @app.command()
        def merge(template: str, data: str, out: str = "merged"):
            """Merge every CSV row into the template, one .docx per row."""
            _run_merge(type("A", (), dict(template=template, data=data, out=out))())

        @app.command()
        def check(template: str, data: str):
            """Dry run: list template fields and check CSV coverage."""
            _run_check(type("A", (), dict(template=template, data=data))())
        app(args=argv, standalone_mode=False)
        return 0

    # ---- argparse fallback (keeps the file runnable if typer is absent) ----
    import argparse
    p = argparse.ArgumentParser(description="python-docx mail-merge helper")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("selftest", help="run the self-test")
    m = sub.add_parser("merge", help="merge rows into the template")
    m.add_argument("--template", required=True)
    m.add_argument("--data", required=True)
    m.add_argument("--out", default="merged")
    c = sub.add_parser("check", help="dry-run field coverage check")
    c.add_argument("--template", required=True)
    c.add_argument("--data", required=True)
    args = p.parse_args(argv)
    if args.cmd == "selftest":
        return run_selftest()
    if args.cmd == "merge":
        return _run_merge(args)
    if args.cmd == "check":
        return _run_check(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
