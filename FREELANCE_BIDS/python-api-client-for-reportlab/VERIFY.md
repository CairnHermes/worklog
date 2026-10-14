# VERIFY - Python API client for ReportLab PDF invoice builder

## Environment
- Python 3.14.6 (venv `.venv/` in this folder, created with `uv venv --python 3.14`).
- Deps: `reportlab 5.0.1` (core), `pypdf 6.20.1` (selftest text extraction only).
- Install: `uv venv .venv && VIRTUAL_ENV=$(pwd)/.venv uv pip install -r requirements.txt`

## Commands (run from this folder)
```
.venv/Scripts/python.exe PoC.py selftest     # 41 checks, exit 0
.venv/Scripts/python.exe PoC.py demo ./demo_out
.venv/Scripts/python.exe PoC.py render spec.json ./demo_out/spec_invoice.pdf
```

## What the selftest proves (41 checks, exit 0)
1. **Money formatting** across USD/GBP/EUR/AUD/CAD/INR/JPY: whole dollars,
   pence, zero, negatives, thousands grouping, and the unsupported-currency
   path raising `InvalidCurrencyError` that names the offending code.
2. **LineItem math + validation**: `total = qty * unit_price` rounded to 2dp;
   zero/negative qty and negative price raise `InvalidLineItemError` naming
   the offending line.
3. **Invoice validation**: empty required field raises `MissingFieldError`
   naming the field; negative / >=100% / 1.5 tax rates each raise
   `InvalidTaxRateError`; unsupported currency raises `InvalidCurrencyError`.
4. **Money math**: subtotal sums line totals; tax = subtotal * rate; total =
   subtotal + tax - all exact to the cent for a multi-line GBP invoice.
5. **Real PDF render**: file exists, >=1500 bytes, `%PDF-` magic header.
6. **Rendered-text proof** (the important one): pypdf extracts the visible
   text of the page and every key identifier is present - "INVOICE", the
   invoice number, both party names, a line description, the subtotal /
   tax / total as formatted money strings, both dates, and the notes line.
   This is not "the file exists" - it is "the invoice data made it onto the
   rendered page."
7. **Empty line list** is rejected with a named error.
8. **`invoice_from_dict` round-trip**: a plain dict (the JSON / DB shape)
   produces the correct subtotal / tax / total; a malformed line raises the
   named error naming the expected keys.
9. **Date coercion**: ISO string, `date`, `datetime`, `None` (defaults to
   today) all work; a bad string raises `InvoiceError`.
10. **Second page size** (US Letter) renders a valid non-trivial PDF.

## Live demo
`demo` writes `demo_out/invoice-2026-1014.pdf` (2571 bytes) - a realistic
3-line USD invoice with 8.25% tax:
  subtotal $5,260.00   tax $433.95   total $5,693.95
`render spec.json` writes `demo_out/spec_invoice.pdf` (2449 bytes) - a
2-line USD invoice at 5% tax:
  subtotal $2,610.00   tax $130.50   total $2,740.50
Both extracted with pypdf contain every expected string (INVOICE, number,
both parties, line items, subtotal/tax/total, notes).

## Honest limits
- **Font / style**: single Helvetica family, A4 or Letter. No custom logo,
  no custom fonts, no multi-column layouts. The posting did not ask for
  these; a design pass would be a separate scope.
- **Multi-page invoices**: the selftest only renders single-page invoices.
  The Platypus table + canvas flow handles overflow by starting a new page,
  but I did not build a test that forces page 2 to appear. If the client
  needs invoices with 50+ lines, that's a quick follow-up test.
- **Currency grouping**: US-style 3-digit grouping for all currencies.
  Indian lakh (1,23,456) grouping is an optional locale extension, not
  part of the core. The posting did not name a locale.
- **Tax model**: a single flat rate applied to the subtotal. No VAT / GST
  reverse-charge, no per-line tax codes, no tax-inclusive prices. The
  posting said "lightweight error handling" and a "clean handoff" - it
  did not ask for a tax engine.

## What is NOT in the PoC (and why)
- No Flask / FastAPI wrapper. The posting is "Python API client" - the
  API is the importable module (`Invoice`, `LineItem`, `build_invoice_pdf`,
  `make_invoice`, `invoice_from_dict`, `format_money`). An HTTP service
  around it is a 20-line add if the client wants one, but it is not the
  deliverable the posting describes.
- No database / ORM. No auth. No queueing. The scope is "idiomatic Python,
  stdlib + common PyPI libs, clarity and easy handoff."
- No template language (Jinja). The posting did not name one; the data
  model + renderer is the contract.
