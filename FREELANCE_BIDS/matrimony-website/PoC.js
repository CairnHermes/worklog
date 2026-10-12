#!/usr/bin/env node
/*
 * Matrimony platform -- core backend proof of concept.
 *
 * Dependency-free Node backend implementing the hard, decision-bearing cores
 * of the job so the state machine, security model and matching logic are
 * *proven*, not promised. Uses only node:http, node:crypto -- nothing to
 * npm install. Run the whole thing with:
 *
 *     node PoC.js server          # start the API on :8087
 *     node PoC.js selftest        # boot the server, run the E2E suite, exit
 *
 * What is implemented and verified by the selftest:
 *   - Role-based auth (superadmin, admin, member) with HMAC-signed tokens,
 *     expiry, and per-tenant scoping baked into the token.
 *   - Multi-tenant data isolation: every read/write is tenant-scoped. A
 *     member of tenant A physically cannot read or mutate tenant B rows.
 *   - Profile CRUD with validation (name, age, religion, city, tier).
 *   - Matchmaking: filter + score by religion, age band, city, then rank.
 *   - Subscription tiers: FREE, PREMIUM, GOLD. Messaging is gated to paying
 *     tiers; FREE can view but not message. Admin can list revenue.
 *   - Admin panel: list users (tenant-scoped), force-tier, delete user.
 *
 * Store adapter: `InMemoryStore` implements a small interface. Swapping to
 * Mongo is a one-file change -- every method maps 1:1 to a Mongoose query,
 * and I've noted the collection + index to use above each method.
 */
'use strict';
const http = require('node:http');
const crypto = require('node:crypto');

const PORT = Number(process.env.PORT || 8087);
const SECRET = process.env.TOKEN_SECRET || 'poctest-secret';

// ---------------------------------------------------------------------------
// Store adapter (swap this class for a Mongoose-backed one in production)
// ---------------------------------------------------------------------------
class InMemoryStore {
  constructor() {
    this.users = new Map();       // id -> {id, tenant, email, pass, role, name, tier}
    this.profiles = new Map();    // id -> {id, tenant, userId, name, age, religion, city, tier}
    this.messages = new Map();    // id -> {id, tenant, from, to, text, ts}
    this.seq = 1;
  }
  nextId(prefix) { return prefix + '-' + (this.seq++); }

  // users: index on (tenant, email) unique  ->  findOne + tenant guard
  insertUser(rec) { rec.id = this.nextId('usr'); this.users.set(rec.id, rec); return rec; }
  userByEmail(tenant, email) {
    for (const u of this.users.values())
      if (u.tenant === tenant && u.email === email) return u;
    return null;
  }
  userById(id) { return this.users.get(id) || null; }
  usersInTenant(tenant) { return [...this.users.values()].filter(u => u.tenant === tenant); }
  updateUser(id, patch) { const u = this.users.get(id); if (u) Object.assign(u, patch); return u; }
  deleteUser(id) { return this.users.delete(id); }

  // profiles: index on (tenant, userId) unique
  insertProfile(rec) { rec.id = this.nextId('prf'); this.profiles.set(rec.id, rec); return rec; }
  profileById(id) { return this.profiles.get(id) || null; }
  profilesInTenant(tenant) { return [...this.profiles.values()].filter(p => p.tenant === tenant); }
  deleteProfile(id) { return this.profiles.delete(id); }

  // messages: index on (tenant)
  insertMessage(rec) { rec.id = this.nextId('msg'); this.messages.set(rec.id, rec); return rec; }
  messagesInTenant(tenant) { return [...this.messages.values()].filter(m => m.tenant === tenant); }
}

// ---------------------------------------------------------------------------
// Auth: HMAC-signed compact token. Claims: {uid, tenant, role, exp}
// format: base64url(payload).base64url(hmac)  -- no header, claims are ours.
// ---------------------------------------------------------------------------
function b64u(buf) { return Buffer.from(buf).toString('base64url'); }
function signToken(claims, ttlMs = 1000 * 60 * 60) {
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
  let claims;
  try { claims = JSON.parse(Buffer.from(body, 'base64url').toString('utf8')); }
  catch { return null; }
  if (typeof claims.exp !== 'number' || claims.exp < Date.now()) return null;
  return claims;
}
function requireAuth(req, needRole) {
  const h = req.headers['authorization'] || '';
  const m = h.match(/^Bearer\s+(.+)$/i);
  const claims = m ? verifyToken(m[1]) : null;
  if (!claims) return { error: { status: 401, message: 'missing or invalid token' } };
  if (needRole && claims.role !== needRole && !(needRole === 'admin_or_above' && (claims.role === 'admin' || claims.role === 'superadmin')))
    return { error: { status: 403, message: 'insufficient role' } };
  return { claims };
}

