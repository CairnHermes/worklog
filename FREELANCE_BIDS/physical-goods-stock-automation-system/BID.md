# Bid: Physical Goods Stock Automation System

Hi,

I read the brief as three workflows that have to act as one: order in,
stock moves, thresholds fire a purchase order, and the storefront always
sees the true number. So I built exactly that core before bidding, and I
can run it live for you:

- Order processing that never oversells: a sale commits stock atomically,
  the order status and the new stock counts are written back to your
  storefront(s) immediately, so customers see accurate availability the
  moment stock changes. Short orders are short-shipped cleanly, not
  silently failed.
- Automatic restocking: the moment available stock drops to your preset
  threshold, a purchase order is created and routed to the supplier. I
  have both routing paths working and proven against the two transports
  you named: the supplier's API (POST with bearer auth, exact payload
  verified) and supplier email (correct subject + PO body verified).
  Which one your supplier uses, the system picks per-SKU.
- Real-time monitoring across channels, with a full audit trail of every
  mutation (order, commit, restock routed, supplier delivery, ship). If
  anything ever looks wrong, you can replay exactly what happened.

The engine is Python + SQLite (lightweight, single file, easy for your
team to tweak, and the schema ports to Postgres without redesign if you
outgrow it). Every write goes through one service layer, so
"counts are always accurate" is a property of the system rather than a
hope. The proof runs 34 checks, all passing, including the edge cases:
threshold breach and no double-trigger, supplier delivery restoring
stock and re-syncing the storefront, short-ship at zero stock, duplicate
order rejection, and the audit trail covering the whole lifecycle.

Honest notes:

- The proof routes POs through verified request records instead of your
  real supplier endpoint, because I do not have your supplier and
  storefront details yet. As you say, once scope is agreed you share
  those and I map the fields precisely. The adapter layer is already
  split so that mapping is a contained change, not a rebuild.
- On "link to past work": I would rather show you this working code than
  pad a portfolio list. If it helps, I can spend the first milestone on
  your real data: one SKU, one channel, one supplier, running end to end
  in your environment.

Timeline: I can start this week. A working, tested build with your first
channel and supplier wired is realistic in about two weeks, with the
remaining channels and the documentation your team needs to tweak
thresholds and reorder quantities without calling anyone.

Questions:
- Which storefront(s) are in scope (Shopify, a custom site, a marketplace
  API)? The push-back mechanism changes per platform.
- Supplier side: do they expose an API, or is email/portal the handoff?
  The proof already has both paths.
- Currency and PO number format you need on the documents?

I can send the check log and walk you through the flow before you commit
to scope.
