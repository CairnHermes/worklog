import { test, expect } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const PORT = 8765;
const BASE = `http://127.0.0.1:${PORT}`;
let server: ChildProcess;
let scratch: string;

test.beforeAll(async () => {
  scratch = await mkdtemp(path.join(tmpdir(), "leadflow-"));
  server = spawn(process.execPath, ["--experimental-strip-types", path.join(ROOT, "server.mts")], {
    cwd: ROOT,
    env: { ...process.env, PORT: String(PORT) },
    stdio: ["ignore", "pipe", "pipe"],
  });
  // wait for readiness
  for (let i = 0; i < 50; i++) {
    try {
      const r = await fetch(`${BASE}/api/leads`);
      if (r.ok) break;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 200));
  }
});

test.afterAll(async () => {
  server?.kill();
  await rm(scratch, { recursive: true, force: true });
});

test("intake -> AI summary -> n8n webhook -> dashboard shows it", async ({ page }) => {
  await page.goto("/");
  // 1) lead intake via the real form
  await page.fill('input[name="name"]', "Dana Reyes");
  await page.fill('input[name="email"]', "dana@acme.co");
  await page.fill('input[name="company"]', "Acme Logistics");
  await page.fill('textarea[name="notes"]', "We need an API integration by Friday. Budget is $5,000. Can you show me a demo?");
  await page.click('button[type="submit"]');

  // 2) dashboard renders the lead with a summary + suggestions (not "Awaiting")
  const lead = page.locator(".lead").first();
  await expect(lead).toContainText("Dana Reyes");
  await expect(lead).toContainText("Acme Logistics");
  await expect(lead).not.toContainText("Awaiting AI summary");
  await expect(lead).toContainText("asked for a demo");
  await expect(lead).toContainText("needs integration work");
  await expect(lead).toContainText("$5,000");
  await expect(lead.locator("li")).toHaveCount(3);

  // 3) API shape check: summary + followUpSuggestions + status
  const body = await page.request.get("/api/leads").then((r) => r.json());
  expect(body).toHaveLength(1);
  expect(body[0].status).toBe("summarized");
  expect(body[0].summary).toMatch(/Dana Reyes/);
  expect(body[0].followUpSuggestions).toHaveLength(3);
});

test("invalid email is rejected with 400", async ({ request }) => {
  const r = await request.post("/api/leads", {
    data: { name: "Bad", email: "not-an-email", notes: "hi" },
  });
  expect(r.status()).toBe(400);
});

test("processQueue is idempotent (n8n re-fire does not double-process)", async ({ request }) => {
  const before = await request.get("/api/leads").then((r) => r.json());
  const n = before.length;
  const r = await request.post("/webhook/n8n");
  expect(r.status()).toBe(200);
  const after = await request.get("/api/leads").then((r) => r.json());
  expect(after).toHaveLength(n);
  for (const l of after) expect(l.status).toBe("summarized");
});
