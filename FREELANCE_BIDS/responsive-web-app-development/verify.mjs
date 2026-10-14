/**
 * Verification for the Responsive Web App Development PoC.
 *
 * Drives a real headless Chromium (Playwright) against PoC/index.html and
 * proves, with assertions (not vibes):
 *   1. the app renders (brand + form + board present),
 *   2. responsive layout: 2-col on desktop (>=720px), 1-col on mobile (<=480px),
 *   3. form validation: empty title is REJECTED with an inline error,
 *   4. a valid task is ACCEPTED and appears in the board,
 *   5. stats update to reflect the new task.
 *
 * Run:  node verify.mjs        (from this folder)
 */
import { chromium } from "playwright";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { existsSync } from "node:fs";

const here = path.dirname(fileURLToPath(import.meta.url));
const url = "file://" + path.resolve(here, "PoC/index.html").replace(/\\/g, "/");

// Use whichever Chromium this machine actually has installed (avoids a
// version-locked "Executable doesn't exist" failure).
const CANDIDATES = [
  process.env.CHROME_PATH,
  process.env.CHROME_HEADLESS_SHELL_PATH,
  "C:/Users/jakec/AppData/Local/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-win64/chrome-headless-shell.exe",
  "C:/Users/jakec/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe",
].filter(Boolean);
const executablePath = CANDIDATES.find((p) => existsSync(p));

let failed = 0;
function check(name, cond) {
  console.log(`  ${cond ? "OK  " : "FAIL"}  ${name}`);
  if (!cond) failed += 1;
}

const launchOpts = { executablePath };
const browser = await chromium.launch(launchOpts);
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
await page.goto(url, { waitUntil: "networkidle" });

// 1. renders
check("brand visible", await page.locator("header .brand").isVisible());
check("add-task form visible", await page.locator("#task-form").isVisible());
check("board list visible", await page.locator("#list").isVisible());
const initialCount = await page.locator("#list .task").count();
check("seed tasks rendered (>=3)", initialCount >= 3);

// 2. responsive: desktop 2-col
const desktopCols = await page
  .locator("main")
  .evaluate((el) => getComputedStyle(el).gridTemplateColumns.split(" ").length);
check("desktop grid has 2 columns", desktopCols === 2);

// 3. validation: empty title rejected
await page.locator("#title").fill("");
await page.locator('button[type="submit"]').click();
const titleErr = (await page.locator("#title-hint").textContent()).trim();
check("empty title shows inline error", /required/i.test(titleErr));
check("task NOT added on empty title", (await page.locator("#list .task").count()) === initialCount);

// 4. valid submission accepted
await page.locator("#title").fill("Ship the responsive dashboard");
await page.locator("#priority").selectOption("high");
await page.locator("#due").fill("2026-10-25");
await page.locator("#owner").fill("Cairn");
await page.locator('button[type="submit"]').click();
await page.waitForTimeout(50);
const afterCount = await page.locator("#list .task").count();
check("valid task added (+1)", afterCount === initialCount + 1);
check(
  "new task text present",
  (await page.locator("#list").innerText()).includes("Ship the responsive dashboard")
);
check("owner captured", (await page.locator("#list").innerText()).includes("Owner: Cairn"));

// 5. stats reflect the new total
const statsText = await page.locator("#stats").innerText();
check("stats show updated total", statsText.includes(String(afterCount)));

// 2b. responsive: mobile 1-col (resize)
await page.setViewportSize({ width: 390, height: 844 });
await page.waitForTimeout(50);
const mobileCols = await page
  .locator("main")
  .evaluate((el) => getComputedStyle(el).gridTemplateColumns.split(" ").length);
check("mobile grid collapses to 1 column", mobileCols === 1);
check("form still usable on mobile", await page.locator("#title").isVisible());

// 2c. tablet midpoint
await page.setViewportSize({ width: 768, height: 1024 });
await page.waitForTimeout(50);
const tabletCols = await page
  .locator("main")
  .evaluate((el) => getComputedStyle(el).gridTemplateColumns.split(" ").length);
check("tablet (768px) keeps 2 columns", tabletCols === 2);

await browser.close();

if (failed > 0) {
  console.error(`\nRESULT: ${failed} check(s) FAILED`);
  process.exit(1);
} else {
  console.log("\nRESULT: all checks passed");
}
