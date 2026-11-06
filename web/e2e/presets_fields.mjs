// Industry presets + custom lead fields: the real UI at 1400px and 375px, failing on any console error.
//
//   API: a fresh OUTREACH_DATA_FILE with brand/offer settings saved (company_name "Skyline Homes", offer value_1
//        "2 and 3 BHK flats in Baner and Wakad.", sign_off "Regards,\nPriya, Skyline Homes"), OUTREACH_BASE_URL set to the UI address
//   UI:  npx vite build --outDir <temp>/dist, served with /api proxied to the API (E2E_UI, default http://127.0.0.1:5263)
//   cd web && node e2e/presets_fields.mjs
import { chromium } from "playwright";
import fs from "node:fs";

const BASE = process.env.E2E_UI || "http://127.0.0.1:5263";
const S = process.env.SHOT_DIR || "e2e-shots/presets-fields";
fs.mkdirSync(S, { recursive: true });
const CSV = ["company,contact_name,title,email,phone,website,Budget (₹ lakh)",
  "Lotus Developers,Anil Mehta,Founder,anil@lotusdev.example,98200 11111,https://lotusdev.example,85",
  "Green Acres Realty,Sunita Rao,Director,sunita@greenacres.example,98200 22222,https://greenacres.example,140",
  "Metro Brokers,Rahul Jain,Channel Partner,rahul@metrobrokers.example,98200 33333,https://metrobrokers.example,40"].join("\n");

const browser = await chromium.launch();
const results = [];
const check = (W, name, ok, extra = "") => { results.push([W, name, ok]); console.log(`${ok ? "PASS" : "FAIL"} ${W} ${name} ${extra}`); };

