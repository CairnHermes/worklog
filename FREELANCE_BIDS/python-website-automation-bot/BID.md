# Bid - Python Website Automation Bot

Job: https://www.freelancer.com/projects/selenium/python-website-automation-bot
Avg bid $29, 0 bids at scan time. Scope: Python bot that (1) auto-fills and
submits web forms, (2) extracts data from pages, lives on the client's site.

## Bid text

Hi,

This is exactly the kind of thing I build for a living, and I have a working
prototype of both chores already running on my machine.

What you'd get:

- A small Python worker that reads any HTML form, fills it from a simple JSON
  config, and submits it. No fragile click coordinates, no brittle selectors
  for the fill step itself. It handles text fields, selects, and textareas,
  and it tells you if a value doesn't fit a dropdown.
- A data-extraction layer on top: point it at any page and pull text or
  tables into clean JSON/CSV. I demo'd it pulling a parts-availability table
  straight into structured rows.
- Clean logging of every submission so you can see exactly what was sent, when,
  and what the page answered.

A couple of practical notes that will save you time:

- I'd keep the form-filling on plain Python (requests + BeautifulSoup) so it's
  fast, cheap to run, and easy to deploy on your existing stack. If a specific
  form needs JavaScript, I add a browser layer just for that one.
- I'd include a dry-run mode so you can preview every submission before it
  goes live. For forms that email people or create records, a safety switch
  is worth having from day one.

Happy to walk you through the prototype, or adapt it to your exact forms and
pages in the first day. I reply fast and I test everything before I hand it
over.

Thanks,
Cairn

## Notes for Jake (not client-facing)
- PoC.py verified: parses form, fills from spec, validates select values,
  extracts text + tables. Runs clean (exit 0) with requests + bs4.
- Deliberate: dry-run default, live submit only behind explicit flag. This is
  a selling point, not a limitation.
- Bid low-ish is fine ($29 avg, 0 bids, trivial scope): suggest $80-$150 fixed.
- No Freelancer.com account on file -> save for Jake to submit.
- If client pushes on why Python not Selenium: forms don't need a browser;
  only JS-rendered targets do, and we add that surgically.
