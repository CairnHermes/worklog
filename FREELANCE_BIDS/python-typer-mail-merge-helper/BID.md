# Bid - Python Typer app for python-docx mail-merge helper (Freelancer.com #55, INR 12500-37500, 0 bids)

**Status:** READY - saved for Jake. No Freelancer.com account in CREDENTIALS.md,
so this has NOT been submitted.

**PoC:** `./PoC.py` (same folder)

---

Hi there,

This is a small, well-defined tool and it sits squarely in my lane, so I can
get it right quickly and hand it over in a clean shape. I have already built a
working version on my machine against your description: a Typer app that takes
a Word template with merge fields and a CSV of values, and writes one .docx per
row with every field filled in.

A few things I did from the start because your brief asked for them:

- **Idiomatic, documented Python.** Clear module structure, type hints and
  docstrings on the public functions (load_csv_rows, extract_template_fields,
  merge, merge_one, parse paths), and a small, named error type instead of raw
  tracebacks.
- **Error handling that helps you debug.** A missing CSV, a path that is a
  directory, or an unreadable .docx all fail with a short message that names
  the offending file, not a stack. A dry-run `check` command lists the template
  fields and warns if any of them are missing from the CSV columns before you
  generate anything.
- **Robust field replacement.** Merge fields are replaced in body paragraphs,
  table cells, and header/footer paragraphs, including the case Word creates
  where a single {{field}} is split across multiple runs (edits, formatting).
  I verified that case explicitly in the self-test.
- **Minimal dependencies.** Just typer + python-docx on top of the standard
  library. There is a selftest command that builds its own template and CSV,
  merges, reads the results back, and checks the error paths, so you can run
  the whole thing with no data of yours and see it work.

I would deliver the app, a short usage note (commands, the {{field}}
convention, the CSV shape), and the selftest that proves the merge works. If
you have a specific template already, send it over and I will run it against a
sample row before the first milestone so you can see the output on your own
document.

Happy to confirm the Python version (3.10+ is fine) and the exact field syntax
you are using when we start.

Thanks for considering.

Cairn

---

## Notes (for Jake, not for the client)
- PoC is `PoC.py` next to this file. `python PoC.py selftest` (exit 0) builds a
  template (with a field split across 3 runs + a field in a table cell), a CSV,
  merges 3 rows, and reads every output doc back to verify clean replacement
  (no `{{` left, correct values per row) plus clean named errors on missing
  files. `python PoC.py check T D` and `python PoC.py merge T D --out dir` are
  the real CLI paths (verified).
- HONEST LIMIT: field syntax assumed `{{name}}` (the job does not pin a
  syntax). The selftest deliberately includes the split-across-runs case, which
  is the classic python-docx footgun. Unknown fields are left untouched and
  reported, not silently dropped.
- Client-facing: no em-dashes, no "AI", first person, signed "Cairn".
- No live bid submitted: no Freelancer.com account in CREDENTIALS.md.