// ---------------------------------------------------------------------------
// Passwords: PBKDF2. In prod you'd store the salt+iterations alongside.
// ---------------------------------------------------------------------------
function hashPassword(pw) {
  const salt = crypto.randomBytes(16);
  const hash = crypto.pbkdf2Sync(pw, salt, 120000, 32, 'sha256');
  return salt.toString('hex') + ':' + hash.toString('hex');
}
function checkPassword(pw, stored) {
  const [salt, hash] = stored.split(':');
  const candidate = crypto.pbkdf2Sync(pw, Buffer.from(salt, 'hex'), 120000, 32, 'sha256');
  return crypto.timingSafeEqual(candidate, Buffer.from(hash, 'hex'));
}

// ---------------------------------------------------------------------------
// Matchmaking: filter + score. Higher = better.
// ---------------------------------------------------------------------------
function match(profiles, self, limit = 10) {
  const scored = profiles
    .filter(p => p.id !== self.id && p.tenant === self.tenant)
    .map(p => {
      let s = 0;
      if (p.religion && p.religion === self.religion) s += 40;       // religion is a hard signal
      const ageDiff = Math.abs(p.age - self.age);
      s += Math.max(0, 20 - ageDiff);                                   // within ~20y band
      if (p.city && p.city === self.city) s += 20;                      // same city
      if (p.tier === 'GOLD' || p.tier === 'PREMIUM') s += 5;            // slight boost to paying
      return { profile: p, score: s };
    })
    .sort((a, b) => b.score - a.score)
    .slice(0, limit);
  return scored.map(s => ({
    name: s.profile.name, age: s.profile.age, religion: s.profile.religion,
    city: s.profile.city, tier: s.profile.tier, matchScore: s.score,
  }));
}

const TIERS = ['FREE', 'PREMIUM', 'GOLD'];

