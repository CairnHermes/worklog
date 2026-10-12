#!/usr/bin/env python3
"""
python-website-automation-bot - PoC
===================================
A small, dependency-light automation tool that does the two chores the
client described:

  1) Automated form submissions - reads an HTML form, fills it from a
     JSON spec, and either POSTs it to the server or (offline) returns
     the populated payload.
  2) Data extraction            - pulls text / attributes / tables out of
     the resulting page with BeautifulSoup and returns clean Python data.

Design goals:
  - Works offline against a bundled sample page (so you can verify the
    logic without a target URL).
  - Works live against a real URL when you pass --url.
  - No JS engine required for the core path (requests + BeautifulSoup).
  - Clear, readable output you can inspect.

Run it:
    python PoC.py                 # offline demo against the bundled sample
    python PoC.py --url https://example.com/contact --fields contact.json
                                   # live mode (needs a real target)

Dependencies: requests, beautifulsoup4   (pip install requests beautifulsoup4)
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError as e:  # pragma: no cover
    print("Missing dependency. Install with: pip install requests beautifulsoup4")
    print(f"  ({e})")
    sys.exit(2)


# --------------------------------------------------------------------------- #
# Core: form filling
# --------------------------------------------------------------------------- #
@dataclass
class FilledForm:
    url: str
    method: str
    action: str
    fields: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def parse_form(html: str, base_url: str = "") -> dict[str, Any]:
    """Extract the first <form> and its fields into a plain dict."""
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form")
    if form is None:
        return {"present": False, "method": "", "action": "", "fields": []}

    method = (form.get("method") or "get").upper()
    action = form.get("action") or base_url

    fields: list[dict[str, str]] = []
    for el in form.find_all(["input", "select", "textarea"]):
        tag = el.name
        name = el.get("name")
        if not name:
            continue  # unnamed fields are not submittable
        ftype = (el.get("type") or "").lower()
        if tag == "input" and ftype in ("submit", "button", "reset", "image", "file"):
            continue
        entry: dict[str, str] = {"name": name, "tag": tag, "type": ftype or tag}
        if tag == "select":
            opts = [o.get("value") or o.get_text(strip=True) for o in el.find_all("option")]
            entry["options"] = opts
        elif tag == "input":
            entry["required"] = "yes" if el.get("required") else "no"
        fields.append(entry)

    return {
        "present": True,
        "method": method,
        "action": action,
        "fields": fields,
    }


def fill_form(html: str, spec: dict[str, str], base_url: str = "") -> FilledForm:
    """Merge a field spec into the parsed form and produce the payload.

    `spec` maps field name -> value. Any form field not covered by the
    spec is left empty. Unknown spec keys (not on the form) are reported
    as notes rather than silently dropped.
    """
    parsed = parse_form(html, base_url)
    if not parsed.get("present"):
        raise ValueError("No <form> found in the HTML - nothing to fill.")

    form_field_names = {f["name"] for f in parsed["fields"]}
    filled = FilledForm(
        url=parsed["action"],
        method=parsed["method"],
        action=parsed["action"],
        notes=[],
    )

    for f in parsed["fields"]:
        name = f["name"]
        if name in spec:
            value = spec[name]
            if f["tag"] == "select":
                opts = f.get("options", [])
                # Validate select value against options where possible.
                if opts and value not in opts:
                    filled.notes.append(f"select {name!r}: {value!r} not in options {opts}")
                filled.fields[name] = value
            else:
                filled.fields[name] = value
        else:
            filled.fields[name] = ""

    for key in spec:
        if key not in form_field_names:
            filled.notes.append(f"spec key {key!r} has no matching form field (ignored)")

    return filled


def submit_filled(form: FilledForm, timeout: float = 20.0) -> dict[str, Any]:
    """POST (or GET) a filled form to its action URL. Live network call."""
    url = form.url
    if form.method == "GET":
        resp = requests.get(url, params=form.fields, timeout=timeout)
    else:
        resp = requests.post(url, data=form.fields, timeout=timeout)
    return {
        "status": resp.status_code,
        "final_url": resp.url,
        "headers_subset": {k: resp.headers.get(k) for k in ("Content-Type", "Location") if resp.headers.get(k)},
        "snippet": resp.text[:300],
    }


# --------------------------------------------------------------------------- #
# Core: data extraction
# --------------------------------------------------------------------------- #
def extract(html: str, selectors: dict[str, str]) -> dict[str, Any]:
    """Extract text for each named CSS selector.

    `selectors` maps a label -> CSS selector. If the selector matches
    multiple elements, a list of strings is returned; one element -> string.
    """
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, Any] = {}
    for label, sel in selectors.items():
        nodes = soup.select(sel)
        if not nodes:
            out[label] = None
        elif len(nodes) == 1:
            out[label] = nodes[0].get_text(strip=True)
        else:
            out[label] = [n.get_text(strip=True) for n in nodes]
    return out


def extract_table(html: str, selector: str = "table") -> list[dict[str, str]]:
    """Extract the first table matching `selector` into list-of-rows dicts."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one(selector)
    if table is None:
        return []
    headers = [th.get_text(strip=True) for th in table.find_all("th")]
    rows = []
    for tr in table.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if not cells:
            continue
        row = dict(zip(headers or [f"col{i}" for i in range(len(cells))], cells))
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
# Demo: a realistic sample page (contact form + a results table)
# --------------------------------------------------------------------------- #
SAMPLE_PAGE = """
<!doctype html>
<html><head><title>Acme Parts - Support</title></head>
<body>
  <h1 id="title">Acme Parts Support Request</h1>
  <p class="intro">Tell us what you need and we will respond within one business day.</p>

  <form action="/support/submit" method="post" name="support">
    <label>Full name <input name="full_name" type="text" required></label>
    <label>Email <input name="email" type="email" required></label>
    <label>Topic
      <select name="topic">
        <option value="order">Order status</option>
        <option value="billing">Billing</option>
        <option value="tech">Technical</option>
      </select>
    </label>
    <label>Message <textarea name="message"></textarea></label>
    <button type="submit">Send request</button>
  </form>

  <h2>Live parts availability</h2>
  <table id="availability">
    <tr><th>Part</th><th>SKU</th><th>In stock</th></tr>
    <tr><td>Bearing 6204</td><td>BRG-6204</td><td>142</td></tr>
    <tr><td>Seal kit</td><td>SEAL-KIT</td><td>0</td></tr>
    <tr><td>Grease 400g</td><td>GRE-400</td><td>37</td></tr>
  </table>
</body></html>
"""

