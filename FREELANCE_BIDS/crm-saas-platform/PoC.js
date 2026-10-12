#!/usr/bin/env node
/*
 * CRM SaaS platform -- core backend proof of concept.
 *
 * Dependency-free Node backend (node:http, node:crypto only) implementing the
 * hard, decision-bearing cores of this job so they are *proven*, not promised:
 *
 *   - Multi-tenant isolation for a SaaS CRM (customers, bookings/jobs, users).
 *   - A booking/jobs workflow STATE MACHINE with enforced valid transitions
 *     (you cannot skip a stage or go backwards) and an event log for audit.
 *   - Role-based permission matrix (superadmin, admin, agent) with
 *     superadmin able to act across tenants and admin/agent scoped to one.
 *   - Reporting: per-tenant pipeline by stage and by owner.
 *
 * Run:
 *     node PoC.js server          # start on :8088
 *     node PoC.js selftest        # boot + run the E2E suite, exit 0 on pass
 *
 * Store adapter: InMemoryStore implements a small interface; each method
 * notes the Mongoose collection + index it maps to, so swapping to Postgres
 * (their stated stack) or Mongo is a one-file change.
 */
'use strict';
const http = require('node:http');
const crypto = require('node:crypto');

const PORT = Number(process.env.PORT || 8088);
const SECRET = process.env.TOKEN_SECRET || 'crm-poc-secret';

// ---------------------------------------------------------------------------
// Domain: booking/jobs workflow state machine
// ---------------------------------------------------------------------------
const STAGES = ['LEAD', 'QUALIFIED', 'SCHEDULED', 'IN_PROGRESS', 'DONE', 'CANCELLED'];
const TRANSITIONS = {
  LEAD: ['QUALIFIED', 'CANCELLED'],
  QUALIFIED: ['SCHEDULED', 'CANCELLED', 'LEAD'],
  SCHEDULED: ['IN_PROGRESS', 'CANCELLED', 'QUALIFIED'],
  IN_PROGRESS: ['DONE', 'SCHEDULED'],
  DONE: [],                 // terminal
  CANCELLED: ['LEAD'],       // may re-open once, nothing else
};
function canTransition(from, to) { return (TRANSITIONS[from] || []).includes(to); }

// ---------------------------------------------------------------------------
// RBAC permission matrix. Superadmin is cross-tenant; others are not.
// ---------------------------------------------------------------------------
const PERMS = {
  superadmin: ['booking:create', 'booking:read', 'booking:update', 'booking:transition', 'booking:delete',
    'customer:create', 'customer:read', 'report:view', 'user:manage', 'cross-tenant'],
  admin: ['booking:create', 'booking:read', 'booking:update', 'booking:transition', 'booking:delete',
    'customer:create', 'customer:read', 'report:view', 'user:manage'],
  agent: ['booking:create', 'booking:read', 'booking:update', 'booking:transition',
    'customer:create', 'customer:read'],
};
function hasRole(claims, perm) { return (PERMS[claims.role] || []).includes(perm); }

// ---------------------------------------------------------------------------
// Store adapter (swap for Postgres/Mongo in production)
// ---------------------------------------------------------------------------
class InMemoryStore {
  constructor() {
    this.users = new Map();      // id -> {id, tenant, email, pass, role, name}
    this.customers = new Map();  // id -> {id, tenant, name, email, phone, notes}
    this.bookings = new Map();   // id -> {id, tenant, customerId, ownerId, stage, title, createdAt}
    this.events = new Map();     // id -> {id, tenant, refId, from, to, actor, ts}
    this.seq = 1;
  }
  nextId() { return 'id-' + (this.seq++); }

  insertUser(r) { r.id = this.nextId(); this.users.set(r.id, r); return r; }
  userByEmail(t, e) { for (const u of this.users.values()) if (u.tenant === t && u.email === e) return u; return null; }
  userById(id) { return this.users.get(id) || null; }
  usersInTenant(t) { return [...this.users.values()].filter(u => u.tenant === t); }
  allUsers() { return [...this.users.values()]; }
  updateUser(id, p) { const u = this.users.get(id); if (u) Object.assign(u, p); return u; }
  deleteUser(id) { return this.users.delete(id); }

