/**
 * mern-realtime-communication - PoC
 * ==================================
 * The "live connection layer" for a MERN real-time app, proven end to end
 * with zero third-party packages (pure Node: http, crypto, and Node's
 * built-in WebSocket client for the tests).
 *
 * What it implements (the parts a MERN stack's realtime layer needs first):
 *   - An RFC6455 WebSocket server (HTTP upgrade, handshake, masked-frame
 *     decode, close/ping/pong, fragmentation-safe buffering).
 *   - A message hub with:
 *       * rooms        - join / leave, per-room broadcast (no cross-room leak)
 *       * presence     - join / leave events fanned out to the room
 *       * typed messages - {type, payload, id} with a server-assigned id
 *       * delivery ack - sender can request an ack; every room member acks;
 *                        the server reports delivery fan-out (n of m received)
 *   - An in-memory pub/sub that a Mongoose/Express app would swap for a
 *     Redis adapter - the interface is the point of the PoC.
 *
 * The MERN bits (Mongo + Express) are ordinary and well known; the risk in a
 * "real-time communication app" is always the socket layer. That is what this
 * file proves: connect, join, broadcast, isolate, presence, ack - live.
 *
 * Run:
 *   node PoC.js --selftest        # spins up the server + 3 built-in WS clients
 *                                 # and asserts delivery, isolation, presence, ack
 *   node PoC.js --serve --port 8080   # run the hub as a standalone service
 *
 * No npm install. Node >= 21 (built-in global WebSocket).
 */
'use strict';

const http = require('http');
const crypto = require('crypto');

const WS_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11';

// --------------------------------------------------------------------------- #
// RFC6455 framing
// --------------------------------------------------------------------------- #
function encodeFrame(payload, opcode = 0x1) {
  const data = Buffer.isBuffer(payload) ? payload : Buffer.from(payload);
  const len = data.length;
  let header;
  if (len < 126) {
    header = Buffer.from([0x80 | opcode, len]);
  } else if (len < 65536) {
    header = Buffer.alloc(4);
    header[0] = 0x80 | opcode;
    header[1] = 126;
    header.writeUInt16BE(len, 2);
  } else {
    header = Buffer.alloc(10);
    header[0] = 0x80 | opcode;
    header[1] = 127;
    header.writeBigUInt64BE(BigInt(len), 2);
  }
  return Buffer.concat([header, data]);
}

// Parse as many complete frames as possible from a buffer. Returns the frames
// parsed and the leftover bytes (for partial frames / queued bytes).
function decodeFrames(buf) {
  const frames = [];
  let offset = 0;
  while (offset < buf.length) {
    if (buf.length - offset < 2) return { frames, rest: buf.slice(offset) };
    const b0 = buf[offset];
    const b1 = buf[offset + 1];
    const fin = (b0 & 0x80) !== 0;
    const opcode = b0 & 0x0f;
    const masked = (b1 & 0x80) !== 0;
    let len = b1 & 0x7f;
    let pos = offset + 2;
    if (len === 126) {
      if (buf.length < pos + 2) return { frames, rest: buf.slice(offset) };
      len = buf.readUInt16BE(pos);
      pos += 2;
    } else if (len === 127) {
      if (buf.length < pos + 8) return { frames, rest: buf.slice(offset) };
      len = Number(buf.readBigUInt64BE(pos));
      pos += 8;
    }
    let mask = null;
    if (masked) {
      if (buf.length < pos + 4) return { frames, rest: buf.slice(offset) };
      mask = buf.slice(pos, pos + 4);
      pos += 4;
    }
    if (buf.length < pos + len) return { frames, rest: buf.slice(offset) };
    let payload = buf.slice(pos, pos + len);
    if (masked) {
      const masked = Buffer.alloc(len);
      for (let i = 0; i < len; i++) masked[i] = payload[i] ^ mask[i % 4];
      payload = masked;
    }
    frames.push({ fin, opcode, payload });
    offset = pos + len;
  }
  return { frames, rest: Buffer.alloc(0) };
}

// --------------------------------------------------------------------------- #
// Connection (one client socket)
// --------------------------------------------------------------------------- #
class Connection {
  constructor(socket, id, hub) {
    this.socket = socket;
    this.id = id;
    this.hub = hub;
    this.room = null;
    this.closed = false;
    this.buffer = Buffer.alloc(0);

    socket.on('data', (chunk) => this._onData(chunk));
    socket.on('close', () => this.close());
    socket.on('error', (e) => { this.hub._log(`conn ${id} error: ${e.message}`); this.close(); });
  }

