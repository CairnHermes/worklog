# Bid - MERN Real-Time Communication App

Job: https://www.freelancer.com/projects/mern/mern-real-time-communication-app
Avg bid $253, 0 bids at scan time. Scope: MERN app where the "heart of the
project is real-time communication" and the client wants the live connection
layer architected and working flawlessly first, everything else to evolve.

## Bid text

Hi,

You said the live layer is the heart and needs to work flawlessly before
anything else. That's exactly how I'd build it, and it's the part I care most
about getting right, because the M, E, and N around it are the easy part once
the socket layer is solid.

Here's the design I'd lock in first:

- One WebSocket hub that owns all the live state. Clients connect, join a
  room, and everything in that room sees the message. Rooms are the unit of
  broadcast, so a chat in one channel never leaks into another, and the same
  hub scales to a hundred or a thousand connections.
- Presence built in: when someone joins or leaves a room, everyone in that
  room finds out immediately. That's the small detail that makes a realtime
  app feel alive, and it's easy to bolt on later and hard to retrofit.
- Typed messages with a server-assigned id and a timestamp, so your UI can
  order, dedupe, and retry correctly instead of guessing.
- Delivery acknowledgement. You can send a message that asks for an ack, and
  the hub reports back once every member in the room has actually received
  it. For anything where "sent" isn't the same as "delivered," that's the
  difference between a demo and a real system.
- The socket layer is the only place that talks to the network. Your Express
  routes and Mongoose models plug in behind it, and the message format is
  stable, so you can evolve the REST side without touching the live side.

I have a working prototype of this hub running on my machine right now. I
proved it live: three real clients connect over WebSockets, join a shared
room, a message broadcasts to everyone in the room and only to that room,
presence events fire on join and leave, and a delivery-ack message reports
back "2 of 2 received" once both members have it. I can walk you through the
protocol or fold it into your existing Express/Mongoose app in the first day.

A couple of practical notes:
- I'd keep the hub in-memory first (fast, simple, correct), with a clean
  interface so we can swap in a Redis adapter later the moment you need more
  than one server node. That's the standard path and I'd rather not over-build
  for one box.
- I'd include reconnection + a small backoff on the client, because network
  drops are a fact of life and a realtime app that hangs on them is a broken
  app.

Send me the one concrete use case (a chat room? a live update feed? a
collaboration canvas?) and I can have the live layer working end to end,
presence and acks included, so you can build the rest on a foundation that
doesn't move under you.

Fast replies, same-day fixes.

Best,
Jake

## Notes for Jake (not client-facing)
- PoC.js verified: `node PoC.js --selftest` exits 0. Pure Node (no npm
  install), uses Node's BUILT-IN global WebSocket client as the test peers.
  Implements a real RFC6455 server (HTTP upgrade, SHA-1 accept, masked-frame
  decode, close/ping/pong) + a hub: rooms, presence (join/leave fan-out),
  typed messages with server id + timestamp, cross-room isolation, and
  delivery-ack (originator gets ack-complete with delivered/expected counts).
  Selftest asserts all of it with 3 live clients over real sockets.
- Two real defects caught + fixed this run:
  (1) the ack registry was stored on the SENDER's connection only, so a peer's
      ack never registered and ack-complete never fired; moved it to a hub-
      global map keyed by message id (correct design).
  (2) the ack-complete frame was missing the message id (JSON dropped the
      undefined field) so clients couldn't correlate it; now stored + sent.
  Also added hub.closeAll() so the selftest process exits cleanly.
- Honest scope: this proves the realtime layer (the client's stated priority)
  standalone. It is a protocol + hub, not a finished MERN app; the bid is
  clear that Express/Mongoose plug in behind it and offers to fold it into
  their app. No claim that Mongo/Express are already wired.
- No Freelancer.com account on file -> save for Jake to submit.
- Bid sizing: $253 avg, 0 bids. Real-time layer + presence + acks is a
  meaningful build. Suggest $400-$700 fixed for the core live layer + first
  integration, or $30-$45/hr.