async function run(width) {
  const W = `[${width}]`;
  const page = await browser.newPage({ viewport: { width, height: width > 500 ? 900 : 812 } });
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  const first = width > 500;

  // 1. Apply the Real estate preset (Setup → Industry & fields)
  await page.goto(`${BASE}/#setup?tab=industry`);
  await page.waitForSelector("[data-testid=presets-card]");
  await page.locator("label", { hasText: "Real estate" }).locator("input[type=radio]").check();
  await page.click("button:has-text('Apply preset')");
  await page.waitForSelector("[data-testid=preset-result]");
  const res = await page.textContent("[data-testid=preset-result]");
  check(W, "preset applied", first ? /email sequence/.test(res) && /LinkedIn steps/.test(res) : /Real estate applied/.test(res), res.slice(0, 140));
  await page.screenshot({ path: `${S}/preset-${width}.png`, fullPage: false });

  // 2. Create the custom field "Budget (₹ lakh)" (once)
  if (first) {
    await page.click("[data-testid=lead-fields-card] >> button:has-text('Add a field')");
    await page.fill("[aria-label='Field 1 name']", "Budget (₹ lakh)");
    await page.selectOption("[aria-label='Field 1 type']", "number");
    const card = page.locator("[data-testid=lead-fields-card]");
    await card.locator("label:has-text('Column') input").check();
    await card.locator("label:has-text('Filter') input").check();
    await card.locator("button:has-text('Save fields')").click();
    await page.waitForSelector("[data-testid=lead-fields-card] >> text=Fields saved.");
    check(W, "field saved with key", (await page.inputValue("[aria-label='Field 1 key']")) === "budget_lakh");
  } else {
    check(W, "field listed", (await page.inputValue("[aria-label='Field 1 name']")) === "Budget (₹ lakh)");
  }
  await page.locator("[data-testid=lead-fields-card]").scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${S}/fields-${width}.png` });

  // 3. Import a CSV with that column (smart import wizard), once
  await page.goto(`${BASE}/#leads`);
  if (first) {
    await page.click("button:has-text('Import leads')");
    await page.fill("#import-paste", CSV);
    await page.click("button:has-text('Next: check the columns')");
    await page.waitForSelector("[aria-label='Field for Budget (₹ lakh)']");
    const mapped = await page.inputValue("[aria-label='Field for Budget (₹ lakh)']");
    check(W, "import maps the custom column", mapped === "custom:budget_lakh", mapped);
    await page.screenshot({ path: `${S}/import-map-${width}.png` });
    await page.click("button:has-text('Next: dry run')");
    await page.click("button:has-text('Import: 3 new')");
    await page.click("button:has-text('Close')");
  }
  await page.waitForSelector("table.leads-table, .leads-table");
  // 4. Column and filter
  if (first) check(W, "custom column header", await page.locator("th:has-text('Budget (₹ lakh)')").count() === 1);
  const cellText = await page.locator("td[data-label='Budget (₹ lakh)']").allTextContents();
  check(W, "custom column values", cellText.join(",").includes("140") && cellText.join(",").includes("85"), cellText.join(","));
  await page.fill("[aria-label='Budget (₹ lakh) from']", "80");
  const shownText = await page.textContent("text=/Showing \\d+ of/");
  check(W, "filter budget >= 80", /Showing 2 of 3/.test(shownText), shownText);
  await page.screenshot({ path: `${S}/leads-filter-${width}.png` });
  await page.fill("[aria-label='Budget (₹ lakh) from']", "");

  // 5. Edit in the drawer (Data tab), audited
  await page.locator("td[data-label=Company] button", { hasText: "Metro Brokers" }).click();
  await page.click("[role=tab]:has-text('Data')");
  await page.waitForSelector("[data-testid=lead-custom-fields]");
  await page.click("[data-testid=lead-custom-fields] >> button:has-text('Edit details')");
  const value = first ? "55" : "60";
  await page.fill("#cf-budget_lakh", value);
  await page.click("button:has-text('Save details')");
  await page.waitForSelector("[data-testid=lead-custom-fields] >> text=Saved 1 field");
  const dd = await page.textContent("[data-testid=lead-custom-fields] dl");
  check(W, "drawer edit saved", dd.includes(value), dd);
  await page.screenshot({ path: `${S}/drawer-${width}.png` });
  const changes = await page.evaluate((v) => fetch("/api/leads").then((r) => r.json()).then((ls) => ls.find((l) => l.company === "Metro Brokers"))
    .then((l) => fetch(`/api/leads/${l.id}/changes`).then((r) => r.json())), value);
  check(W, "edit audited", changes.some((c) => c.label === "Budget (₹ lakh)" && c.new === value), JSON.stringify(changes[0] || {}));
  await page.keyboard.press("Escape");

  // 6. Sequence preview with custom + brand placeholders (edit the preset's sequence step 1)
  await page.goto(`${BASE}/#templates`);
  const cardSel = page.locator(".card", { hasText: "Real estate enquiry follow-up" }).first();
  await cardSel.waitFor();
  await cardSel.locator("button", { hasText: /^Edit$/ }).click();
  const body = page.getByLabel("Email (A)").first();
  await body.waitFor();
  const text = await body.inputValue();
  if (!text.includes("{{custom.budget_lakh}}")) await body.fill(text.replace("{{greeting}}\n\n", "{{greeting}}\n\nYour budget on file: {{custom.budget_lakh}} lakh.\n\n"));
  const frame = page.frameLocator("iframe[title='Live email preview']");
  await page.waitForTimeout(1200);
  const html = await frame.locator("body").innerText();
  check(W, "preview fills brand", html.includes("Skyline Homes") && html.includes("2 and 3 BHK flats in Baner and Wakad.") && html.includes("Priya, Skyline Homes"), html.slice(0, 160).replace(/\n/g, " "));
  check(W, "preview fills custom", /Your budget on file: 25 lakh/.test(html));
  check(W, "preview nothing missing", await page.locator("text=Not filled:").count() === 0);
  await page.screenshot({ path: `${S}/preview-${width}.png` });

  // 7. LinkedIn page: step editor with the preset's steps and the 300 limit
  await page.goto(`${BASE}/#linkedin`);
  await page.waitForSelector("[data-testid=li-sequence-editor]");
  await page.click("[data-testid=li-sequence-editor] > summary");
  await page.waitForSelector("[data-testid=li-step-1] [data-testid=li-preview]");
  const p1 = await page.textContent("[data-testid=li-step-1] [data-testid=li-preview]");
  check(W, "LinkedIn step 1 preview", /property/.test(p1) && /\/ 300 characters/.test(p1), p1.slice(0, 120));
  await page.screenshot({ path: `${S}/linkedin-${width}.png` });

  // layout: no horizontal scroll
  const wide = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check(W, "no horizontal page scroll", wide <= 1, String(wide));
  check(W, "no console errors", errors.length === 0, errors.join(" | ").slice(0, 300));
  await page.close();
}

await run(1400);
await run(375);
await browser.close();
const failed = results.filter((r) => !r[2]);
console.log(failed.length ? `FAILED ${failed.length}` : `ALL ${results.length} CHECKS PASSED`);
process.exit(failed.length ? 1 : 0);