  send(obj) {
    if (this.closed) return;
    try {
      this.socket.write(encodeFrame(JSON.stringify(obj), 0x1));
    } catch (e) {
      this.hub._log(`send failed ${this.id}: ${e.message}`);
    }
  }

  close() {
    if (this.closed) return;
    this.closed = true;
    try { this.socket.destroy(); } catch (_) {}
    if (this.room) this.hub.leave(this);
    this.hub._remove(this.id, this);
  }

  _onData(chunk) {
    this.buffer = Buffer.concat([this.buffer, chunk]);
    for (;;) {
      const { frames, rest } = decodeFrames(this.buffer);
      if (frames.length === 0) { this.buffer = rest; return; }
      this.buffer = rest;
      for (const f of frames) this._handleFrame(f);
      if (this.closed) break;
    }
  }

  _handleFrame(f) {
    switch (f.opcode) {
      case 0x1: { // text
        let msg;
        try { msg = JSON.parse(f.payload.toString('utf8')); }
        catch (e) { this.send({ type: 'error', error: 'bad json' }); return; }
        this.hub.dispatch(this, msg);
        return;
      }
      case 0x8: // close
        this.close();
        return;
      case 0x9: // ping -> pong
        try { this.socket.write(encodeFrame(f.payload, 0xA)); } catch (_) {}
        return;
      case 0xA: // pong
        return;
      default:
        break;
    }
  }
}

// --------------------------------------------------------------------------- #
// Hub (rooms, presence, typed messages, delivery ack)
// --------------------------------------------------------------------------- #
class Hub {
  constructor(log = () => {}) {
    this.log = log;
    this.connections = new Map(); // id -> Connection
    this.rooms = new Map();       // room -> Set<Connection>
    this.pending = new Map();     // msgId -> {sender, room, expected, acks:Set}
    this.seq = 0;
  }
  _log(m) { this.log(m); }

  add(conn) { this.connections.set(conn.id, conn); }
  _remove(id, conn) { this.connections.delete(id); }

  nextId() { return `m_${++this.seq}`; }

  dispatch(conn, msg) {
    const type = msg.type;
    if (type === 'join') {
      this.join(conn, msg.room);
      return;
    }
    if (type === 'leave') {
      this.leave(conn, msg.room);
      return;
    }
    if (type === 'message' || type === 'request') {
      this.deliver(conn, msg);
      return;
    }
    if (type === 'ack') {
      this._handleAck(conn, msg);
      return;
    }
    conn.send({ type: 'error', error: `unknown type ${JSON.stringify(type)}` });
  }

  join(conn, room) {
    if (!room) { conn.send({ type: 'error', error: 'join needs room' }); return; }
    if (conn.room && conn.room !== room) this.leave(conn, conn.room);
    conn.room = room;
    if (!this.rooms.has(room)) this.rooms.set(room, new Set());
    const set = this.rooms.get(room);
    const wasEmpty = set.size === 0;
    set.add(conn);
    conn.send({ type: 'joined', room, members: [...set].map(c => c.id) });
    this._presence(room, 'joined', conn, wasEmpty);
  }

  leave(conn, room) {
    room = room || conn.room;
    if (!room) return;
    const set = this.rooms.get(room);
    if (!set) { conn.room = null; return; }
    set.delete(conn);
    if (set.size === 0) this.rooms.delete(room);
    conn.room = null;
    this._presence(room, 'left', conn, set.size === 0);
  }

  _presence(room, kind, conn, emptyAfter) {
    const set = this.rooms.get(room);
    const ev = { type: 'presence', room, kind, who: conn.id };
    if (emptyAfter) return; // room closed; no one left to notify
    for (const c of set) if (c !== conn) c.send(ev);
  }