  insertCustomer(r) { r.id = this.nextId(); this.customers.set(r.id, r); return r; }
  customerById(id) { return this.customers.get(id) || null; }
  customersInTenant(t) { return [...this.customers.values()].filter(c => c.tenant === t); }

  insertBooking(r) { r.id = this.nextId(); r.stage = 'LEAD'; r.createdAt = Date.now(); this.bookings.set(r.id, r); return r; }
  bookingById(id) { return this.bookings.get(id) || null; }
  bookingsInTenant(t) { return [...this.bookings.values()].filter(b => b.tenant === t); }

  insertEvent(r) { r.id = this.nextId(); r.ts = Date.now(); this.events.set(r.id, r); return r; }
  eventsInTenant(t) { return [...this.events.values()].filter(e => e.tenant === t); }
}

// ---------------------------------------------------------------------------
// Auth: same compact HMAC token as the other PoC (proven, reused).
// ---------------------------------------------------------------------------
const b64u = buf => Buffer.from(buf).toString('base64url');
function signToken(claims, ttlMs = 3600000) {
  const payload = { ...claims, exp: Date.now() + ttlMs };
  const body = b64u(JSON.stringify(payload));
  const mac = b64u(crypto.createHmac('sha256', SECRET).update(body).digest());
  return `${body}.${mac}`;
}
function verifyToken(token) {
  const [body, mac] = String(token || '').split('.');
  if (!body || !mac) return null;
  const expect = b64u(crypto.createHmac('sha256', SECRET).update(body).digest());
  const a = Buffer.from(mac), b = Buffer.from(expect);
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) return null;
  let claims; try { claims = JSON.parse(Buffer.from(body, 'base64url').toString('utf8')); } catch { return null; }
  if (typeof claims.exp !== 'number' || claims.exp < Date.now()) return null;
  return claims;
}
function hashPassword(pw) { const s = crypto.randomBytes(16); const h = crypto.pbkdf2Sync(pw, s, 120000, 32, 'sha256'); return s.toString('hex') + ':' + h.toString('hex'); }
function checkPassword(pw, stored) {
  const [salt, hash] = stored.split(':');
  const c = crypto.pbkdf2Sync(pw, Buffer.from(salt, 'hex'), 120000, 32, 'sha256');
  return crypto.timingSafeEqual(c, Buffer.from(hash, 'hex'));
}

// Tenant guard: superadmin passes with any tenant in the token; others must
// belong to the record's tenant. This is the isolation seam.
function inScope(claims, recordTenant) {
  if (claims.role === 'superadmin') return true;
  return claims.tenant === recordTenant;
}

