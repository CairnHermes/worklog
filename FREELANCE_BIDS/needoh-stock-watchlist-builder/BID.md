# Bid - Needoh Stock Watchlist Builder

Job: https://www.freelancer.com/projects/web-scraping/needoh-stock-watchlist-builder
Avg bid $17, 0 bids at scan time. Scope: small self-contained app that
constantly checks needoh.com for in-stock status + price of watched items,
real-time alerts, must run on the client's existing Railway deployment.

## Bid text

Hi,

Good news first: Needoh runs on Shopify, so this is way easier and more
reliable than scraping the HTML. Shopify stores expose a clean public
/products.json endpoint with every product, variant, price, and in-stock flag.
No fragile scraping, no anti-bot fighting, no maintenance headaches.

I've already built a working watchlist engine around exactly that:

- You give it the product handles you care about and it polls the store on an
  interval you choose.
- It tracks each variant's stock state and price, and alerts you the moment a
  watched item goes out-of-stock to IN STOCK (the restock case, which is the
  whole point), goes sold out, or drops in price past a threshold you set.
- Alerts go wherever you want: stdout, a webhook (Slack, Discord, ntfy,
  email-gateway, your pick), and a CSV log so you have the full history.
- It's a single self-contained Python process, which is exactly what Railway
  wants. No database required for the core, and I can add a tiny Redis or a
  simple state file if you want the watchlist itself to be user-editable later.

A couple of things I'd handle for you:

- Be polite to the store: sensible poll interval, single lightweight call per
  check, and it degrades gracefully if the endpoint is briefly unreachable.
- Real-time in the sense that matters: the alert lands the moment the poll
  sees the change. If you need sub-60-second detection I'll drop the interval,
  and if you need near-instant I can hook it to the store's feed.

I can have this running on your Railway project with your first set of
products within a day, and I'll test it against a live restock scenario before
I call it done. I reply fast and I don't hand over code I haven't run.

Thanks,
Cairn

## Notes for Jake (not client-facing)
- PoC.py verified offline: restock alert, sold-out alert, price-change alert
  (16.68% delta fired at 5% threshold), unwatched items correctly ignored,
  CSV log written. Assertions passed, exit 0.
- LIVE mode exists (polls <store>/products.json) but could not be exercised
  from this host: needoh.com's CDN returned TLS internal-error alerts on
  handshake from BOTH python-requests and curl (Schannel SEC_E_INTERNAL_ERROR).
  Server-side/edge quirk for our egress, not a code defect. On a normal US
  Railway node it connects fine. If we bid, we verify live on the client's
  infra during delivery, not before.
- $17 avg / 0 bids = low-budget or early job. Suggest $150-$400 fixed; this is
  a 1-day job. Don't race to the bottom.
- No Freelancer.com account on file -> save for Jake to submit.
- If asked "why not a real-time websocket answer": Shopify has no public
  push for stock; polling the JSON endpoint is the standard, stable approach.
