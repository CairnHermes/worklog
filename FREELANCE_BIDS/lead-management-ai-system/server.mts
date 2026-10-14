// LeadFlow demo server: the SAME modules the Playwright E2E test drives.
// Endpoints:
//   GET  /            -> single-page dashboard (vanilla, responsive)
//   POST /api/leads   -> intake (name, email, notes, source?)
//   POST /webhook/n8n -> n8n-style webhook trigger, processes the queue
//   GET  /api/leads   -> all leads with summaries + suggestions
//
// Run:  node server.mts   (needs tsc build OR use the compiled test path)
// The Playwright test runs the same functions in-process (below).

import { createServer } from "node:http";
import { addLead, createStore, processQueue, type Lead } from "./src/leads.ts";

const store = createStore();

export function handler(req: import("node:http").IncomingMessage, res: import("node:http").ServerResponse) {
  const url = new URL(req.url ?? "/", "http://localhost");
  if (req.method === "POST" && url.pathname === "/api/leads") {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      try {
        const j = JSON.parse(body || "{}");
        const lead: Lead = addLead(store, {
          name: String(j.name ?? ""),
          email: String(j.email ?? ""),
          notes: String(j.notes ?? ""),
          company: j.company ? String(j.company) : undefined,
          budget: j.budget ? String(j.budget) : undefined,
          source: j.source ?? "web-intake",
        });
        res.writeHead(201, { "content-type": "application/json" });
        res.end(JSON.stringify({ ok: true, id: lead.id }));
      } catch (e) {
        res.writeHead(400, { "content-type": "application/json" });
        res.end(JSON.stringify({ ok: false, error: String(e) }));
      }
    });
    return;
  }
  if (req.method === "POST" && url.pathname === "/webhook/n8n") {
    (async () => {
      const done = await processQueue(store);
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ ok: true, processed: done.length, processedIds: done.map((d) => d.id) }));
    })();
    return;
  }
  if (url.pathname === "/api/leads") {
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify([...store.leads.values()]));
    return;
  }
  if (url.pathname === "/" && req.method === "GET") {
    res.writeHead(200, { "content-type": "text/html" });
    res.end(DASHBOARD_HTML);
    return;
  }
  res.writeHead(404, { "content-type": "application/json" });
  res.end(JSON.stringify({ ok: false, error: "not found" }));
}

const DASHBOARD_HTML = `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LeadFlow - AI Lead Management (PoC)</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: system-ui, sans-serif; margin: 0; background: #0f1115; color: #e6e6e6; }
  header { padding: 16px 20px; border-bottom: 1px solid #2a2d35; display: flex; gap: 12px; align-items: center; }
  header b { font-size: 16px; }
  main { padding: 20px; max-width: 900px; margin: 0 auto; }
  form { display: grid; gap: 8px; margin-bottom: 24px; }
  input, textarea { padding: 8px 10px; border-radius: 6px; border: 1px solid #3a3d45; background: #171a21; color: inherit; font: inherit; }
  button { padding: 8px 14px; border-radius: 6px; border: 0; background: #3b82f6; color: #fff; font: inherit; cursor: pointer; }
  .lead { border: 1px solid #2a2d35; border-radius: 8px; padding: 14px; margin-bottom: 12px; }
  .lead h3 { margin: 0 0 6px; font-size: 15px; }
  .lead .sum { margin: 6px 0; color: #b9c2d0; }
  .lead ul { margin: 6px 0 0; padding-left: 18px; color: #9aa4b5; }
  .badge { font-size: 11px; padding: 2px 8px; border-radius: 99px; background: #1e3a8a; }
  .badge.new { background: #7c2d12; }
</style></head><body>
<header><b>LeadFlow</b><span class="badge">PoC</span></header>
<main>
  <form id="intake">
    <input name="name" placeholder="Contact name" required>
    <input name="email" type="email" placeholder="Email" required>
    <input name="company" placeholder="Company (optional)">
    <textarea name="notes" rows="3" placeholder="What did they say?" required></textarea>
    <button type="submit">Add lead</button>
  </form>
  <section id="leads"></section>
</main>
<script>
async function refresh() {
  const r = await fetch('/api/leads');
  const leads = await r.json();
  const el = document.getElementById('leads');
  el.innerHTML = '';
  for (const l of leads) {
    const div = document.createElement('div');
    div.className = 'lead';
    div.innerHTML = '<h3></h3><div class="sum"></div><ul></ul>';
    div.querySelector('h3').textContent = l.name + (l.company ? ' - ' + l.company : '');
    div.querySelector('h3').insertAdjacentHTML('beforeend', ' <span class="badge ' + l.status + '">' + l.status + '</span>');
    div.querySelector('.sum').textContent = l.summary || 'Awaiting AI summary...';
    for (const s of (l.followUpSuggestions || [])) {
      const li = document.createElement('li'); li.textContent = s; div.querySelector('ul').appendChild(li);
    }
    el.appendChild(div);
  }
}
document.getElementById('intake').addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = e.target;
  await fetch('/api/leads', { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ name: f.name.value, email: f.email.value, company: f.company.value, notes: f.notes.value }) });
  await fetch('/webhook/n8n', { method: 'POST' });
  f.reset(); refresh();
});
refresh();
</script></body></html>`;

const server = createServer(handler);
const port = Number(process.env.PORT ?? 8765);
server.listen(port, "127.0.0.1", () => {
  console.log(`LeadFlow PoC listening on http://127.0.0.1:${port}`);
});