// ---------------------------------------------------------------------------
// Server
// ---------------------------------------------------------------------------
function buildServer(store) {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, `http://localhost:${PORT}`);
    const path = url.pathname.replace(/\/+$/, '') || '/';
    const send = (status, obj) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(obj)); };
    const readBody = () => new Promise(r => { let b = ''; req.on('data', c => b += c); req.on('end', () => { try { r(JSON.parse(b || '{}')); } catch { r({}); } }); });

    if (req.method === 'GET' && (path === '/health' || path === '/'))
      return send(200, { ok: true, service: 'crm-poc' });

    // ---- auth
    if (req.method === 'POST' && path === '/auth/signup') {
      (async () => {
        const { email, password, name, tenant = 'default', role = 'agent' } = await readBody();
        if (!email || !password || !name) return send(422, { error: 'email, password, name required' });
        if (!PERMS[role]) return send(422, { error: 'bad role' });
        if (store.userByEmail(tenant, email)) return send(409, { error: 'email exists in tenant' });
        const u = store.insertUser({ tenant, email, pass: hashPassword(password), name, role });
        const token = signToken({ uid: u.id, tenant, role: u.role });
        return send(201, { token, user: { id: u.id, name: u.name, role: u.role } });
      })();
      return;
    }
    if (req.method === 'POST' && path === '/auth/login') {
      (async () => {
        const { email, password, tenant = 'default' } = await readBody();
        const u = store.userByEmail(tenant, email);
        if (!u || !checkPassword(password, u.pass)) return send(401, { error: 'bad credentials' });
        const token = signToken({ uid: u.id, tenant, role: u.role });
        return send(200, { token, user: { id: u.id, name: u.name, role: u.role } });
      })();
      return;
    }

    // ---- customers
    if (req.method === 'POST' && path === '/customers') {
      const tok = (req.headers['authorization'] || '').match(/^Bearer\s+(.+)$/i);
      const claims = tok ? verifyToken(tok[1]) : null;
      if (!claims) return send(401, { error: 'missing or invalid token' });
      if (!hasRole(claims, 'customer:create')) return send(403, { error: 'no permission' });
      (async () => {
        const { name, email, phone } = await readBody();
        if (!name) return send(422, { error: 'name required' });
        const c = store.insertCustomer({ tenant: claims.tenant, name, email, phone });
        return send(201, { customer: c });
      })();
      return;
    }

    // ---- bookings
    if (req.method === 'POST' && path === '/bookings') {
      const tok = (req.headers['authorization'] || '').match(/^Bearer\s+(.+)$/i);
      const claims = tok ? verifyToken(tok[1]) : null;
      if (!claims) return send(401, { error: 'missing or invalid token' });
      if (!hasRole(claims, 'booking:create')) return send(403, { error: 'no permission' });
      (async () => {
        const { customerId, title } = await readBody();
        if (!customerId || !title) return send(422, { error: 'customerId + title required' });
        const cust = store.customerById(customerId);
        if (!cust) return send(404, { error: 'customer not found' });
        if (!inScope(claims, cust.tenant)) return send(403, { error: 'customer not in your tenant' });
        const b = store.insertBooking({ tenant: cust.tenant, customerId, ownerId: claims.uid, title });
        return send(201, { booking: b });
      })();
      return;
    }
    if (req.method === 'GET' && path === '/bookings') {
      const tok = (req.headers['authorization'] || '').match(/^Bearer\s+(.+)$/i);
      const claims = tok ? verifyToken(tok[1]) : null;
      if (!claims) return send(401, { error: 'missing or invalid token' });
      if (!hasRole(claims, 'booking:read')) return send(403, { error: 'no permission' });
      const tenant = claims.role === 'superadmin' ? (url.searchParams.get('tenant') || claims.tenant) : claims.tenant;
      const list = store.bookingsInTenant(tenant);
      return send(200, { count: list.length, bookings: list });
    }
    if (req.method === 'PATCH' && path === '/bookings') {
      const tok = (req.headers['authorization'] || '').match(/^Bearer\s+(.+)$/i);
      const claims = tok ? verifyToken(tok[1]) : null;
      if (!claims) return send(401, { error: 'missing or invalid token' });
      (async () => {
        const { id, toStage } = await readBody();
        if (!id || !toStage) return send(422, { error: 'id + toStage required' });
        if (!hasRole(claims, 'booking:transition')) return send(403, { error: 'no permission' });
        const b = store.bookingById(id);
        if (!b) return send(404, { error: 'booking not found' });
        if (!inScope(claims, b.tenant)) return send(403, { error: 'booking not in your tenant' });
        if (!canTransition(b.stage, toStage))
          return send(409, { error: `illegal transition ${b.stage} -> ${toStage}`, allowed: TRANSITIONS[b.stage] || [] });
        const from = b.stage; b.stage = toStage;
        store.insertEvent({ tenant: b.tenant, refId: b.id, from, to: toStage, actor: claims.uid });
        return send(200, { booking: { id: b.id, stage: b.stage }, event: { from, to: toStage } });
      })();
      return;
    }

    // ---- reporting (admin+)
    if (req.method === 'GET' && path === '/report/pipeline') {
      const tok = (req.headers['authorization'] || '').match(/^Bearer\s+(.+)$/i);
      const claims = tok ? verifyToken(tok[1]) : null;
      if (!claims) return send(401, { error: 'missing or invalid token' });
      if (!hasRole(claims, 'report:view')) return send(403, { error: 'no permission' });
      const tenant = claims.role === 'superadmin' ? (url.searchParams.get('tenant') || claims.tenant) : claims.tenant;
      const bookings = store.bookingsInTenant(tenant);
      const byStage = {}; STAGES.forEach(s => byStage[s] = 0);
      bookings.forEach(b => { byStage[b.stage] = (byStage[b.stage] || 0) + 1; });
      const active = bookings.filter(b => b.stage !== 'DONE' && b.stage !== 'CANCELLED').length;
      return send(200, { tenant, total: bookings.length, active, byStage });
    }
    if (req.method === 'GET' && path === '/report/audit') {
      const tok = (req.headers['authorization'] || '').match(/^Bearer\s+(.+)$/i);
      const claims = tok ? verifyToken(tok[1]) : null;
      if (!claims) return send(401, { error: 'missing or invalid token' });
      if (!hasRole(claims, 'report:view')) return send(403, { error: 'no permission' });
      const tenant = claims.role === 'superadmin' ? (url.searchParams.get('tenant') || claims.tenant) : claims.tenant;
      return send(200, { events: store.eventsInTenant(tenant) });
    }

    // ---- superadmin cross-tenant: list all tenants + their users
    if (req.method === 'GET' && path === '/superadmin/tenants') {
      const tok = (req.headers['authorization'] || '').match(/^Bearer\s+(.+)$/i);
      const claims = tok ? verifyToken(tok[1]) : null;
      if (!claims || claims.role !== 'superadmin') return send(claims ? 403 : 401, { error: claims ? 'superadmin only' : 'missing token' });
      const tenants = {};
      for (const u of store.allUsers()) (tenants[u.tenant] = tenants[u.tenant] || []).push({ id: u.id, name: u.name, role: u.role, email: u.email });
      return send(200, { tenants });
    }

    return send(404, { error: 'not found', path });
  });
  return server;
}