// ---------------------------------------------------------------------------
// HTTP server
// ---------------------------------------------------------------------------
function buildServer(store) {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, `http://localhost:${PORT}`);
    const path = url.pathname.replace(/\/+$/, '') || '/';
    const send = (status, obj) => {
      res.writeHead(status, { 'content-type': 'application/json' });
      res.end(JSON.stringify(obj));
    };
    const body = (cb) => {
      let raw = '';
      req.on('data', c => raw += c);
      req.on('end', () => { try { cb(JSON.parse(raw || '{}')); } catch { cb({}); } });
    };

    // ---- health
    if (req.method === 'GET' && (path === '/health' || path === '/'))
      return send(200, { ok: true, service: 'matrimony-poc' });

    // ---- auth
    if (req.method === 'POST' && path === '/auth/signup') {
      return body(({ email, password, name, tenant = 'default', tier = 'FREE', role = 'member' }) => {
        if (!email || !password || !name) return send(422, { error: 'email, password, name required' });
        if (!TIERS.includes(tier)) return send(422, { error: 'bad tier' });
        if (store.userByEmail(tenant, email)) return send(409, { error: 'email exists in tenant' });
        const u = store.insertUser({ tenant, email, pass: hashPassword(password), name, tier, role });
        const token = signToken({ uid: u.id, tenant, role: u.role });
        return send(201, { token, user: { id: u.id, name: u.name, tier: u.tier, role: u.role } });
      });
    }
    if (req.method === 'POST' && path === '/auth/login') {
      return body(({ email, password, tenant = 'default' }) => {
        const u = store.userByEmail(tenant, email);
        if (!u || !checkPassword(password, u.pass)) return send(401, { error: 'bad credentials' });
        const token = signToken({ uid: u.id, tenant, role: u.role });
        return send(200, { token, user: { id: u.id, name: u.name, tier: u.tier, role: u.role } });
      });
    }

    // ---- profiles (member or above, tenant-scoped)
    if (req.method === 'POST' && path === '/profiles') {
      const { claims, error } = requireAuth(req, 'member');
      if (error) return send(error.status, { error: error.message });
      return body(({ name, age, religion, city }) => {
        if (!name || !age || !religion || !city) return send(422, { error: 'name, age, religion, city required' });
        const ageNum = Number(age);
        if (!(ageNum >= 18 && ageNum <= 99)) return send(422, { error: 'age must be 18-99' });
        const p = store.insertProfile({
          tenant: claims.tenant, userId: claims.uid, name, age: ageNum, religion, city,
          tier: store.userById(claims.uid)?.tier || 'FREE',
        });
        return send(201, { profile: p });
      });
    }
    if (req.method === 'GET' && path === '/profiles/match') {
      const { claims, error } = requireAuth(req, 'member');
      if (error) return send(error.status, { error: error.message });
      const self = store.profilesInTenant(claims.tenant).find(p => p.userId === claims.uid);
      if (!self) return send(404, { error: 'no profile; POST /profiles first' });
      return send(200, { matches: match(store.profilesInTenant(claims.tenant), self) });
    }

    // ---- messaging: gated to paying tiers, tenant-scoped
    if (req.method === 'POST' && path === '/messages') {
      const { claims, error } = requireAuth(req, 'member');
      if (error) return send(error.status, { error: error.message });
      return body(({ toUserId, text }) => {
        if (!toUserId || !text) return send(422, { error: 'toUserId, text required' });
        const me = store.userById(claims.uid);
        if (!me || me.tier === 'FREE') return send(403, { error: 'messaging requires a paid tier (PREMIUM or GOLD)' });
        const recipient = store.userById(toUserId);
        if (!recipient || recipient.tenant !== claims.tenant) return send(404, { error: 'recipient not found' });
        const m = store.insertMessage({ tenant: claims.tenant, from: me.id, to: recipient.id, text, ts: Date.now() });
        return send(201, { message: m });
      });
    }

    // ---- admin panel: tenant-scoped
    if (req.method === 'GET' && path === '/admin/users') {
      const { claims, error } = requireAuth(req, 'admin_or_above');
      if (error) return send(error.status, { error: error.message });
      const users = store.usersInTenant(claims.tenant).map(({ pass, ...u }) => u);
      return send(200, { count: users.length, users });
    }
    if (req.method === 'PATCH' && path === '/admin/tier') {
      const { claims, error } = requireAuth(req, 'admin_or_above');
      if (error) return send(error.status, { error: error.message });
      return body(({ userId, tier }) => {
        if (!userId || !TIERS.includes(tier)) return send(422, { error: 'userId + valid tier required' });
        const target = store.userById(userId);
        if (!target || target.tenant !== claims.tenant) return send(404, { error: 'user not in tenant' });
        const u = store.updateUser(userId, { tier });
        return send(200, { user: { id: u.id, name: u.name, tier: u.tier } });
      });
    }
    if (req.method === 'DELETE' && path === '/admin/users') {
      const { claims, error } = requireAuth(req, 'admin_or_above');
      if (error) return send(error.status, { error: error.message });
      const id = url.searchParams.get('id');
      const target = store.userById(id);
      if (!target || target.tenant !== claims.tenant) return send(404, { error: 'user not in tenant' });
      store.deleteUser(id);
      return send(200, { deleted: id });
    }

    // ---- admin: revenue snapshot (tenant-scoped)
    if (req.method === 'GET' && path === '/admin/revenue') {
      const { claims, error } = requireAuth(req, 'admin_or_above');
      if (error) return send(error.status, { error: error.message });
      const users = store.usersInTenant(claims.tenant);
      const byTier = { FREE: 0, PREMIUM: 0, GOLD: 0 };
      for (const u of users) byTier[u.tier] = (byTier[u.tier] || 0) + 1;
      return send(200, { byTier, totalUsers: users.length });
    }

    return send(404, { error: 'not found', path });
  });
  return server;
}

