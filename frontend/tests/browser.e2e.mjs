// Real-browser check of the built PWA against a running backend: `node tests/browser.e2e.mjs http://127.0.0.1:PORT`.
// Started by backend/tests/test_browser.py, which serves a fake Alpaca feed. Exits non-zero on the first failed check.
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium, devices } = require("playwright");
const base = process.argv[2];
assert.ok(base, "usage: node tests/browser.e2e.mjs <base-url>");

const shots = process.env.SCREENSHOT_DIR; // optional: save a screenshot of each checked page
const shot = (page, name) => shots && page.screenshot({ path: `${shots}/${name}.png`, fullPage: !name.startsWith("iphone") });
const STATUSES = ["LIVE", "DELAYED", "STALE", "CLOSED", "UNAVAILABLE"];
const browser = await chromium.launch();
const errors = [];

async function noStoredData(page) {
  const r = await page.evaluate(async () => {
    const dbs = indexedDB.databases ? await indexedDB.databases() : [];
    const cached = [];
    for (const k of await caches.keys()) for (const req of await (await caches.open(k)).keys()) cached.push(req.url);
    return { local: localStorage.length, session: sessionStorage.length, dbs: dbs.length, cookies: document.cookie, api: cached.filter((u) => u.includes("/api/")) };
  });
  assert.deepEqual(r, { local: 0, session: 0, dbs: 0, cookies: "", api: [] }, "nothing about the visitor or the market may be stored in the browser");
}

try {
  for (const [label, opts] of [["desktop", { viewport: { width: 1366, height: 900 } }], ["iphone", devices["iPhone 13"]]]) {
    const ctx = await browser.newContext(opts);
    const page = await ctx.newPage();
    page.on("pageerror", (e) => errors.push(`${label}: ${e.message}`));

    // Dashboard: cards carry a status badge and inspectable provenance.
    await page.goto(base + "/");
    await page.getByText("Bottom Opportunity Score").first().waitFor({ timeout: 60_000 });
    const badges = await page.locator("span[title]").filter({ hasText: new RegExp(`^(${STATUSES.join("|")})$`) }).count();
    assert.ok(badges >= 8, `${label}: expected a status badge per card, found ${badges}`);
    await page.locator("canvas").first().waitFor({ timeout: 60_000 }); // price chart rendered
    await page.getByRole("button", { name: "source & method" }).nth(1).click();
    await page.getByText("Method", { exact: true }).first().waitFor();
    await shot(page, `${label}-dashboard`);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    assert.ok(overflow <= 1, `${label}: page scrolls horizontally by ${overflow}px`);

    // Live quote: driven by streamed trades from the fake upstream, so the price must change on its own.
    await page.goto(base + "/analyze/?s=SPY");
    const last = page.getByTestId("live-last");
    await last.waitFor({ timeout: 30_000 });
    const p1 = await last.textContent();
    await page.waitForFunction((v) => document.querySelector('[data-testid="live-last"]')?.textContent !== v, p1, { timeout: 15_000 });
    const status = await page.getByTestId("live-quote").getAttribute("data-status");
    assert.ok(["LIVE", "DELAYED", "CLOSED", "STALE"].includes(status), `${label}: live status ${status}`);
    // SPY has no history in the test feed: the page must say so instead of inventing analysis.
    await page.getByRole("alert").first().waitFor();
    await shot(page, `${label}-analyze`);

    // Session alerts: exist only for this tab.
    await page.goto(base + "/alerts/");
    await page.getByRole("button", { name: "Add alert" }).click();
    await page.getByText("Score rises above threshold", { exact: true }).last().waitFor();
    await noStoredData(page);
    await page.reload();
    await page.getByText("No alerts yet.").waitFor();

    // Data sources page reads the server's live status.
    await page.goto(base + "/settings/");
    await page.getByText("Streaming feeds").waitFor();
    await page.getByText("alpaca").first().waitFor();
    await shot(page, `${label}-sources`);
    await noStoredData(page);
    await ctx.close();
    console.log(`ok ${label}`);
  }
  assert.deepEqual(errors, [], "uncaught page errors");
} finally {
  await browser.close();
}