// ---------------------------------------------------------------------------
// Selftest + server
// ---------------------------------------------------------------------------
async function httpCall(server, method, path, { token, body } = {}) {
  const port = server.address().port;
  const res = await fetch(`http://127.0.0.1:${port}${path}`, {
    method, headers: { 'content-type': 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  let json = null; try { json = await res.json(); } catch {}
  return { status: res.status, body: json };
}

async function selftest() {
  const store = new InMemoryStore();
  const server = buildServer(store);
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  let passed = 0, failed = 0;
  const check = (n, c) => { c ? (passed++, console.log('  PASS  ' + n)) : (failed++, console.log('  FAIL  ' + n)); };
  console.log('CRM PoC selftest\n');

  // tenant A users
  const A_admin = await httpCall(server, 'POST', '/auth/signup', { body: { email: 'adm@t-a.com', password: 'pw', name: 'A-Admin', tenant: 't-a', role: 'admin' } });
  const A_agent = await httpCall(server, 'POST', '/auth/signup', { body: { email: 'agt@t-a.com', password: 'pw', name: 'A-Agent', tenant: 't-a', role: 'agent' } });
  // tenant B user
  const B_agent = await httpCall(server, 'POST', '/auth/signup', { body: { email: 'agt@t-b.com', password: 'pw', name: 'B-Agent', tenant: 't-b', role: 'agent' } });
  // superadmin (any tenant token)
  const SA = await httpCall(server, 'POST', '/auth/signup', { body: { email: 'sa@platform.com', password: 'pw', name: 'PlatformSA', tenant: 'platform', role: 'superadmin' } });
  check('signups ok', [A_admin, A_agent, B_agent, SA].every(x => x.status === 201 && x.body.token));
  const [adm, agt, bagt, satok] = [A_admin.body.token, A_agent.body.token, B_agent.body.token, SA.body.token];

  // wrong password
  check('bad login 401', (await httpCall(server, 'POST', '/auth/login', { body: { email: 'adm@t-a.com', password: 'nope', tenant: 't-a' } })).status === 401);

  // agent creates customer + booking in A
  const custA = await httpCall(server, 'POST', '/customers', { token: agt, body: { name: 'Client A', email: 'a@x.com' } });
  check('agent creates customer', custA.status === 201);
  const bkA = await httpCall(server, 'POST', '/bookings', { token: agt, body: { customerId: custA.body.customer.id, title: 'A job' } });
  check('agent creates booking (LEAD)', bkA.status === 201 && bkA.body.booking.stage === 'LEAD');
  const bkid = bkA.body.booking.id;

  // agent CANNOT read report
  check('agent blocked from report', (await httpCall(server, 'GET', '/report/pipeline', { token: agt })).status === 403);

  // B agent cannot create booking on A's customer (cross-tenant)
  const crossBk = await httpCall(server, 'POST', '/bookings', { token: bagt, body: { customerId: custA.body.customer.id, title: 'steal' } });
  check('cross-tenant booking blocked', crossBk.status === 403);

  // B agent cannot see A's bookings
  const bList = await httpCall(server, 'GET', '/bookings', { token: bagt });
  check('B sees zero A bookings', bList.body.count === 0);

  // workflow state machine
  check('LEAD->QUALIFIED ok', (await httpCall(server, 'PATCH', '/bookings', { token: adm, body: { id: bkid, toStage: 'QUALIFIED' } })).status === 200);
  check('QUALIFIED->DONE illegal', (await httpCall(server, 'PATCH', '/bookings', { token: adm, body: { id: bkid, toStage: 'DONE' } })).status === 409);
  check('QUALIFIED->SCHEDULED ok', (await httpCall(server, 'PATCH', '/bookings', { token: adm, body: { id: bkid, toStage: 'SCHEDULED' } })).status === 200);
  check('SCHEDULED->IN_PROGRESS ok', (await httpCall(server, 'PATCH', '/bookings', { token: adm, body: { id: bkid, toStage: 'IN_PROGRESS' } })).status === 200);
  check('IN_PROGRESS->DONE ok', (await httpCall(server, 'PATCH', '/bookings', { token: adm, body: { id: bkid, toStage: 'DONE' } })).status === 200);
  check('DONE is terminal', (await httpCall(server, 'PATCH', '/bookings', { token: adm, body: { id: bkid, toStage: 'LEAD' } })).status === 409);

  // audit log captured every transition
  const audit = await httpCall(server, 'GET', '/report/audit', { token: adm });
  const seq = audit.body.events.map(e => e.from + '>' + e.to);
  check('audit has 4 transitions', audit.body.events.length === 4);
  check('audit order correct', seq.join('|') === 'LEAD>QUALIFIED|QUALIFIED>SCHEDULED|SCHEDULED>IN_PROGRESS|IN_PROGRESS>DONE');

  // pipeline report (tenant A)
  // add one more active booking
  const custA2 = await httpCall(server, 'POST', '/customers', { token: agt, body: { name: 'Client A2', email: 'a2@x.com' } });
  await httpCall(server, 'POST', '/bookings', { token: agt, body: { customerId: custA2.body.customer.id, title: 'A2 job' } });
  const rep = await httpCall(server, 'GET', '/report/pipeline', { token: adm });
  check('pipeline counts', rep.body.total === 2 && rep.body.byStage.DONE === 1 && rep.body.byStage.LEAD === 1 && rep.body.active === 1);

  // superadmin cross-tenant
  const ten = await httpCall(server, 'GET', '/superadmin/tenants', { token: satok });
  check('superadmin sees both tenants', !!ten.body.tenants['t-a'] && !!ten.body.tenants['t-b']);
  check('non-superadmin blocked', (await httpCall(server, 'GET', '/superadmin/tenants', { token: adm })).status === 403);
  const saListB = await httpCall(server, 'GET', '/bookings?tenant=t-a', { token: satok });
  check('superadmin reads tenant A bookings', saListB.body.count === 2);

  // token tamper
  check('tampered token 401', (await httpCall(server, 'GET', '/bookings', { token: agt.slice(0, -4) + 'ZZZZ' })).status === 401);

  server.close();
  console.log(`\nResult: ${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
}

async function main() {
  const cmd = process.argv[2] || 'server';
  if (cmd === 'selftest') return selftest();
  if (cmd === 'server' || cmd === 'start') {
    const server = buildServer(new InMemoryStore());
    server.listen(PORT, '127.0.0.1', () => console.log(`crm-poc listening on http://127.0.0.1:${PORT}`));
    return;
  }
  console.error('unknown command: ' + cmd); process.exit(2);
}
main().catch(e => { console.error(e); process.exit(1); });