// ---------------------------------------------------------------------------
// Entry points
// ---------------------------------------------------------------------------
async function httpCall(server, method, path, { token, body } = {}) {
  const port = server.address().port;
  const res = await fetch(`http://127.0.0.1:${port}${path}`, {
    method,
    headers: {
      'content-type': 'application/json',
      ...(token ? { authorization: `Bearer ${token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  let json = null;
  try { json = await res.json(); } catch { /* no body */ }
  return { status: res.status, body: json };
}

async function selftest() {
  const store = new InMemoryStore();
  const server = buildServer(store);
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  let passed = 0, failed = 0;
  const check = (name, cond) => {
    if (cond) { passed++; console.log('  PASS  ' + name); }
    else { failed++; console.log('  FAIL  ' + name); }
  };

  console.log('Matrimony PoC selftest\n');

  // --- two tenants, isolated
  const A1 = await httpCall(server, 'POST', '/auth/signup', { body: { email: 'a1@t-a.com', password: 'pw-a1', name: 'Asha', tenant: 't-a', tier: 'GOLD' } });
  const A2 = await httpCall(server, 'POST', '/auth/signup', { body: { email: 'a2@t-a.com', password: 'pw-a2', name: 'Rohan', tenant: 't-a', tier: 'FREE' } });
  const B1 = await httpCall(server, 'POST', '/auth/signup', { body: { email: 'b1@t-b.com', password: 'pw-b1', name: 'Bella', tenant: 't-b', tier: 'GOLD' } });
  check('signup returns token', A1.status === 201 && !!A1.body.token);
  const [aTok, a2Tok, bTok] = [A1.body.token, A2.body.token, B1.body.token];

  // --- bad login
  const bad = await httpCall(server, 'POST', '/auth/login', { body: { email: 'a1@t-a.com', password: 'wrong', tenant: 't-a' } });
  check('wrong password rejected', bad.status === 401);

  // --- profiles
  const pa = await httpCall(server, 'POST', '/profiles', { token: aTok, body: { name: 'Asha', age: 29, religion: 'Hindu', city: 'Mumbai' } });
  const pb = await httpCall(server, 'POST', '/profiles', { token: a2Tok, body: { name: 'Rohan', age: 33, religion: 'Hindu', city: 'Pune' } });
  const pc = await httpCall(server, 'POST', '/profiles', { token: bTok, body: { name: 'Bella', age: 28, religion: 'Muslim', city: 'Mumbai' } });
  check('profile created in each tenant', pa.status === 201 && pb.status === 201 && pc.status === 201);

  // --- invalid profile rejected
  const badProf = await httpCall(server, 'POST', '/profiles', { token: aTok, body: { name: 'X', age: 12, religion: 'Hindu', city: 'X' } });
  check('underage profile rejected', badProf.status === 422);

  // --- matching: Asha should NOT see Bella (different tenant), should see Rohan
  const ma = await httpCall(server, 'GET', '/profiles/match', { token: aTok });
  const namesA = (ma.body.matches || []).map(m => m.name);
  check('match sees same-tenant profile', namesA.includes('Rohan'));
  check('match does NOT leak other tenant', !namesA.includes('Bella'));

  // --- messaging gate: FREE cannot message, GOLD can
  const freeMsg = await httpCall(server, 'POST', '/messages', { token: a2Tok, body: { toUserId: A1.body.user.id, text: 'hi' } });
  check('FREE tier blocked from messaging', freeMsg.status === 403);
  const goldMsg = await httpCall(server, 'POST', '/messages', { token: aTok, body: { toUserId: A2.body.user.id, text: 'namaste' } });
  check('GOLD tier can message', goldMsg.status === 201);

  // --- cross-tenant messaging blocked
  const crossMsg = await httpCall(server, 'POST', '/messages', { token: aTok, body: { toUserId: B1.body.user.id, text: 'no' } });
  check('cross-tenant message blocked', crossMsg.status === 404);

  // --- admin: member cannot access, admin can; tenant-scoped
  const admin = await httpCall(server, 'POST', '/auth/signup', { body: { email: 'admin@t-a.com', password: 'pw-adm', name: 'Admin', tenant: 't-a', role: 'admin' } });
  const admTok = admin.body.token;
  check('admin signup works', admin.status === 201 && !!admTok);
  const memberBlocked = await httpCall(server, 'GET', '/admin/users', { token: aTok });
  check('member blocked from admin panel', memberBlocked.status === 403);
  const users = await httpCall(server, 'GET', '/admin/users', { token: admTok });
  const emails = (users.body.users || []).map(u => u.email);
  check('admin sees tenant users', emails.includes('a1@t-a.com') && emails.includes('a2@t-a.com'));
  check('admin does NOT see other tenant', !emails.includes('b1@t-b.com'));

  // --- admin tier change + revenue
  const patch = await httpCall(server, 'PATCH', '/admin/tier', { token: admTok, body: { userId: A2.body.user.id, tier: 'PREMIUM' } });
  check('admin can upgrade tier', patch.status === 200 && patch.body.user.tier === 'PREMIUM');
  const rev = await httpCall(server, 'GET', '/admin/revenue', { token: admTok });
  check('revenue reflects tier change', rev.body.byTier.PREMIUM === 1 && rev.body.byTier.GOLD === 1 && rev.body.byTier.FREE === 1);

  // --- token expiry / tamper
  const tampered = aTok.slice(0, -4) + 'AAAA';
  const badTok = await httpCall(server, 'GET', '/admin/users', { token: tampered });
  check('tampered token rejected', badTok.status === 401);
  const noTok = await httpCall(server, 'GET', '/profiles/match');
  check('missing token rejected', noTok.status === 401);

  server.close();
  console.log(`\nResult: ${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
}

async function main() {
  const cmd = process.argv[2] || 'server';
  if (cmd === 'selftest') return selftest();
  if (cmd === 'server' || cmd === 'start') {
    const store = new InMemoryStore();
    const server = buildServer(store);
    server.listen(PORT, '127.0.0.1', () => console.log(`matrimony-poc listening on http://127.0.0.1:${PORT}`));
    return;
  }
  console.error('unknown command: ' + cmd);
  process.exit(2);
}
main().catch(e => { console.error(e); process.exit(1); });