  deliver(sender, msg) {
    const room = sender.room;
    if (!room) { sender.send({ type: 'error', error: 'not in a room' }); return; }
    const set = this.rooms.get(room) || new Set();
    const id = this.nextId();
    const envelope = {
      type: 'message',
      id,
      room,
      from: sender.id,
      at: new Date().toISOString(),
      payload: msg.payload,
      requires_ack: !!msg.requires_ack,
    };
    for (const c of set) c.send(envelope); // sender included (echo)
    this._log(`room ${room}: delivered ${id} from ${sender.id} to ${set.size} member(s)`);
    if (msg.requires_ack) {
      // Registry is hub-global and keyed by message id: any room member's ack
      // must count toward the originator, not just the sender's own socket.
      this.pending.set(id, { id, sender: sender.id, room, expected: set.size, acks: new Set() });
    }
  }

  _handleAck(conn, msg) {
    const entry = this.pending.get(msg.id);
    if (!entry) return; // not a pending ack we issued; ignore
    entry.acks.add(conn.id);
    if (entry.acks.size >= entry.expected) {
      const originator = this.connections.get(entry.sender);
      if (originator) {
        originator.send({ type: 'ack-complete', id: entry.id, room: entry.room,
                          delivered: entry.acks.size, expected: entry.expected });
      }
      this.pending.delete(entry.id);
    }
  }

  stats() {
    const rooms = {};
    for (const [r, set] of this.rooms) rooms[r] = set.size;
    return { connections: this.connections.size, rooms };
  }

  closeAll() {
    // Hard teardown: destroy every socket so the event loop can drain and the
    // process can exit (server.close() alone only stops new connections).
    for (const c of this.connections.values()) {
      try { c.socket.destroy(); } catch (_) {}
    }
    this.connections.clear();
    this.rooms.clear();
    this.pending.clear();
  }
}

// --------------------------------------------------------------------------- #
// WebSocket server over plain http
// --------------------------------------------------------------------------- #
function createServer(hub) {
  const server = http.createServer();
  let connSeq = 0;
  server.on('upgrade', (req, socket) => {
    const key = req.headers['sec-websocket-key'];
    if (!key || (req.headers['upgrade'] || '').toLowerCase() !== 'websocket') {
      socket.destroy();
      return;
    }
    const accept = crypto.createHash('sha1')
      .update(key + WS_GUID).digest('base64');
    socket.write(
      'HTTP/1.1 101 Switching Protocols\r\n' +
      'Upgrade: websocket\r\n' +
      'Connection: Upgrade\r\n' +
      `Sec-WebSocket-Accept: ${accept}\r\n\r\n`
    );
    socket.setNoDelay(true);
    const id = `c${++connSeq}`;
    const conn = new Connection(socket, id, hub);
    hub.add(conn);
    hub._log(`upgrade ${id}`);
  });
  return server;
}

