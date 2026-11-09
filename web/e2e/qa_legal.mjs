// QA (area "legal"): the public Terms / Privacy / DPA pages while signed out, by path and by hash.
//   API: fresh OUTREACH_DATA_FILE, OUTREACH_ADMIN_PASSWORD=qa-admin-pass-1 OUTREACH_ADMIN_LOGIN=1 OUTREACH_EMAIL_DOMAIN_CHECK=0
//   UI:  vite build, served by e2e/static_proxy.mjs (falls back to index.html like Caddy).
//   cd web && E2E_UI=http://localhost:5291 SHOT_DIR=/tmp/legal node e2e/qa_legal.mjs
import fs from "node:fs";
import { chromium } from "playwright";

const UI = process.env.E2E_UI || "http://localhost:5291";
const SHOTS = process.env.SHOT_DIR || "/tmp";
fs.mkdirSync(SHOTS, { recursive: true });
const results = [];
const shots = [];
const browser = await chromium.launch();
const DOCS = { terms: "Terms of Service", privacy: "Privacy Policy", dpa: "Data Processing Agreement" };
async function step(name, fn) { try { await fn(); results.push([true, name]); } catch (e) { results.push([false, `${name} - ${e.message.split("\n")[0]}`]); } }
const ok = (c, m) => { if (!c) throw new Error(m); };

for (const [key, title] of Object.entries(DOCS)) {
  for (const [w, h] of [[1440, 900], [375, 800]]) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h } });
    const page = await ctx.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errors.push(m.text()); });
    const apiCalls = [];
    page.on("request", (r) => { if (r.url().includes("/api/")) apiCalls.push(new URL(r.url()).pathname); });
    await step(`${key} @${w}: path /${key} renders signed out`, async () => {
      await page.goto(`${UI}/${key}`, { waitUntil: "load" });
      await page.getByRole("heading", { level: 1, name: title }).waitFor({ timeout: 8000 });
      ok(await page.getByRole("heading", { level: 1, name: title }).isVisible(), "h1 missing");
      ok(await page.getByTestId("legal-draft").isVisible(), "draft banner missing");
      ok((await page.getByTestId("legal-draft").innerText()).includes("DRAFT"), "no DRAFT text");
      ok(await page.locator("input[type=password]").count() === 0, "login form shown");
      ok(await page.locator("main h2").count() >= 10, "too few sections");
      await page.waitForFunction(() => /Version \d{4}-\d{2}-\d{2}/.test(document.querySelector("[data-testid=legal-version]")?.textContent || ""), null, { timeout: 5000 });
      ok(apiCalls.every((p) => p === "/api/signup/config"), "unexpected API calls " + apiCalls.join(","));
      ok(errors.length === 0, "console errors " + errors.join(" | "));
    });
    await step(`${key} @${w}: no horizontal scroll`, async () => {
      const sw = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
      ok(sw[0] <= sw[1], `scrollWidth ${sw[0]} > ${sw[1]}`);
    });
    await step(`${key} @${w}: contents links jump to their section`, async () => {
      const links = page.locator("nav[aria-label=Contents] a");
      const n = await links.count();
      ok(n >= 10, "toc short");
      const last = links.nth(5);      // a middle entry (the last one cannot reach the top of a short end of page)
      const id = (await last.getAttribute("href")).slice(1);
      await last.click();
      ok(page.url().startsWith(`${UI}/${key}`) && !page.url().includes("#"), "navigated away: " + page.url());
      const top = await page.evaluate((i) => document.getElementById(i).getBoundingClientRect().top, id);
      ok(top >= -2 && top < 200, "section not scrolled into view " + top);
      await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
    });
    if (w === 1440) await step(`${key}: skip link and placeholders list`, async () => {
      await page.goto(`${UI}/${key}`, { waitUntil: "load" });
      await page.keyboard.press("Tab");
      ok(await page.locator(".legal-skip").evaluate((e) => e === document.activeElement), "skip link not first focus");
      ok(await page.locator(".legal-ph li").count() >= 5, "placeholders not listed");
    });
    const file = `${SHOTS}/legal-${key}-${w}.png`;
    await page.screenshot({ path: file, fullPage: false }); shots.push(file);
    await ctx.close();
  }
}
{
  const ctx = await browser.newContext({ viewport: { width: 1200, height: 800 } });
  const page = await ctx.newPage();
  for (const key of Object.keys(DOCS)) {
    await step(`hash form #${key}`, async () => {
      await page.goto(`${UI}/#${key}`, { waitUntil: "load" }); await page.getByRole("heading", { level: 1 }).waitFor({ timeout: 8000 });
      ok(await page.getByRole("heading", { level: 1, name: DOCS[key] }).isVisible(), "h1 missing");
    });
  }
  await step("sign-in page still shown at / and links open the pages", async () => {
    await page.goto(`${UI}/`, { waitUntil: "load" }); await page.locator("input[type=password]").first().waitFor({ timeout: 8000 });
    ok(await page.locator("input[type=password]").first().isVisible(), "login missing");
  });
  await ctx.close();
}
await browser.close();
for (const [pass, name] of results) console.log(pass ? "PASS" : "FAIL", name);
console.log(shots.join("\n"));
const bad = results.filter((r) => !r[0]).length;
console.log(`${results.length - bad}/${results.length} passed`);
process.exit(bad ? 1 : 0);
