// Visits every page at desktop, tablet and phone width: screenshots, console errors, horizontal overflow.
// Run after `npm run e2e` against the same servers so the pages have data in them.
import { chromium } from "playwright";
import fs from "node:fs";

const UI = process.env.E2E_UI || "http://localhost:5173";
const SHOTS = process.env.SHOT_DIR || "tour-shots";
fs.mkdirSync(SHOTS, { recursive: true });
const PAGES = ["Dashboard", "Leads", "Campaigns", "Data hygiene", "Data sources", "Insights", "Free experience", "Workflows",
  "Outbox & sending", "Tasks & follow-ups", "LinkedIn outreach", "Priya's call desk", "Meetings", "Sequences & templates",
  "Success stories", "Prompt generator", "Setup", "Integrations"];
const SIZES = [["desktop", 1440, 900], ["tablet", 820, 1100], ["phone", 390, 844]];

const browser = await chromium.launch(process.env.HEADED ? { headless: false, slowMo: 300 } : {});
const issues = [];
let visited = 0;
for (const [size, width, height] of SIZES) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => issues.push(`${size}: page error ${e.message}`));
  page.on("console", (m) => m.type() === "error" && !m.text().includes("status of 400") && issues.push(`${size}: console ${m.text()}`));
  await page.goto(UI);
  for (const label of PAGES) {
    await page.getByRole("button", { name: label }).first().click();
    await page.waitForTimeout(500);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    if (overflow > 1) issues.push(`${size}: ${label} scrolls sideways by ${overflow}px`);
    const heading = await page.locator("main h2").first().textContent().catch(() => null);
    if (!heading) issues.push(`${size}: ${label} has no heading`);
    await page.screenshot({ path: `${SHOTS}/${size}-${label.replace(/[^a-z]+/gi, "-").toLowerCase()}.png`, fullPage: true });
    visited += 1;
  }
  await ctx.close();
}
await browser.close();
console.log(`${visited} page views captured in ${SHOTS}`);
console.log(issues.length ? `Problems:\n${issues.join("\n")}` : "No console errors, missing headings or sideways scrolling.");