// --------------------------------------------------------------------------- #
// Selftest: 3 built-in WebSocket clients exercise the live layer
// --------------------------------------------------------------------------- #
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function selftest() {
  const hub = new Hub((m) => console.log(m));
  const server = createServer(hub);
  await new Promise(res => server.listen(0, '127.0.0.1', res));
  const base = `ws://127.0.0.1:${server.address().port}`;

  const problems = [];
  const clients = [];

  async function connect() {
    const ws = new WebSocket(base);
    const events = [];
    ws.onmessage = (ev) => {
      const raw = typeof ev.data === 'string' ? ev.data : ev.data.toString();
      let m; try { m = JSON.parse(raw); } catch { return; }
      events.push(m);
    };
    clients.push(ws);
    const helper = {
      ws,
      events,
      send(obj) { ws.send(JSON.stringify(obj)); },
      until(pred, timeoutMs = 3000) {
        return new Promise((resolve, reject) => {
          const started = Date.now();
          const iv = setInterval(() => {
            const hit = events.find(pred);
            if (hit) { clearInterval(iv); resolve(hit); }
            else if (Date.now() - started > timeoutMs) {
              clearInterval(iv);
              reject(new Error(`timeout waiting for ${pred.toString()}`));
            }
          }, 20);
        });
      },
      close() { try { ws.close(); } catch (_) {} },
    };
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    return helper;
  }

  try {
    const A = await connect();
    const B = await connect();
    const C = await connect();
    hub._log('3 clients connected');

    // 1) A and B join "lobby"; C joins "trading-desk"
    A.send({ type: 'join', room: 'lobby' });
    B.send({ type: 'join', room: 'lobby' });
    C.send({ type: 'join', room: 'trading-desk' });
    await A.until(m => m.type === 'joined' && m.room === 'lobby');
    await B.until(m => m.type === 'joined' && m.room === 'lobby');
    await C.until(m => m.type === 'joined' && m.room === 'trading-desk');

    // 2) B's join should have produced a presence event for A (and vice versa).
    await A.until(m => m.type === 'presence' && m.kind === 'joined' && m.room === 'lobby');

    // 3) B broadcasts a message to "lobby". A (same room) must receive it.
    B.send({ type: 'message', room: 'lobby', payload: { text: 'hello from B' } });
    const gotA = await A.until(m => m.type === 'message' && m.payload && m.payload.text === 'hello from B');

    // 4) Cross-room isolation: C is in "trading-desk" and must NOT have seen it.
    await sleep(150);
    const cSawIt = C.events.some(m => m.type === 'message' && m.payload && m.payload.text === 'hello from B');
    if (cSawIt) problems.push('cross-room leak: C received lobby message');

    // 5) A broadcasts to lobby; C must not see it either.
    A.send({ type: 'message', room: 'lobby', payload: { text: 'hi lobby' } });
    await B.until(m => m.type === 'message' && m.payload && m.payload.text === 'hi lobby');
    await sleep(150);
    if (C.events.some(m => m.type === 'message' && m.payload && m.payload.text === 'hi lobby'))
      problems.push('cross-room leak #2: C received lobby message');

    // 6) Delivery ack: B sends a requires_ack message to lobby (A and B in room).
    //    A and B must each ack; the originator (B) gets ack-complete (2 of 2).
    B.send({ type: 'request', room: 'lobby', requires_ack: true, payload: { seq: 42 } });
    const reqB = await B.until(m => m.type === 'message' && m.requires_ack === true && m.id);
    A.send({ type: 'ack', id: reqB.id });
    B.send({ type: 'ack', id: reqB.id });
    const ackDone = await B.until(m => m.type === 'ack-complete' && m.id === reqB.id);
    if (ackDone.delivered !== 2 || ackDone.expected !== 2)
      problems.push(`ack-complete wrong: delivered=${ackDone.delivered} expected=${ackDone.expected}`);

    // 7) Presence on leave: A leaves lobby; B must get a 'left' event.
    A.send({ type: 'leave', room: 'lobby' });
    await B.until(m => m.type === 'presence' && m.kind === 'left' && m.room === 'lobby');

    // 8) Messages carry a server-assigned id + timestamp (ordering/identity).
    if (!gotA.id || !gotA.at) problems.push('message missing id/at');

    if (problems.length) {
      hub._log('SELFTEST FAIL: ' + problems.join('; '));
      for (const c of clients) c.close();
      server.close();
      return 1;
    }
    hub._log('SELFTEST PASS: 3 live WS clients - join/leave presence, ' +
             'lobby broadcast, cross-room isolation, typed messages with id, ' +
             `delivery ack (2 of 2). Server stats: ${JSON.stringify(hub.stats())}`);
    return 0;
  } catch (e) {
    hub._log(`SELFTEST ERROR: ${e.message}`);
    if (process.env.DEBUG_WS) {
      hub._log('  pending: ' + JSON.stringify([...hub.pending.keys()]));
      clients.forEach((c, i) =>
        hub._log(`  client${i}: ` + JSON.stringify(c.events.map(m => m.type + (m.id ? ':' + m.id : '')))));
    }
    for (const c of clients) c.close();
    server.close();
    return 1;
  } finally {
    for (const c of clients) c.close();
    hub.closeAll();
    server.close();
  }
}

function serve(port) {
  const hub = new Hub((m) => console.log(new Date().toISOString(), m));
  const server = createServer(hub);
  server.listen(port, '127.0.0.1', () => {
    console.log(`Realtime hub listening on ws://127.0.0.1:${port}`);
    console.log('protocol: {type:join|leave|message|request|ack, ...}');
  });
}

async function main(argv = process.argv.slice(2)) {
  if (argv.includes('--selftest')) {
    process.exitCode = await selftest();
  } else if (argv.includes('--serve')) {
    const i = argv.indexOf('--port');
    const port = i >= 0 ? Number(argv[i + 1]) : 8080;
    serve(port);
  } else {
    console.log('usage: node PoC.js --selftest   |   node PoC.js --serve --port 8080');
  }
}

main().catch(e => { console.error(e); process.exit(1); });
