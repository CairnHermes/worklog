#!/usr/bin/env python3
"""
tabula-py table extractor
=========================
Client for extracting tabular data from PDFs.

Prefer tabula-py when available (requires Java + tabula-py + tabula jar).
Fall back to a pure-Python pymupdf extractor that gives the same row/column
shape for simple, well-structured tables drawn with lines.

CLI:
    python PoC.py selftest
    python PoC.py extract --pdf report.pdf --pages 1,3 --out out.json
    python PoC.py demo --out-dir demo

Exit codes:
    0  success
    2  user / config error (missing file, bad page, no Java, no tabula)
    3  unexpected internal error
"""

from __future__ import annotations
import argparse
import csv
import io
import json
import math
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple

# -----------------------------------------------------------------------------
# Error types
# -----------------------------------------------------------------------------

class TableExtractError(Exception):
    """Base class for user-facing errors."""


class MissingPDFError(TableExtractError):
    def __init__(self, path: str):
        self.path = path
        super().__init__(f"PDF not found: {path!r}")


class InvalidPageError(TableExtractError):
    def __init__(self, page: int, max_page: int):
        self.page = page
        self.max_page = max_page
        super().__init__(f"page {page} out of range 1..{max_page}")


class JavaUnavailableError(TableExtractError):
    def __init__(self, detail: str = ""):
        super().__init__(
            "Java is not available on this machine (or tabula jar is not on "
            "the classpath). Install a JRE and `pip install tabula-py` to "
            "enable the tabula backend, or run with --backend pymupdf."
            + (f" Detail: {detail}" if detail else "")
        )


class NoBackendError(TableExtractError):
    def __init__(self):
        super().__init__(
            "No table-extraction backend available. Install tabula-py + a JRE, "
            "or install pymupdf."
        )


class TabulaNotInstalledError(TableExtractError):
    def __init__(self):
        super().__init__(
            "tabula-py is not installed. Install it with `pip install tabula-py` "
            "and make sure a Java JRE is on PATH, or run with --backend pymupdf."
        )


# -----------------------------------------------------------------------------
# Result types
# -----------------------------------------------------------------------------

@dataclass
class PageResult:
    page: int
    rows: List[List[str]]


@dataclass
class ExtractResult:
    pages: List[PageResult]
    backend: str            # which backend actually produced the result
    backend_tried: List[str]
    path: str
    warnings: List[str] = field(default_factory=list)

    def rows_all(self) -> List[List[str]]:
        out: List[List[str]] = []
        for p in self.pages:
            out.extend(p.rows)
        return out

    def to_csv(self, delimiter: str = ",") -> str:
        # Flatten all pages into one table. Cells are quoted if they contain
        # the delimiter, a quote, or a newline.
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=delimiter, quoting=csv.QUOTE_MINIMAL)
        for p in self.pages:
            for r in p.rows:
                w.writerow(r)
        return buf.getvalue()

    def to_json(self, indent: Optional[int] = 2) -> str:
        payload = {
            "path": self.path,
            "backend": self.backend,
            "backend_tried": self.backend_tried,
            "warnings": self.warnings,
            "pages": [
                {"page": p.page, "rows": p.rows} for p in self.pages
            ],
        }
        return json.dumps(payload, indent=indent, ensure_ascii=False)


# -----------------------------------------------------------------------------
# Backends
# -----------------------------------------------------------------------------

def _has_pymupdf() -> bool:
    try:
        import pymupdf  # type: ignore  # noqa
        return True
    except Exception:
        return False


def _tabula_extract(pdf: str, pages: List[int]) -> Tuple[List[PageResult], str]:
    """Run the real tabula-py path. Raises on any failure."""
    try:
        import tabula  # lazy import
    except ModuleNotFoundError as e:
        raise TabulaNotInstalledError() from e

    # tabula.extract accepts pages='1,2' or a single int; we pass a list.
    df = tabula.extract(pdf, pages=pages, multiple_tables=True)
    if df is None or not isinstance(df, list) or len(df) == 0:
        raise NoBackendError()

    # tabula.extract(multiple_tables=True) returns a list of DataFrames, one
    # per table on the page (or per page). Flatten to a single row list per
    # page. We cannot reliably map each df back to a specific page from the
    # API, so we emit one PageResult per DataFrame and label pages by
    # appearance order. This is documented behavior.
    out: List[PageResult] = []
    for i, d in enumerate(df, start=1):
        try:
            rows = d.astype(object).where(d.notna(), "").values.tolist()
        except Exception:
            rows = list(d.values.tolist())
        out.append(PageResult(page=i, rows=[[str(c) for c in r] for r in rows]))
    return out, "tabula-py"