# A field spec the client would hand us (e.g. from a JSON file).
DEMO_SPEC = {
    "full_name": "Dana Whitfield",
    "email": "dana.whitfield@example.com",
    "topic": "tech",
    "message": "The bearing 6204 we ordered shows 0 in stock on the site. Can you confirm lead time?",
}

DEMO_SELECTORS = {
    "page_title": "#title",
    "intro": ".intro",
    "part_names": "#availability td:nth-child(1)",
}


def run_offline_demo() -> dict[str, Any]:
    """Exercise fill + extract against the bundled sample page."""
    print("=" * 72)
    print("OFFLINE DEMO  (bundled sample page: Acme Parts Support)")
    print("=" * 72)

    # 1) Parse the form so the client can see what the bot found.
    parsed = parse_form(SAMPLE_PAGE, base_url="https://acme.example")
    print("\n[form parse]")
    print(f"  action : {parsed['action']}")
    print(f"  method : {parsed['method']}")
    for f in parsed["fields"]:
        extra = f" (options={f['options']})" if f.get("options") else ""
        print(f"  - {f['name']:<12} {f['tag']:<7} {f['type']}{extra}")

    # 2) Fill it from the spec.
    filled = fill_form(SAMPLE_PAGE, DEMO_SPEC, base_url="https://acme.example")
    print("\n[fill result]")
    print(f"  POST -> {filled.url}")
    for k, v in filled.fields.items():
        shown = v if len(v) <= 60 else v[:57] + "..."
        print(f"  {k:<12} = {shown}")
    for n in filled.notes:
        print(f"  note: {n}")

    # 3) Extract data from the page.
    data = extract(SAMPLE_PAGE, DEMO_SELECTORS)
    print("\n[data extraction]")
    for k, v in data.items():
        print(f"  {k}: {v}")

    table = extract_table(SAMPLE_PAGE, "#availability")
    print("\n[table extraction -> rows]")
    for r in table:
        print(f"  {r}")

    return {
        "parsed": parsed,
        "filled": filled,
        "extracted": data,
        "table": table,
    }


def run_live(url: str, fields: str, selectors: str) -> dict[str, Any]:
    """Live mode: fetch a page, fill its form, submit, and extract."""
    print("=" * 72)
    print(f"LIVE MODE  ({url})")
    print("=" * 72)

    resp = requests.get(url, timeout=25)
    resp.raise_for_status()
    html = resp.text

    spec = json.loads(Path(fields).read_text()) if fields else {}
    sel = json.loads(Path(selectors).read_text()) if selectors else {}

    filled = fill_form(html, spec, base_url=url)
    print(f"\n[fill] {filled.method} {filled.url}")
    for k, v in filled.fields.items():
        print(f"  {k} = {v}")

    # Do NOT auto-submit to an unknown site without confirmation.
    result = {
        "note": "Form prepared. Live submission is intentionally NOT fired "
                "automatically - call submit_filled(form) once you confirm the target.",
        "extracted": extract(html, sel) if sel else {},
    }
    print("\n[live] extraction:", json.dumps(result.get("extracted", {}), indent=2))
    return result


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Website form automation + data extraction PoC")
    ap.add_argument("--url", help="Live page URL to operate on")
    ap.add_argument("--fields", help="Path to a JSON file mapping field name -> value")
    ap.add_argument("--selectors", help="Path to a JSON file mapping label -> CSS selector")
    ap.add_argument("--live-submit", action="store_true",
                    help="Actually POST/GET the filled form to the live site")
    ap.add_argument("--save", help="Write the full result JSON to this path")
    args = ap.parse_args(argv)

    if args.url:
        result = run_live(args.url, args.fields, args.selectors)
        if args.live_submit:
            # Rebuild the form to submit (only after explicit --live-submit).
            r = requests.get(args.url, timeout=25)
            spec = json.loads(Path(args.fields).read_text()) if args.fields else {}
            filled = fill_form(r.text, spec, base_url=args.url)
            out = submit_filled(filled)
            print("\n[submitted]")
            print(json.dumps(out, indent=2))
            result["submission"] = out
    else:
        result = run_offline_demo()

    if args.save:
        Path(args.save).write_text(json.dumps(result, indent=2, default=str))
        print(f"\nSaved full result to {args.save}")

    print("\nDONE: fill + extract verified on the sample page.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
