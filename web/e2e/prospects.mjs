// Prospect search walkthrough at 1400px and 375px; fails on any console error, failed request or sideways page scroll.
//
//   .venv/bin/python -m tests.qa.seed_prospects <tmp>/d.json 3000 300
//   API: OUTREACH_DATA_FILE=<tmp>/d.json OUTREACH_ADMIN_PASSWORD= .venv/bin/uvicorn app.main:app --port 8073
//   UI:  npx vite build --outDir <tmp>/dist && node e2e/static_proxy.mjs <tmp>/dist 5273 http://127.0.0.1:8073
//   cd web && E2E_UI=http://127.0.0.1:5273 node e2e/prospects.mjs
import { chromium } from "playwright";
import fs from "node:fs";

const BASE = process.env.E2E_UI || "http://127.0.0.1:5273";
const S = process.env.SHOT_DIR || "e2e-shots/prospects";
fs.mkdirSync(S, { recursive: true });
const browser = await chromium.launch();
const results = [];
const check = (name, ok, extra = "") => { results.push([ok, name]); console.log(ok ? "PASS" : "FAIL", name, extra); };

async function run(width) {
  const W = `[${width}]`;
  const phone = width < 760;
  const page = await browser.newPage({ viewport: { width, height: phone ? 800 : 900 } });
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("response", (r) => { if (r.status() >= 400 && !r.url().includes("favicon")) errors.push(`HTTP ${r.status()} ${r.url()}`); });
  const noSideScroll = async (where) => {
    const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    check(`${W} no sideways scroll (${where})`, over <= 1, `overflow ${over}px`);
  };
  const answer = async (value) => {
    const box = page.getByTestId("app-dialog");
    await box.waitFor({ timeout: 5000 });
    if (value !== undefined) await box.getByTestId("dialog-input").fill(value);
    await box.getByTestId("dialog-ok").click();
    await box.waitFor({ state: "detached" });
  };

  await page.goto(`${BASE}/#/prospects`);
  await page.getByTestId("prospect-row").first().waitFor({ timeout: 15000 });
  const total = await page.getByTestId("prospect-total").textContent();
  check(`${W} people load with a total`, /3,005 people/.test(total), total);
  check(`${W} stats line`, /people .* companies/.test(await page.getByTestId("prospect-stats").textContent()));
  await page.screenshot({ path: `${S}/people-${width}.png`, fullPage: false });
  await noSideScroll("people");

  // menu: first item of Pipeline
  if (!phone) {
    const firstPipeline = await page.locator("nav.side .nav-group").filter({ hasText: "Pipeline" }).locator("button").first().textContent();
    check(`${W} Prospect search is first in Pipeline`, /Prospect search/.test(firstPipeline), firstPipeline);
  }

  // filters (behind a button on phones)
  if (phone) {
    await page.getByRole("button", { name: /^Filters/ }).click();
    check(`${W} filter panel opens`, await page.getByTestId("prospect-filters").isVisible());
  }
  await page.getByLabel("Title has any of").fill("hr");
  await page.getByLabel("Title has any of").press("Enter");
  await page.waitForFunction(() => /^455 people/.test(document.querySelector("[data-testid=prospect-total]")?.textContent || ""), null, { timeout: 8000 });
  check(`${W} title filter narrows results`, true);
  await page.getByRole("button", { name: /^Email and phone/ }).click();
  await page.getByLabel("Verified emails only").check();
  await page.waitForFunction(() => { const t = document.querySelector("[data-testid=prospect-total]")?.textContent || ""; return /people/.test(t) && !/^455 /.test(t); }, null, { timeout: 8000 });
  const narrowed = await page.getByTestId("prospect-total").textContent();
  check(`${W} verified-only narrows further`, parseInt(narrowed.replace(/,/g, ""), 10) < 455, narrowed);
  const badges = await page.locator("[data-testid=prospect-row] .ps-email .pill").allTextContents();
  check(`${W} every row shows a verified/valid badge with confidence`, badges.length > 0 && badges.filter((b) => b !== "phone").every((b) => /(Verified|Valid) · \d+%/.test(b)), badges.slice(0, 3).join(" | "));
  if (phone) await page.getByRole("button", { name: /^Filters/ }).click();

  // save the search, then reopen it from the saved list
  await page.getByRole("button", { name: "Save search" }).click();
  const searchName = `HR verified ${width} ${Date.now()}`;
  await answer(searchName);
  await page.getByTestId("prospect-msg").waitFor();
  await page.getByRole("button", { name: /^Clear all/ }).count().then(async (n) => {
    if (n && (!phone || await page.getByTestId("prospect-filters").isVisible())) await page.getByRole("button", { name: /^Clear all/ }).click();
  });
  if (phone) { await page.getByRole("button", { name: /^Filters/ }).click(); await page.getByRole("button", { name: /^Clear all/ }).click(); await page.getByRole("button", { name: /^Filters/ }).click(); }
  await page.waitForFunction(() => /^3,005 people/.test(document.querySelector("[data-testid=prospect-total]")?.textContent || ""), null, { timeout: 8000 });
  const saved = page.getByLabel("Saved searches");
  const value = await saved.locator("option", { hasText: searchName }).getAttribute("value");
  await saved.selectOption(value);
  await page.waitForFunction((n) => (document.querySelector("[data-testid=prospect-total]")?.textContent || "").startsWith(n), narrowed.split(" ")[0], { timeout: 8000 });
  check(`${W} saved search restores the filters`, true);

  // person drawer with provenance
  await page.locator("[data-testid=prospect-row] .ps-name button").first().click();
  await page.getByTestId("prospect-person").waitFor();
  await page.getByTestId("provenance").waitFor();
  await page.locator("[data-testid=provenance] summary").first().click();
  check(`${W} provenance shows source and seen date`, await page.locator(".prov-table tbody tr").first().isVisible());
  await page.screenshot({ path: `${S}/person-${width}.png` });
  await page.getByTestId("prospect-person").getByRole("button", { name: "Close" }).click();

  // company drawer
  await page.locator("[data-testid=prospect-row] td:nth-child(4) button").first().click();
  await page.getByTestId("prospect-company").waitFor();
  await page.getByTestId("company-people").waitFor();
  check(`${W} company drawer lists its people`, await page.getByTestId("company-people").locator("button").count() > 0);
  await page.screenshot({ path: `${S}/company-${width}.png` });
  await noSideScroll("company drawer");
  await page.getByTestId("prospect-company").getByRole("button", { name: "Close" }).click();

  // pick two people and add them to leads + a new list
  const boxes = page.locator("[data-testid=prospect-row] td.pick input");
  await boxes.nth(0).check(); await boxes.nth(1).check();
  await page.getByTestId("prospect-bulkbar").waitFor();
  await page.getByTestId("prospect-bulkbar").getByRole("button", { name: "Add to list…" }).click();
  await answer(`Prospects ${width}`);
  await page.getByText(/Added \d+ new lead/).waitFor({ timeout: 8000 });
  check(`${W} add to leads + list`, /Saved to the list/.test(await page.getByTestId("prospect-msg").textContent()));
  // estimate before spending: pick people without a verified email (no providers are connected here, so nothing is spent)
  await page.reload();
  await page.getByTestId("prospect-row").first().waitFor();
  await page.getByLabel("Sort by").selectOption("name");
  await page.waitForTimeout(600);
  for (let i = 0; i < 4; i++) await boxes.nth(i).check();
  await page.getByTestId("prospect-bulkbar").getByRole("button", { name: "Find emails…" }).click();
  const est = await Promise.race([
    page.getByTestId("app-dialog").waitFor({ timeout: 8000 }).then(() => page.getByTestId("app-dialog").textContent()),
    page.getByText(/already has a verified email/).waitFor({ timeout: 8000 }).then(() => "nothing to look up"),
  ]);
  check(`${W} find-emails shows the estimated cost before running`, /Estimated cost|nothing to look up/.test(est), est.slice(0, 160));
  if (await page.getByTestId("app-dialog").count()) {
    await page.getByTestId("app-dialog").getByRole("button", { name: /Cancel/ }).click();
    await page.getByTestId("app-dialog").waitFor({ state: "detached" });
  }
  await page.getByTestId("prospect-bulkbar").getByRole("button", { name: "Clear" }).click();
  await page.screenshot({ path: `${S}/people-${width}.png`, fullPage: false });

  // companies tab
  await page.getByRole("tab", { name: "Companies" }).click();
  await page.getByTestId("company-row").first().waitFor();
  check(`${W} companies tab`, /300 companies/.test(await page.getByTestId("prospect-total").textContent()));
  await noSideScroll("companies");
  await page.screenshot({ path: `${S}/companies-${width}.png` });

  // owner tab
  await page.getByRole("tab", { name: "Spend & sources" }).click();
  await page.getByTestId("prospect-spend").waitFor();
  check(`${W} spend and sources tab`, await page.getByTestId("prospect-sources").isVisible());
  await page.screenshot({ path: `${S}/admin-${width}.png`, fullPage: true });
  await noSideScroll("admin");

  check(`${W} no console errors`, errors.length === 0, errors.slice(0, 5).join(" | "));
  await page.close();
}

await run(1400);
await run(375);
await browser.close();
const failed = results.filter(([ok]) => !ok);
console.log(`${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
