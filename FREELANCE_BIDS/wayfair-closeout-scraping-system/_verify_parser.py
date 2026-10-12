#!/usr/bin/env python3
"""Self-test: prove the Wayfair scraper's PARSE core works (JSON-LD, cards, dedup).
The live site is bot-walled, so we exercise the parser on synthetic HTML instead
of claiming it works. This file is a dev check, not part of the deliverable."""
import importlib.util, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("wf", os.path.join(HERE, "PoC.py"))
wf = importlib.util.module_from_spec(spec)
sys.modules["wf"] = wf  # register before exec (Python 3.14 @dataclass needs this)
spec.loader.exec_module(wf)

html_jsonld = """
<script type="application/ld+json">
[
 {"@type":"Product","name":"Closeout 48-in Bookshelf","description":"Solid oak bookshelf, 5 shelves.","price":189.99,"sku":"SK-111"},
 {"@type":"Product","name":"Clearance Dining Chair","price":{"price":"212.5"},"sku":"SK-222"}
]
</script>
"""
html_cards = """
<div data-qa="productCard">
  <a data-qa="productCardName" href="/bookcase-abc12-334455">Oak Bookcase</a>
  <span data-qa="productCardPrice">$249.99</span>
</div></div>
<div data-qa="productCard">
  <a data-qa="productCardName" href="/chair-xyz98-998877">Pine Chair</a>
  <span data-qa="productCardPrice">$59.00</span>
</div></div>
"""
html_dup = html_jsonld + html_jsonld  # same JSON-LD twice -> dedup must collapse

for label, h in [("jsonld", html_jsonld), ("cards", html_cards), ("dedup", html_dup)]:
    prods = wf.scrape_page(h, "https://www.wayfair.com/search?q=x")
    print(f"[{label}] -> {len(prods)} unique")
    for p in prods:
        print(f"    name={p.name!r} price={p.price} src={p.source} pid={p.product_id}")

assert wf.scrape_page(html_jsonld, "u")[0].price == 189.99, "jsonld price parse failed"
assert wf.scrape_page(html_jsonld, "u")[1].price == 212.5, "nested offers.price parse failed"
assert wf.scrape_page(html_cards, "u")[0].name == "Oak Bookcase", "card name parse failed"
assert wf.scrape_page(html_cards, "u")[0].price == 249.99, "card price parse failed"
assert len(wf.scrape_page(html_dup, "u")) == 2, "dedup failed (expected 2 unique from doubled JSON-LD)"
print("\nASSERTIONS PASSED: JSON-LD parse, card parse, nested-price, and dedup all work.")
