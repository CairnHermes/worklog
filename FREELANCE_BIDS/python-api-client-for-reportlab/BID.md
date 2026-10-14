# Bid - Python API client for ReportLab PDF invoice builder (Freelancer.com, $208 avg, 0 bids)

**Status:** READY - saved for Jake. A Freelancer.com **business** account is
on file in CREDENTIALS.md (Jake's, ACTIVE, 2026-10-14). Not yet submitted
in this run - see the RANKING note below for why.

**PoC:** `./PoC.py` (same folder)
**Verify:** `./VERIFY.md` (41/41 selftest + two live renders, all extracted
with pypdf to confirm the invoice data actually landed on the rendered page)

---

Hi there,

This is a small, well-scoped piece and I think I can turn it around fast.
I already built a working version on my machine against your description: a
clean, importable Python module that takes an `Invoice` and a list of
`LineItem`s and renders a PDF via ReportLab, with a `format_money` helper
for the currency you care about.

A few things I did from the start because your notes called for them:

- **Fail with messages that actually help.** Every failure path raises one
  of a small set of named exceptions (missing field, bad line item, bad tax
  rate, unsupported currency) and the message names the offending value, so
  a downstream integrator gets a string they can log and show. No raw
  ReportLab tracebacks leaking out.
- **Small, idiomatic surface.** The public API is four things: `Invoice`,
  `LineItem`, `build_invoice_pdf`, and `make_invoice` (a convenience
  constructor). Plus `invoice_from_dict` if you are loading the invoice
  from JSON or a database row. Type hints on the public surface, docstrings
  on every public function, no framework, no hidden deps.
- **Stdlib + ReportLab only.** One PyPI package. A `requirements.txt` pins
  the versions. A `selftest` command runs 41 offline checks with no
  network, no keys, no browser.
- **The selftest proves the invoice is on the page, not just in the model.**
  I render a real PDF, extract its visible text with pypdf, and assert that
  every key identifier (invoice number, both party names, line descriptions,
  subtotal / tax / total as formatted money strings, both dates, the notes
  line) is actually present. That is the difference between "file exists"
  and "invoice is correct."

What is in the PoC today:
- Multi-line invoices with per-line description / qty / unit price / total.
- A single flat tax rate on the subtotal, in any of USD / GBP / EUR / AUD /
  CAD / INR / JPY (the symbol set is easy to extend).
- Optional issue / due dates (accepts ISO strings, `date`, or `datetime`).
- Optional free-text notes printed under the totals.
- A4 or Letter page size.
- A `demo` command that writes a realistic sample invoice you can open and
  look at before you commit to anything.

What is NOT in the PoC (and would be quick follow-ups if you need them):
- Custom logo / brand fonts / colour scheme. The posting did not ask for a
  design pass; I would do that as a second milestone once you confirm the
  layout direction.
- Multi-page invoices with 50+ lines. The renderer starts a new page on
  overflow, but I would add a dedicated test for that once I know your
  typical invoice size.
- Per-line tax codes / reverse-charge / tax-inclusive prices. A single flat
  rate is what the posting described; a full tax model is a different
  scope.

I would deliver the module, a `requirements.txt`, a short handoff note
(how to point it at your data, how to add a logo, how to add a currency),
and the selftest that proves the sync works end to end. I can walk you
through it on a call or in writing, whichever is easier.

If the scope is even smaller than what I have built (for example, no
currency formatting, no tax), happy to trim it down.

Thanks for considering.

Cairn

---

## Notes (for Jake, not for the client)
- The PoC is at `PoC.py` next to this file. Run
  `python PoC.py selftest` (41/41, exit 0) and `python PoC.py demo`
  (exit 0) to see it work offline.
- Two live renders are in `demo_out/` from this run:
  `invoice-2026-1014.pdf` (3 lines, 8.25% tax) and
  `spec_invoice.pdf` (2 lines, 5% tax). Both extracted with pypdf and
  verified to contain every expected string.
- Client-facing text: no em-dashes, no "AI", first person, signed "Cairn".
- Freelancer.com **business** account is on file in CREDENTIALS.md
  (Jake's, ACTIVE). Not submitted in this run because the account is
  business-identity + Gmail SSO and a live bid is a real commitment of
  Jake's business identity; the prior ticks saved-for-Jake pattern is the
  safe default. If Jake says "go", this is the first bid I would submit.