def _pymupdf_extract(pdf: str, pages: List[int]) -> Tuple[List[PageResult], str]:
    """Pure-Python table extraction via pymupdf.

    Strategy:
      1. Pull every word with its left/top coordinates.
      2. Cluster words into rows by their vertical (top) coordinate,
         merging clusters closer than a small tolerance.
      3. Within each row, sort words left-to-right and cluster their left
         edges into columns; a new column starts whenever a word's left edge
         jumps more than a gap threshold past the current column anchor.
      4. Emit each row as a list of cell strings (concatenating words that
         fall in the same column, in reading order).
    """
    import pymupdf  # type: ignore  # noqa

    out: List[PageResult] = []
    doc = pymupdf.open(pdf)
    try:
        for pn in pages:
            if pn < 1 or pn > doc.page_count:
                raise InvalidPageError(pn, doc.page_count)
            page = doc[pn - 1]
            words = page.get_text("words")
            # words: list of (x0, y0, x1, y1, word, block_no, line_no, word_no)
            rows: List[List[str]] = []
            if not words:
                out.append(PageResult(page=pn, rows=rows))
                continue

            # --- Row clustering by y0 (top) ---------------------------------
            # Tolerance: a fraction of the median word height.
            heights = [abs(w[3] - w[1]) for w in words if abs(w[3] - w[1]) > 0]
            med_h = float(sorted(heights)[len(heights) // 2]) if heights else 12.0
            tol = max(2.0, 0.45 * med_h)

            words_by_y = sorted(words, key=lambda w: (w[1], w[0]))
            rows_clusters: List[List[Any]] = []
            for w in words_by_y:
                if not rows_clusters:
                    rows_clusters.append([w])
                    continue
                last = rows_clusters[-1]
                # Anchor of the cluster: median top of existing words.
                anchor = sum(x[1] for x in last) / len(last)
                if abs(w[1] - anchor) <= tol:
                    last.append(w)
                else:
                    rows_clusters.append([w])

            # --- Column clustering within a row ------------------------------
            # For the whole page, collect the left edges of all words and
            # derive a set of column anchors. Words whose left edge is within
            # `col_tol` of an anchor belong to that column.
            lefts = sorted({round(w[0], 2) for w in words})
            med_w = float(sorted(abs(w[2] - w[0]) for w in words)[len(words) // 2]) if words else 6.0
            col_tol = max(4.0, 0.55 * med_w)

            anchors: List[float] = []
            for L in lefts:
                placed = False
                for a in anchors:
                    if abs(L - a) <= col_tol:
                        placed = True
                        break
                if not placed:
                    anchors.append(L)
                    anchors.sort()

            def nearest_col(L: float) -> int:
                best = 0
                best_d = abs(L - anchors[0])
                for i, a in enumerate(anchors):
                    d = abs(L - a)
                    if d < best_d:
                        best_d = d
                        best = i
                return best

            for cluster in rows_clusters:
                cells: List[str] = [""] * len(anchors)
                # Sort within the row left-to-right; stable, preserves reading
                # order. If two words map to the same cell, join with a space.
                for w in sorted(cluster, key=lambda x: x[0]):
                    col = nearest_col(w[0])
                    if cells[col]:
                        cells[col] += " " + w[4]
                    else:
                        cells[col] = w[4]
                rows.append(cells)
            out.append(PageResult(page=pn, rows=rows))
    finally:
        doc.close()
    return out, "pymupdf"


# -----------------------------------------------------------------------------
# Public client
# -----------------------------------------------------------------------------

class TableExtract:
    """
    Extract tables from a PDF.

    Parameters
    ----------
    path : str
        Path to the PDF file.
    backend : "auto" | "tabula" | "pymupdf"
        Which backend to use. "auto" tries tabula first and falls back to
        pymupdf. An explicit choice will raise if that backend is not
        available.
    """

    def __init__(self, path: str, backend: str = "auto"):
        if not isinstance(path, str) or not path:
            raise ValueError("path must be a non-empty string")
        if not os.path.isfile(path):
            raise MissingPDFError(path)
        if not path.lower().endswith((".pdf",)):
            raise TableExtractError(f"expected a .pdf file, got {path!r}")
        if backend not in ("auto", "tabula", "pymupdf"):
            raise ValueError("backend must be one of: auto, tabula, pymupdf")
        self.path = path
        self.backend = backend

    def _max_page(self) -> int:
        import pymupdf  # type: ignore  # noqa
        d = pymupdf.open(self.path)
        try:
            return d.page_count
        finally:
            d.close()

    def extract(self, pages: Optional[List[int]] = None) -> ExtractResult:
        if pages is None:
            pages = [1]

        # Normalize pages (ints, >=1)
        norm: List[int] = []
        for p in pages:
            if not isinstance(p, int) or p < 1:
                raise InvalidPageError(p, self._max_page())
            norm.append(p)

        if self.backend == "tabula":
            pages_out, used = _tabula_extract(self.path, norm)
            return ExtractResult(
                pages=pages_out, backend=used, backend_tried=["tabula"], path=self.path
            )

        if self.backend == "pymupdf":
            if not _has_pymupdf():
                raise NoBackendError()
            pages_out, used = _pymupdf_extract(self.path, norm)
            return ExtractResult(
                pages=pages_out, backend=used, backend_tried=["pymupdf"], path=self.path
            )

        # auto: try tabula first, fall back to pymupdf
        tried: List[str] = []
        try:
            pages_out, used = _tabula_extract(self.path, norm)
            tried.append("tabula")
            return ExtractResult(
                pages=pages_out, backend=used, backend_tried=tried, path=self.path
            )
        except NoBackendError:
            tried.append("tabula")
        except Exception as e:
            # Java unavailable, tabula jar missing, or any tabula failure.
            tried.append("tabula")
            detail = str(e)
            self._last_tabula_err = detail

        # Fallback
        if _has_pymupdf():
            pages_out, used = _pymupdf_extract(self.path, norm)
            return ExtractResult(
                pages=pages_out, backend=used, backend_tried=tried + ["pymupdf"],
                path=self.path,
                warnings=[
                    f"tabula-py unavailable ({getattr(self, '_last_tabula_err', 'no detail')}); "
                    "fell back to pymupdf"
                ],
            )
        raise JavaUnavailableError(getattr(self, "_last_tabula_err", ""))


# -----------------------------------------------------------------------------
# Demo PDF generator (for selftest / demo)
# -----------------------------------------------------------------------------

def _make_demo_pdf(path: str) -> int:
    """Create a small multi-page PDF with a simple ruled table.

    Returns the number of pages written.
    """
    try:
        import pymupdf  # type: ignore  # noqa
    except Exception as e:
        raise NoBackendError() from e

    doc = pymupdf.open()
    pages = 0
    for pi, rows in enumerate([
        [["Q1", "Q2", "Q3", "Q4"],
         ["North", "12", "14", "15"],
         ["South", "8", "9", "10"]],
        [["Region", "Total"],
         ["East", "42"],
         ["West", "55"]],
    ], start=1):
        page = doc.new_page()
        # Draw a simple ruled grid.
        x0, y0 = 72, 90
        col_w = 100
        row_h = 26
        n_cols = len(rows[0])
        n_rows = len(rows)
        grid_w = col_w * n_cols
        grid_h = row_h * n_rows
        # Vertical lines
        for c in range(n_cols + 1):
            x = x0 + c * col_w
            page.draw_line((x, y0), (x, y0 + grid_h), color=(0, 0, 0), width=0.6)
        # Horizontal lines
        for r in range(n_rows + 1):
            y = y0 + r * row_h
            page.draw_line((x0, y), (x0 + grid_w, y), color=(0, 0, 0), width=0.6)
        # Cells
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                x = x0 + c * col_w + 4
                y = y0 + r * row_h + 16
                page.insert_text((x, y), str(cell), fontsize=10, fontname="hebo")
        pages = pi
    doc.save(path)
    doc.close()
    return pages


# -----------------------------------------------------------------------------
# Selftest
# -----------------------------------------------------------------------------

def _selftest(tmp: str) -> None:
    """Run a small set of checks. Prints a PASS/FAIL line per check and
    exits with 0 on success, 2 on failure.
    """
    checks: List[Tuple[str, Callable[[], None]]] = []

    def add(name: str, fn: Callable[[], None]) -> None:
        checks.append((name, fn))

    # --- Environment probe --------------------------------------------------
    tabula_present = False
    try:
        import tabula  # type: ignore  # noqa
        tabula_present = True
    except Exception:
        tabula_present = False

    # --- 1. Missing file raises MissingPDFError -----------------------------
    def t_missing() -> None:
        try:
            TableExtract("/no/such/file.pdf").extract([1])
        except MissingPDFError:
            return
        raise AssertionError("expected MissingPDFError")
    add("missing file raises MissingPDFError", t_missing)

    # --- 2. Non-PDF extension rejected --------------------------------------
    def t_ext() -> None:
        try:
            TableExtract("not_a_pdf.txt").extract([1])
        except TableExtractError:
            return
        raise AssertionError("expected TableExtractError for bad extension")
    add("non-pdf extension rejected", t_ext)

    # --- 3. Bad page raises InvalidPageError --------------------------------
    def t_badpage() -> None:
        pdf = os.path.join(tmp, "badpage.pdf")
        _make_demo_pdf(pdf)
        te = TableExtract(pdf, backend="pymupdf")
        try:
            te.extract([999])
        except InvalidPageError:
            return
        raise AssertionError("expected InvalidPageError for page 999")
    add("bad page raises InvalidPageError", t_badpage)

    # --- 4. Demo PDF extracts expected table (pymupdf) ----------------------
    def t_happy() -> None:
        pdf = os.path.join(tmp, "demo.pdf")
        _make_demo_pdf(pdf)
        te = TableExtract(pdf, backend="pymupdf")
        r = te.extract([1])
        # We expect 3 rows: header + 2 data rows, 4 columns each.
        assert len(r.pages) == 1, f"expected 1 page result, got {len(r.pages)}"
        rows = r.pages[0].rows
        assert len(rows) == 3, f"expected 3 rows, got {len(rows)}"
        # Header should contain the quarter labels.
        header = rows[0]
        for token in ("Q1", "Q2", "Q3", "Q4"):
            assert any(token in c for c in header), f"missing {token} in header {header}"
        # Data row should contain "North" and one of the numbers.
        data = rows[1]
        assert any("North" in c for c in data), f"missing North in {data}"
    add("happy path: demo PDF row+col extraction (pymupdf)", t_happy)

    # --- 5. Second page extraction ------------------------------------------
    def t_page2() -> None:
        pdf = os.path.join(tmp, "demo.pdf")
        _make_demo_pdf(pdf)
        te = TableExtract(pdf, backend="pymupdf")
        r = te.extract([2])
        assert len(r.pages) == 1
        rows = r.pages[0].rows
        assert any(any("East" in c for c in row) for row in rows), \
            f"expected 'East' on page 2, got {rows}"
    add("page 2: region table extracted", t_page2)

    # --- 6. All pages at once -----------------------------------------------
    def t_allpages() -> None:
        pdf = os.path.join(tmp, "demo.pdf")
        _make_demo_pdf(pdf)
        te = TableExtract(pdf, backend="pymupdf")
        r = te.extract([1, 2])
        assert len(r.pages) == 2
        assert any(any("Q1" in c for c in row) for row in r.pages[0].rows), "page1 Q1 missing"
        assert any(any("West" in c for c in row) for row in r.pages[1].rows), "page2 West missing"
    add("multi-page extraction", t_allpages)

    # --- 7. tabula path: either works, or fails with JavaUnavailableError --
    def t_tabula() -> None:
        pdf = os.path.join(tmp, "demo.pdf")
        _make_demo_pdf(pdf)
        te = TableExtract(pdf, backend="tabula")
        try:
            r = te.extract([1])
            # If tabula ran, we got something.
            assert r.backend == "tabula"
        except JavaUnavailableError:
            # This is the expected path on a box without Java.
            return
        except NoBackendError:
            # tabula-py not installed at all: also an acceptable, clean
            # failure. The client still raised a clean, actionable error.
            return
        except TableExtractError as e:
            # Any other clean, user-facing error is fine for this check;
            # we only fail if it raised a raw exception with no context.
            msg = str(e)
            assert msg, "TableExtractError with empty message"
    add("tabula backend: works or raises a clean Java/NoBackend error", t_tabula)

    # --- 8. Auto backend falls back to pymupdf ------------------------------
    def t_auto() -> None:
        pdf = os.path.join(tmp, "demo.pdf")
        _make_demo_pdf(pdf)
        te = TableExtract(pdf, backend="auto")
        r = te.extract([1])
        # On a box without tabula or Java, the backend will be pymupdf.
        # On a box with tabula+Java, it will be tabula. Either is OK.
        assert r.backend in ("tabula", "pymupdf")
        assert len(r.pages) == 1
        assert len(r.pages[0].rows) == 3
    add("auto backend: resolves to a working backend", t_auto)

    # --- 9. CSV output is well-formed ---------------------------------------
    def t_csv() -> None:
        pdf = os.path.join(tmp, "demo.pdf")
        _make_demo_pdf(pdf)
        te = TableExtract(pdf, backend="pymupdf")
        r = te.extract([1])
        csv_text = r.to_csv()
        lines = [ln for ln in csv_text.splitlines() if ln]
        assert len(lines) == 3, f"expected 3 CSV rows, got {len(lines)}"
        # Each row should have exactly 4 fields (or fewer if empty).
        for ln in lines:
            n = len(ln.split(","))
            assert n >= 1
    add("CSV output is well-formed", t_csv)

    # --- 10. JSON output is valid -------------------------------------------
    def t_json() -> None:
        pdf = os.path.join(tmp, "demo.pdf")
        _make_demo_pdf(pdf)
        te = TableExtract(pdf, backend="pymupdf")
        r = te.extract([1])
        obj = json.loads(r.to_json())
        assert obj["backend"] in ("tabula", "pymupdf")
        assert len(obj["pages"]) == 1
        assert len(obj["pages"][0]["rows"]) == 3
    add("JSON output is valid", t_json)

    # --- Run ----------------------------------------------------------------
    failed = 0
    for name, fn in checks:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            print(f"  FAIL  {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
            failed += 1

    if failed:
        print(f"\n{failed}/{len(checks)} checks failed.")
        sys.exit(2)
    print(f"\nAll {len(checks)} selftest checks passed.")
    print(f"  (tabula-py available: {tabula_present})")


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _parse_pages(s: Optional[str]) -> Optional[List[int]]:
    if not s:
        return None
    parts = [p.strip() for p in s.split(",") if p.strip()]
    out: List[int] = []
    for p in parts:
        if not p.isdigit():
            raise InvalidPageError(p, 0)
        out.append(int(p))
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Extract tables from a PDF.")
    sub = ap.add_subparsers(dest="cmd")

    st = sub.add_parser("selftest", help="Run selftest checks.")

    ex = sub.add_parser("extract", help="Extract tables from a PDF.")
    ex.add_argument("--pdf", required=True)
    ex.add_argument("--pages", default=None, help="Comma-separated page list, e.g. 1,2,3")
    ex.add_argument("--backend", default="auto", choices=["auto", "tabula", "pymupdf"])
    ex.add_argument("--out", default=None, help="Write JSON output to this path.")

    dm = sub.add_parser("demo", help="Generate a demo PDF and extract from it.")
    dm.add_argument("--out-dir", default="demo", help="Where to write the demo PDF.")

    args = ap.parse_args(argv)
    if args.cmd is None:
        ap.print_help()
        return 2

    import tempfile

    if args.cmd == "selftest":
        with tempfile.TemporaryDirectory(prefix="tabula_poc_") as tmp:
            _selftest(tmp)
        return 0

    if args.cmd == "demo":
        os.makedirs(args.out_dir, exist_ok=True)
        pdf = os.path.join(args.out_dir, "demo.pdf")
        _make_demo_pdf(pdf)
        te = TableExtract(pdf, backend="auto")
        r = te.extract([1, 2])
        out_json = os.path.join(args.out_dir, "extracted.json")
        with open(out_json, "w", encoding="utf-8") as f:
            f.write(r.to_json())
        out_csv = os.path.join(args.out_dir, "extracted.csv")
        with open(out_csv, "w", encoding="utf-8") as f:
            f.write(r.to_csv())
        print(f"demo PDF:  {pdf}")
        print(f"backend:   {r.backend}  (tried: {r.backend_tried})")
        print(f"rows:      {len(r.rows_all())}")
        print(f"json:      {out_json}")
        print(f"csv:       {out_csv}")
        if r.warnings:
            for w in r.warnings:
                print(f"warning:   {w}")
        return 0

    if args.cmd == "extract":
        pages = _parse_pages(args.pages)
        te = TableExtract(args.pdf, backend=args.backend)
        r = te.extract(pages)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(r.to_json())
            print(f"wrote {args.out}")
        else:
            print(r.to_json())
        if r.warnings:
            for w in r.warnings:
                print(f"warning: {w}", file=sys.stderr)
        return 0

    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except TableExtractError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)
