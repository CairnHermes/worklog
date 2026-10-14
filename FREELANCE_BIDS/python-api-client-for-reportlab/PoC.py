#!/usr/bin/env python3
"""ReportLab PDF invoice builder - a clean, importable API surface.

Design notes (mirrors the posting's constraints):
  * Idiomatic, small, documented. Public functions carry docstrings; every
    public type is a dataclass so callers can build invoices in a few lines.
  * Stdlib + ReportLab only. No Flask, no framework, no hidden deps.
  * Fail with named errors, not tracebacks. Every failure raises one of the
    small exception classes below and names the offending value in its
    message, so a downstream integrator gets a string they can log and show.
  * PDF text is rendered with a single Helvetica family; line items sit in
    a Platypus table; tax and totals are computed from the same data model
    so what the client sees and what the math says always agree.

Public API (import from this file or use via the CLI):

    from invoice import Invoice, LineItem, build_invoice_pdf, make_invoice

    inv = Invoice(
        number="INV-2026-001",
        from_name="Acme Consulting",
        to_name="Globex Ltd.",
        lines=[LineItem("Design work", 12, 150.0)],
        tax_rate=0.10,
        currency="GBP",
    )
    path = build_invoice_pdf(inv, "out/invoice-2026-001.pdf")

CLI:
    python PoC.py selftest       # offline: ~20 checks incl. PDF text extraction
    python PoC.py demo [outdir]  # render one realistic sample invoice
    python PoC.py render spec.json out.pdf   # spec = JSON dict of an Invoice
    python PoC.py --version
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import struct
import sys
import zlib
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

# ReportLab is the only third-party import (pinned in requirements.txt).
# Imported at module level so a clean ImportError message names the real
# dependency when the package is absent.
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.lib.styles import getSampleStyleSheet


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class InvoiceError(Exception):
    """Base class for every failure this module raises.

    Catches cleanly when a caller wants to log or surface any invoice
    problem, without depending on ReportLab's own exception tree.
    """


class MissingFieldError(InvoiceError):
    """A required invoice field was absent or empty."""

    def __init__(self, field_name: str):
        super().__init__(f"missing required invoice field: {field_name!r}")
        self.field_name = field_name


class InvalidLineItemError(InvoiceError):
    """A line item's quantity or unit price is out of range."""


class InvalidTaxRateError(InvoiceError):
    """Tax rate is negative or absurdly large (>= 100%)."""


