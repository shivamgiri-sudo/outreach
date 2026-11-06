// Leads owner / bulk / tags / saved views / scoring: the real UI at 1400px and 375px, failing on any console error.
//
//   API: OUTREACH_DATA_FILE=<fresh temp>/d.json OUTREACH_ADMIN_PASSWORD= ../.venv/bin/uvicorn app.main:app --port 8041
//   UI:  npx vite build --outDir <temp>/dist, served with /api proxied to the API (E2E_UI, default http://127.0.0.1:5241)
//   cd web && node e2e/leads_owner.mjs        (seeds 70 leads + a team through the API first; needs an empty data file)
import { chromium } from "playwright";
import fs from "node:fs";

const BASE = process.env.E2E_UI || "http://127.0.0.1:5241";
const S = process.env.SHOT_DIR || "e2e-shots/leads-owner";
fs.mkdirSync(S, { recursive: true });
const post = (path, body, method = "POST") => fetch(BASE + path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((r) => r.json());
const rows = ["company,contact_name,email,website,open_roles,company_size,country"];
for (let i = 0; i < 70; i++) rows.push(`Firm ${String(i).padStart(2, "0")},Person ${i},hr${i}@firm${i}.in,https://firm${i}.in,${(i * 7) % 90},${[10, 200, 5000][i % 3]},IN`);
await post("/api/leads/import", { csv: rows.join("\n"), source: "apollo" });
await post("/api/team", { team: [{ name: "Priya" }, { name: "Amit" }] }, "PUT");

const browser = await chromium.launch();
let failed = false;
const log = (...a) => console.log(...a);
async function run(width) {
  const page = await browser.newPage({ viewport: { width, height: width > 500 ? 900 : 800 } });
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(`${BASE}/#/leads`); await page.waitForSelector("[aria-label='Filter by owner']");
  const W = `[${width}]`;
  // columns
  if (width > 800) for (const h of ["Owner", "Tags", "Last activity", "Next task due"]) log(W, "column", h, await page.locator(`th:has-text("${h}")`).count() > 0);
  // select all shown (all filter pages) -> confirm counts off-page leads
  const pickAll = async () => {
    if (width > 800) return page.check("[aria-label='Select all shown']");
    const n = await page.locator("td.pick input").count();
    for (let i = 0; i < n; i++) await page.locator("td.pick input").nth(i).check();
  };
  if (width > 800) await pickAll();
  else { await page.locator("td.pick input").nth(0).check(); await page.click(".pager >> button:has-text(\"Next\")"); await page.locator("td.pick input").nth(0).check(); }
  await page.click("[data-testid=bulkbar] >> text=Start email sequence");
  const countText = await page.textContent("[data-testid=confirm-count]");
  log(W, "confirm text:", countText.trim());
  await page.waitForTimeout(400); await page.screenshot({ path: `${S}/confirm-${width}.png` });
  await page.click("[data-testid=confirm-dialog] >> text=Cancel");
  log(W, "dialog closed:", await page.locator("[data-testid=confirm-dialog]").count() === 0);
  await page.click("[data-testid=bulkbar] >> text=Clear");
  if (width <= 800) await page.click(".pager >> button:has-text(\"Previous\")");
  // pick 3 on page and assign to Priya via bulk
  const boxes = page.locator("td.pick input");
  for (let i = 0; i < 3; i++) await boxes.nth(i).check();
  await page.selectOption("[aria-label='Assign selected leads to']", "Priya");
  await page.click("[data-testid=confirm-dialog] >> text=Yes, assign");
  await page.waitForSelector("[role=status]:has-text('Assigned')");
  log(W, "assign msg:", (await page.textContent("[role=status]")).trim().slice(0, 90));
  // owner filter
  await page.selectOption("[aria-label='Filter by owner']", "Priya");
  log(W, "showing:", (await page.textContent("text=/Showing \\d+ of/")).trim());
  // tag via bulk
  await pickAll();
  await page.fill("[aria-label='Tag for selected leads']", `vip${width}`);
  await page.click("[data-testid=bulkbar] >> text=Add tag");
  await page.waitForSelector("[role=status]:has-text('Tagged')");
  log(W, "tag msg:", (await page.textContent("[role=status]")).trim().slice(0, 90));
  // stage with reasons
  await pickAll();
  await page.selectOption("[aria-label='Change stage of selected leads']", "contacted");
  await page.click("[data-testid=confirm-dialog] >> text=Yes, move them");
  await page.waitForSelector("[role=status]:has-text('Moved')");
  log(W, "stage msg:", (await page.textContent("[role=status]")).trim().slice(0, 120));
  // save view
  await page.click("[data-testid=lead-views] >> text=Save view");
  await page.fill("[aria-label='View name']", `Priya ${width}`);
  await page.click("[data-testid=lead-views] >> button:has-text('Save')");
  await page.waitForTimeout(400);
  await page.selectOption("[aria-label='Filter by owner']", "");
  await page.selectOption("[aria-label='Saved views']", { label: `Priya ${width}` });
  log(W, "view applied owner =", await page.inputValue("[aria-label='Filter by owner']"));
  await page.screenshot({ path: `${S}/leads-${width}.png`, fullPage: false });
  // drawer owner + tags
  await page.locator("td[data-label=Company] button").first().click();
  await page.waitForSelector("[data-testid=lead-owner]");
  await page.selectOption("[data-testid=lead-owner] select", "Amit");
  await page.waitForTimeout(400);
  await page.fill("[aria-label='New tag']", "Noida BPO");
  await page.click("[data-testid=lead-tags] >> text=Add tag");
  await page.waitForSelector("[data-testid=lead-tags] .pill:has-text('Noida BPO')");
  log(W, "drawer owner:", await page.inputValue("[data-testid=lead-owner] select"), "tags:", (await page.locator("[data-testid=lead-tags] .pill").allTextContents()).join("|"));
  await page.screenshot({ path: `${S}/drawer-${width}.png` });
  await page.click(".drawer-head >> text=Close");
  // do-not-contact on one lead
  await page.selectOption("[aria-label='Saved views']", "");
  await page.selectOption("[aria-label='Filter by owner']", "__none");
  await page.locator("td.pick input").first().check();
  await page.click("[data-testid=bulkbar] >> text=Do not contact");
  await page.click("[data-testid=confirm-dialog] >> text=Yes, never contact them");
  await page.waitForSelector("[role=status]:has-text('do-not-contact')");
  log(W, "dnc msg:", (await page.textContent("[role=status]")).trim().slice(0, 90));
  // delete one (owner)
  await page.locator("td.pick input").first().check();
  await page.click("[data-testid=bulkbar] >> button:has-text('Delete')");
  await page.click("[data-testid=confirm-dialog] >> text=/Yes, delete/");
  await page.waitForSelector("[role=status]:has-text('Deleted')");
  log(W, "delete msg:", (await page.textContent("[role=status]")).trim().slice(0, 90));
  // horizontal overflow
  log(W, "page horizontal scroll:", await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth));
  // Setup scoring card
  await page.goto(`${BASE}/#/setup`); await page.click("text=Team & workspaces");
  await page.waitForSelector("[data-testid=scoring-card]");
  await page.fill("[data-testid=scoring-card] label:has-text('Hot from') input", "65");
  await page.click("text=Save scoring");
  await page.waitForSelector("[data-testid=scoring-card] [role=status]");
  log(W, "scoring:", (await page.textContent("[data-testid=scoring-card] [role=status]")).trim());
  await page.screenshot({ path: `${S}/scoring-${width}.png` });
  await page.click("text=Back to defaults"); await page.waitForTimeout(300);
  // Lists confirm
  await page.goto(`${BASE}/#/leads`); await page.waitForSelector("[aria-label='Filter by owner']");
  await page.locator("td.pick input").nth(0).check(); await page.locator("td.pick input").nth(1).check();
  page.once("dialog", (d) => d.accept(`List ${width}`));
  await page.click("[data-testid=bulkbar] >> text=Save as list");
  await page.waitForSelector("[role=status]:has-text('Saved')");
  await page.goto(`${BASE}/#/lists`); await page.click(`text=List ${width}`);
  await page.click("text=Start email for all");
  log(W, "list confirm:", (await page.textContent("[data-testid=confirm-count]")).trim());
  await page.click("[data-testid=confirm-dialog] >> text=/Yes, start/");
  await page.waitForSelector("[role=status]:has-text('Email started')");
  log(W, "list result:", (await page.textContent("[role=status]:has-text('Email started')")).trim());
  log(W, "console errors:", errors.length ? errors.join(" || ") : "none");
  if (errors.length) failed = true;
  await page.close();
}
async function enrollOk() {
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  await page.goto(`${BASE}/#/leads`); await page.waitForSelector("[aria-label='Filter by owner']");
  await page.fill("[aria-label='Search leads']", "Firm 6");
  await page.check("[aria-label='Select all shown']");
  await page.click("[data-testid=bulkbar] >> text=Start email sequence");
  await page.keyboard.press("Escape");
  await page.click("[data-testid=bulkbar] >> text=Start email sequence");
  await page.click("[data-testid=confirm-dialog] >> text=/Yes, start/");
  await page.waitForSelector("[role=status]:has-text('Started the email sequence for 10 of 10')");
  log("[1400] bulk enroll ok");
  await page.close();
}
await enrollOk(); await run(1400); await run(375);
await browser.close();
process.exit(failed ? 1 : 0);
