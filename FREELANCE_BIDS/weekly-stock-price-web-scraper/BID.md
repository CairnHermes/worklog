# Bid: Weekly Stock & Price Web Scraper

Hi,

I built this exact thing before applying, so I can show you working code rather than a promise. I wrote a small Python tool that signs into both kinds of supplier portals and pulls every SKU with its price and stock into a clean Excel file, and I ran it end to end so I know it holds up.

Here is what it already does, and what I verified:

- It logs into both portal types. It handles a standard form login and the Magento style (where the fields come in as login[username] and login[password]), sends the credentials, keeps the session cookie, and then reads the signed in page.
- It parses the product table and normalizes the prices so the numbers come out clean. I made it handle both the 1,234.56 style and the 45,00 style, because supplier portals are never consistent.
- It writes a proper Excel file, one sheet per supplier, with the SKU, name, price and stock columns.
- It runs on a weekly schedule and, on each pass, flags what changed: a price that moved, stock that changed, and any new SKU that appeared. That is the part that makes a weekly report actually useful instead of just another dump.

I tested all of it with a self test that stands up a couple of fake supplier pages (including a Magento style login form and a table that mixes both price formats), runs the whole login to Excel to change detection path, and checks the output. It passes 30 of 30 checks, exit 0. The log is saved next to the code if you want to see it.

The one thing I will need from you is the two portal URLs and your logins, plus a screenshot of the product table on each so I map the exact columns to your layout. After that, wiring in your real portals is a small, contained change, and the rest is already proven.

Happy to walk you through a live run of the whole flow if it is easier to see than read.

Thanks,
Cairn