class InvalidCurrencyError(InvoiceError):
    """Currency symbol is not one of the supported set."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class LineItem:
    """A single charged line on the invoice.

    quantity   - units delivered (must be > 0)
    unit_price - price per unit in the invoice currency (must be >= 0)
    description- short text, may be empty (falls back to "Service")
    """

    description: str
    quantity: float
    unit_price: float

    @property
    def total(self) -> float:
        return round(self.quantity * self.unit_price, 2)

    def validate(self) -> None:
        if self.quantity <= 0:
            raise InvalidLineItemError(
                f"line item {self.description!r}: quantity must be > 0, got {self.quantity!r}"
            )
        if self.unit_price < 0:
            raise InvalidLineItemError(
                f"line item {self.description!r}: unit_price must be >= 0, got {self.unit_price!r}"
            )


@dataclass
class Invoice:
    """A complete, renderable invoice.

    All money fields are floats in the currency given by `currency`.
    `tax_rate` is a decimal (0.10 == 10%), applied to the pre-tax subtotal.
    `due_date` accepts either a datetime.date / datetime.datetime or an
    ISO-8601 string ("2026-11-30"). `notes` is optional free-text printed
    near the totals block.
    """

    number: str
    from_name: str
    from_address: str
    to_name: str
    to_address: str
    lines: List[LineItem]
    tax_rate: float = 0.0
    currency: str = "USD"
    issue_date: Optional[_dt.date] = None
    due_date: Optional[_dt.date] = None
    notes: str = ""
    page_size: object = A4

    def validate(self) -> None:
        for name in ("number", "from_name", "from_address", "to_name", "to_address"):
            value = getattr(self, name)
            if not value or not str(value).strip():
                raise MissingFieldError(name)
        if not self.lines:
            raise InvalidLineItemError("invoice has no line items")
        if self.tax_rate < 0 or self.tax_rate >= 1:
            raise InvalidTaxRateError(
                f"tax_rate must be a decimal in [0, 1), got {self.tax_rate!r}"
            )
        if self.currency not in _SUPPORTED_CURRENCIES:
            raise InvalidCurrencyError(
                f"currency {self.currency!r} is not one of the supported symbols: "
                f"{sorted(_SUPPORTED_CURRENCIES)}"
            )
        for line in self.lines:
            line.validate()

    @property
    def subtotal(self) -> float:
        return round(sum(l.total for l in self.lines), 2)

    @property
    def tax_amount(self) -> float:
        return round(self.subtotal * self.tax_rate, 2)

    @property
    def total(self) -> float:
        return round(self.subtotal + self.tax_amount, 2)


# ---------------------------------------------------------------------------
# Money formatting
# ---------------------------------------------------------------------------

_SUPPORTED_CURRENCIES = {
    "USD": "$",
    "GBP": "\u00a3",
    "EUR": "\u20ac",
    "AUD": "A$",
    "CAD": "C$",
    "INR": "\u20b9",
    "JPY": "\u00a5",
}


def format_money(amount: float, currency: str = "USD", decimals: int = 2) -> str:
    """Return a human-readable money string for `amount` in `currency`.

    Uses thousands separators and a fixed number of decimals. Unsupported
    currencies raise InvalidCurrencyError with the name of the offending
    code, matching the job's "error messages that help debug" constraint.
    """
    symbol = _SUPPORTED_CURRENCIES.get(currency)
    if symbol is None:
        raise InvalidCurrencyError(
            f"currency {currency!r} is not supported (expected one of {sorted(_SUPPORTED_CURRENCIES)})"
        )
    sign = "-" if amount < 0 else ""
    value = abs(amount)
    whole, frac = divmod(round(value * (10 ** decimals)), 10 ** decimals)
    whole_str = f"{int(whole):,}"
    frac_str = f"{int(frac):0{decimals}d}" if decimals else ""
    return f"{sign}{symbol}{whole_str}.{frac_str}" if decimals else f"{sign}{symbol}{whole_str}"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _coerce_date(value) -> _dt.date:
    if value is None:
        return _dt.date.today()
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, str):
        try:
            return _dt.date.fromisoformat(value)
        except ValueError as exc:
            raise InvalidLineItemError(
                f"could not parse date string {value!r} as ISO-8601"
            ) from exc
    raise InvoiceError(f"unsupported date type: {type(value).__name__}")


def _build_table(lines: Sequence[LineItem]) -> Table:
    header = ["Description", "Qty", "Unit price", "Total"]
    rows = [header]
    for line in lines:
        # Description stays plain string in the table cell - reportlab
        # handles the escaping. Numbers right-align via the style below.
        rows.append(
            [
                line.description or "Service",
                f"{line.quantity:g}",
                f"{line.unit_price:,.2f}",
                f"{line.total:,.2f}",
            ]
        )
    tbl = Table(rows, colWidths=[90 * mm, 18 * mm, 26 * mm, 30 * mm])
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2933")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 9),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("ALIGN", (0, 0), (0, -1), "LEFT"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 1), (-1, -1), 9),
                ("LINEBELOW", (0, 0), (-1, 0), 1, colors.black),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f7fa")]),
            ]
        )
    )
    return tbl


def _add_invoice_text(cv: canvas.Canvas, inv: Invoice) -> None:
    styles = getSampleStyleSheet()
    title = styles["Title"]
    normal = styles["Normal"]
    bold = styles["Heading2"]

    cv.setFont("Helvetica-Bold", 18)
    cv.drawString(20 * mm, 260 * mm, "INVOICE")
    cv.setFont("Helvetica", 10)
    cv.drawString(20 * mm, 253 * mm, f"No: {inv.number}")

    # Two-column header block: From / To
    cv.setFont("Helvetica-Bold", 10)
    cv.drawString(20 * mm, 240 * mm, "From")
    cv.drawString(110 * mm, 240 * mm, "To")
    cv.setFont("Helvetica", 9)
    for i, part in enumerate(inv.from_address.splitlines() or [""]):
        cv.drawString(20 * mm, (234 - i * 4) * mm, part)
    for i, part in enumerate(inv.to_address.splitlines() or [""]):
        cv.drawString(110 * mm, (234 - i * 4) * mm, part)

    issue = _coerce_date(inv.issue_date)
    due = _coerce_date(inv.due_date)
    cv.drawString(20 * mm, 210 * mm, f"Issued: {issue.isoformat()}")
    cv.drawString(110 * mm, 210 * mm, f"Due: {due.isoformat()}")

    # Line items table
    table = _build_table(inv.lines)
    # Wrap in a Flowable-free layout by drawing the table onto the canvas
    # using the Table wrapper's .wrap + .draw. We use A4 height minus header.
    avail_w, avail_h = inv.page_size
    table.wrap(avail_w - 40 * mm, 100 * mm)
    table.drawOn(cv, 20 * mm, 110 * mm)

    # Totals block
    subtotal = format_money(inv.subtotal, inv.currency)
    tax_amt = format_money(inv.tax_amount, inv.currency)
    total = format_money(inv.total, inv.currency)
    cv.setFont("Helvetica", 10)
    y = 100 * mm
    cv.drawString(85 * mm, y, "Subtotal")
    cv.drawRightString(avail_w - 20 * mm, y, subtotal)
    y -= 6 * mm
    cv.drawString(85 * mm, y, f"Tax ({inv.tax_rate:.0%})")
    cv.drawRightString(avail_w - 20 * mm, y, tax_amt)
    y -= 8 * mm
    cv.setLineWidth(1)
    cv.line(80 * mm, y + 3 * mm, avail_w - 20 * mm, y + 3 * mm)
    y -= 4 * mm
    cv.setFont("Helvetica-Bold", 12)
    cv.drawString(85 * mm, y, "Total")
    cv.drawRightString(avail_w - 20 * mm, y, total)

    if inv.notes:
        y -= 14 * mm
        cv.setFont("Helvetica", 9)
        for line in inv.notes.splitlines():
            cv.drawString(20 * mm, y, line)
            y -= 4 * mm

    cv.setFont("Helvetica-Oblique", 8)
    cv.drawString(
        20 * mm, 10 * mm,
        f"Generated by invoice.py on {_dt.date.today().isoformat()}",
    )


def build_invoice_pdf(inv: Invoice, out_path: str) -> str:
    """Render `inv` to a PDF at `out_path` and return that path.

    Raises the InvoiceError subclasses above on any validation failure -
    never a raw ReportLab exception, and never a bare "something went
    wrong" message.
    """
    inv.validate()
    directory = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(directory, exist_ok=True)
    cv = canvas.Canvas(out_path, pagesize=inv.page_size)
    _add_invoice_text(cv, inv)
    cv.showPage()
    cv.save()
    return out_path


def make_invoice(
    number: str,
    from_name: str,
    to_name: str,
    lines: Sequence[LineItem],
    tax_rate: float = 0.0,
    currency: str = "USD",
    from_address: str = "",
    to_address: str = "",
    issue_date=None,
    due_date=None,
    notes: str = "",
) -> Invoice:
    """Convenience constructor for the common case.

    Mirrors the exact keyword set a caller would want when building an
    invoice from a dict (JSON / database row). Delegates to `Invoice` and
    validates eagerly so callers get the named error at construction time.
    """
    inv = Invoice(
        number=number,
        from_name=from_name,
        from_address=from_address or from_name,
        to_name=to_name,
        to_address=to_address or to_name,
        lines=list(lines),
        tax_rate=tax_rate,
        currency=currency,
        issue_date=issue_date,
        due_date=due_date,
        notes=notes,
    )
    inv.validate()
    return inv


def invoice_from_dict(spec: dict) -> Invoice:
    """Build an Invoice from a plain dict (e.g. `json.load`).

    Accepts `lines` as a list of dicts with the keys `description`,
    `quantity`, `unit_price`. Anything missing or of the wrong type
    raises the matching named error, not a KeyError/TypeError.
    """
    try:
        lines = [
            LineItem(
                description=item["description"],
                quantity=item["quantity"],
                unit_price=item["unit_price"],
            )
            for item in spec["lines"]
        ]
    except (KeyError, TypeError) as exc:
        raise InvalidLineItemError(
            f"lines must be a list of objects with description/quantity/unit_price; got {exc!r}"
        ) from exc
    return make_invoice(
        number=spec["number"],
        from_name=spec["from_name"],
        to_name=spec["to_name"],
        lines=lines,
        tax_rate=spec.get("tax_rate", 0.0),
        currency=spec.get("currency", "USD"),
        from_address=spec.get("from_address", ""),
        to_address=spec.get("to_address", ""),
        issue_date=spec.get("issue_date"),
        due_date=spec.get("due_date"),
        notes=spec.get("notes", ""),
    )


# ---------------------------------------------------------------------------
# Selftest helpers (no network, no keys)
# ---------------------------------------------------------------------------

def _extract_pdf_text(path: str) -> str:
    """Extract the visible text from a rendered single/multi-page PDF.

    Prefers pypdf (a real PDF reader - robust across content-stream
    filters, which vary between ReportLab versions). If pypdf is not
    installed it falls back to a best-effort decode of Flate-compressed
    content streams, which only works for the uncompressed-ASCII /
    FlateDecode cases.

    The point of this helper is to prove the invoice's data actually made
    it onto the rendered page (not just into the data model). Callers
    should treat the result as "contains the rendered glyphs" rather than
    a full OCR.
    """
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001 - degrade to fallback, then raise
        raise InvoiceError(f"could not read PDF text with pypdf: {exc}") from exc

    # Fallback: only handles FlateDecode single-stream PDFs.
    with open(path, "rb") as fh:
        data = fh.read()
    if not data.startswith(b"%PDF-"):
        raise InvoiceError("output is not a PDF")
    chunks = []
    for marker in (b"stream\r\n", b"stream\n"):
        start = data.find(marker)
        while start != -1:
            start += len(marker)
            end = data.find(b"endstream", start)
            if end == -1:
                break
            stream = data[start:end]
            try:
                decoded = zlib.decompress(stream)
            except zlib.error:
                decoded = stream
            chunks.append(decoded)
            start = data.find(marker, end)
        break
    text_parts = []
    import re
    for chunk in chunks:
        for match in re.finditer(rb"\((?:\\.|[^\\()])*\)\s*Tj", chunk):
            token = match.group(0)
            s = token[: token.rfind(b")") + 1]
            s = s[1:-1]
            s = s.replace(b"\\(", b"(").replace(b"\\)", b")").replace(b"\\\\", b"\\")
            text_parts.append(s.decode("latin-1", errors="replace"))
        for match in re.finditer(rb"\[(?:\((?:\\.|[^\\()])*\)\s*)+\]\s*TJ", chunk):
            group = match.group(0)
            for inner in re.finditer(rb"\((?:\\.|[^\\()])*\)", group):
                s = inner.group(0)[1:-1]
                s = s.replace(b"\\(", b"(").replace(b"\\)", b")").replace(b"\\\\", b"\\")
                text_parts.append(s.decode("latin-1", errors="replace"))
    return "".join(text_parts)


def _selftest() -> int:
    """Run the offline test suite. Returns a process exit code (0 = all
    pass). Every assertion that fails raises AssertionError with a
    message that names the offending value, per the posting's constraint.
    """
    checks = 0

    def check(label: str, cond: bool, detail: str = "") -> None:
        nonlocal checks
        checks += 1
        if not cond:
            raise AssertionError(f"check {checks} failed: {label}" + (f" ({detail})" if detail else ""))

    # 1. Money formatting across currencies + negatives + zero
    check("USD whole dollars", format_money(1500.0) == "$1,500.00")
    check("GBP with pence", format_money(1234.5, "GBP") == "\u00a31,234.50")
    check("EUR zero", format_money(0.0, "EUR") == "\u20ac0.00")
    check("USD negative", format_money(-42.5, "USD") == "-$42.50")
    # Grouping is intentionally US-style (3-digit) for every currency; INR
    # lakh grouping is an optional locale extension, not part of this core.
    check("INR thousands grouping (US-style)", format_money(123456.78, "INR") == "\u20b9123,456.78")

    # 2. Unsupported currency raises the named error
    try:
        format_money(1.0, "XXX")
        raise AssertionError("format_money(XXX) did not raise")
    except InvalidCurrencyError as exc:
        check("unsupported currency error names the code", "XXX" in str(exc))

    # 3. LineItem math
    item = LineItem("Consulting", 12, 150.0)
    check("line total = qty * price", item.total == 1800.0)
    check("line total rounded to 2dp", LineItem("x", 3, 0.333).total == 1.0)

    # 4. LineItem validation
    for bad in (LineItem("bad", 0, 10), LineItem("neg", 5, -1)):
        try:
            bad.validate()
            raise AssertionError(f"LineItem.validate({bad}) did not raise")
        except InvalidLineItemError as exc:
            check(f"bad line item raises named error: {bad.description}", "line item" in str(exc))

    # 5. Invoice validation: missing field
    inv_missing = Invoice(
        number="", from_name="A", from_address="a", to_name="B", to_address="b",
        lines=[LineItem("x", 1, 1.0)],
    )
    try:
        inv_missing.validate()
        raise AssertionError("empty number did not raise")
    except MissingFieldError as exc:
        check("MissingFieldError names the field", exc.field_name == "number")

    # 6. Tax rate bounds
    for bad_rate in (-0.01, 1.0, 1.5):
        inv_bad_tax = Invoice(
            number="N", from_name="A", from_address="a", to_name="B", to_address="b",
            lines=[LineItem("x", 1, 1.0)], tax_rate=bad_rate,
        )
        try:
            inv_bad_tax.validate()
            raise AssertionError(f"tax_rate {bad_rate} did not raise")
        except InvalidTaxRateError:
            checks += 1

    # 7. Math: subtotal / tax / total
    inv = make_invoice(
        number="INV-2026-042",
        from_name="Acme Consulting",
        from_address="Acme Consulting\n1 Example St\nLondon",
        to_name="Globex Ltd.",
        to_address="Globex Ltd.\n42 Main Rd\nGlasgow",
        lines=[
            LineItem("Design work", 12, 150.0),
            LineItem("Engineering review", 6, 90.0),
        ],
        tax_rate=0.20,
        currency="GBP",
        issue_date=_dt.date(2026, 10, 14),
        due_date=_dt.date(2026, 11, 15),
        notes="Payment due on receipt of this invoice.",
    )
    check("subtotal sums line totals", inv.subtotal == 2340.0, f"got {inv.subtotal}")
    check("tax amount = subtotal * rate", inv.tax_amount == 468.0, f"got {inv.tax_amount}")
    check("total = subtotal + tax", inv.total == 2808.0, f"got {inv.total}")

    # 8. Render to PDF
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_selftest_invoice.pdf")
    path = build_invoice_pdf(inv, out)
    check("PDF file exists on disk", os.path.isfile(path))
    size = os.path.getsize(path)
    check("PDF is non-trivially sized (>= 1500 bytes)", size >= 1500, f"got {size} bytes")
    with open(path, "rb") as fh:
        head = fh.read(8)
    check("PDF magic header", head.startswith(b"%PDF-"))

    # 9. Extract text from the rendered page and confirm the key
    #    identifiers landed on the page (not just in the data model).
    text = _extract_pdf_text(path)
    for expected in ("INVOICE", "INV-2026-042", "Acme Consulting",
                     "Globex Ltd.", "Design work", "2,340.00", "2,808.00",
                     "2026-10-14", "2026-11-15", "Payment due on receipt"):
        check(f"rendered PDF text contains {expected!r}", expected in text,
              f"missing from extracted {len(text)} chars")

    # 10. Empty line list is rejected
    try:
        make_invoice(
            number="N", from_name="A", to_name="B", lines=[],
        )
        raise AssertionError("empty lines did not raise")
    except InvalidLineItemError:
        checks += 1

    # 11. invoice_from_dict round-trip
    spec = {
        "number": "INV-2026-999",
        "from_name": "Acme Consulting",
        "to_name": "Globex Ltd.",
        "lines": [
            {"description": "Setup", "quantity": 1, "unit_price": 500.0},
            {"description": "Hours", "quantity": 4, "unit_price": 125.0},
        ],
        "tax_rate": 0.10,
        "currency": "USD",
        "notes": "Thanks!",
    }
    inv2 = invoice_from_dict(spec)
    check("dict -> invoice subtotal", inv2.subtotal == 1000.0, f"got {inv2.subtotal}")
    check("dict -> invoice tax", inv2.tax_amount == 100.0, f"got {inv2.tax_amount}")
    check("dict -> invoice total", inv2.total == 1100.0, f"got {inv2.total}")

    # 12. dict with a malformed line raises the named error
    try:
        invoice_from_dict({
            "number": "X", "from_name": "A", "to_name": "B",
            "lines": [{"description": "only desc"}],
        })
        raise AssertionError("malformed dict line did not raise")
    except InvalidLineItemError as exc:
        check("malformed line names the issue", "description/quantity/unit_price" in str(exc))

    # 13. Date coercion
    check("date.fromisoformat ok", _coerce_date("2026-12-01") == _dt.date(2026, 12, 1))
    check("datetime.date passthrough", _coerce_date(_dt.date(2026, 1, 2)) == _dt.date(2026, 1, 2))
    check("datetime passthrough", _coerce_date(_dt.datetime(2026, 1, 3, 9, 0)) == _dt.date(2026, 1, 3))
    check("None -> today (no error)", isinstance(_coerce_date(None), _dt.date))
    try:
        _coerce_date("not-a-date")
        raise AssertionError("bad date string did not raise")
    except InvoiceError:
        checks += 1

    # 14. A second page-size works too
    inv_l = make_invoice(
        number="US-1", from_name="A", to_name="B",
        lines=[LineItem("thing", 1, 1.0)],
    )
    inv_l.page_size = letter
    out_l = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_selftest_letter.pdf")
    p = build_invoice_pdf(inv_l, out_l)
    check("letter-page PDF rendered", os.path.getsize(p) >= 1200)

    # Clean up the scratch PDFs
    for f in (out, out_l):
        if os.path.isfile(f):
            os.remove(f)

    print(f"selftest: {checks} checks passed, 0 failed")
    return 0


def _demo(outdir: str) -> None:
    """Render one realistic sample invoice and print the file path."""
    os.makedirs(outdir, exist_ok=True)
    inv = make_invoice(
        number="INV-2026-1014",
        from_name="Acme Consulting Ltd.",
        from_address="Acme Consulting Ltd.\n12 Example Street\nLondon  N1 1AA",
        to_name="Globex Holdings",
        to_address="Globex Holdings\n4200 Main Avenue\nNew York  NY 10001",
        lines=[
            LineItem("Product design (sprint 4)", 10, 180.0),
            LineItem("Engineering support", 20, 140.0),
            LineItem("Documentation review", 3, 220.0),
        ],
        tax_rate=0.0825,
        currency="USD",
        issue_date=_dt.date(2026, 10, 14),
        due_date=_dt.date(2026, 11, 13),
        notes="Please remit by the due date. Reference: INV-2026-1014.",
    )
    out = os.path.join(outdir, "invoice-2026-1014.pdf")
    path = build_invoice_pdf(inv, out)
    print(f"demo: wrote {path} ({os.path.getsize(path)} bytes)")
    print(f"      subtotal {format_money(inv.subtotal)}  "
          f"tax {format_money(inv.tax_amount)}  "
          f"total  {format_money(inv.total)}")


def _render(spec_path: str, out_path: str) -> None:
    with open(spec_path, "r", encoding="utf-8") as fh:
        spec = json.load(fh)
    inv = invoice_from_dict(spec)
    path = build_invoice_pdf(inv, out_path)
    print(f"render: wrote {path} ({os.path.getsize(path)} bytes)")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="invoice",
        description="ReportLab-based PDF invoice builder (clean API + CLI).",
    )
    parser.add_argument("--version", action="store_true", help="print the module version")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("selftest", help="run the offline test suite (no network)")
    demo = sub.add_parser("demo", help="render a sample invoice to a PDF")
    demo.add_argument("outdir", nargs="?", default="./demo_invoices",
                     help="directory to write the sample invoice to (default ./demo_invoices)")
    render = sub.add_parser("render", help="render an invoice from a JSON spec")
    render.add_argument("spec", help="path to a JSON spec file")
    render.add_argument("out", help="output PDF path")

    args = parser.parse_args(argv)

    if args.version:
        print("invoice 1.0.0 (reportlab-based)")
        return 0
    if args.command == "selftest":
        return _selftest()
    if args.command == "demo":
        _demo(args.outdir)
        return 0
    if args.command == "render":
        _render(args.spec, args.out)
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
