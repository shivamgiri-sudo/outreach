// Opens the "How do I get this?" guide on every integration card, wizard, lead-capture type, Setup connector, checklist row
// and app form, at desktop (1400) and phone (375) width. Fails on console errors, sideways scrolling or a guide that is missing.
// Needs a fresh API (OUTREACH_DATA_FILE on a throwaway file) and the built UI: E2E_UI=http://127.0.0.1:5261 node e2e/guides.mjs
import { chromium } from "playwright";

const UI = process.env.E2E_UI || "http://localhost:5173";
const SIZES = [["desktop", 1400, 900], ["phone", 375, 812]];
const issues = [];
const counts = {};
const count = (what) => { counts[what] = (counts[what] || 0) + 1; };

async function overflow(page, where) {
  const px = await page.evaluate(() => {
    const doc = document.documentElement.scrollWidth - document.documentElement.clientWidth;
    const panel = document.querySelector("[data-testid=guide-panel]");
    return Math.max(doc, panel ? panel.scrollWidth - panel.clientWidth : 0);
  });
  if (px > 1) issues.push(`${where}: scrolls sideways by ${px}px`);
}

// Clicks a guide button, checks the panel shows a real guide, then closes it with Escape.
async function openGuide(page, button, where) {
  await button.scrollIntoViewIfNeeded();
  await button.click();
  const panel = page.getByTestId("guide-panel");
  await panel.waitFor({ timeout: 5000 });
  const steps = await panel.locator(".guide-steps li").count();
  const title = (await panel.locator("#guide-title").textContent()) || "";
  if (!steps || !title.trim() || title === "Setup guide") issues.push(`${where}: guide did not render (title "${title}")`);
  const focused = await page.evaluate(() => document.activeElement?.id);
  if (focused !== "guide-title") issues.push(`${where}: focus did not move into the guide`);
  await overflow(page, `${where} (guide open)`);
  await page.keyboard.press("Escape");
  await panel.waitFor({ state: "detached", timeout: 3000 });
  count("guides opened");
}

async function go(page, hash) {
  await page.goto("about:blank");                 // a hash-only change would not reload the page
  await page.goto(`${UI}/#${hash}`);
  await page.waitForLoadState("networkidle");
}

async function integrations(page, size) {
  await go(page, "integrations");
  const tabs = await page.locator("[role=tablist][aria-label=Channels] [role=tab]").evaluateAll((els) => els.map((e) => e.dataset.testid));
  for (const tab of tabs.filter((t) => !["tab-capture", "tab-webhooks", "tab-custom"].includes(t))) {
    await page.getByTestId(tab).click();
    const cards = await page.locator(".prov-card").evaluateAll((els) => els.map((e) => e.dataset.testid));
    for (const card of cards) {
      const where = `${size} ${card}`;
      const box = page.getByTestId(card);
      const guide = box.locator("[data-testid^=card-guide-]");
      if (!(await guide.count())) { issues.push(`${where}: no guide button on the card`); continue; }
      await openGuide(page, guide, `${where} card`);
      await box.getByRole("button", { name: /^(Set up|Open)$/ }).click();
      const wizard = page.getByTestId("wizard");
      await wizard.waitFor();
      await openGuide(page, wizard.getByTestId("wizard-guide"), `${where} wizard`);
      await wizard.getByRole("button", { name: /2\. Your details/ }).click();
      if (size === "desktop") await openGuide(page, wizard.getByTestId("wizard-guide-details"), `${where} details step`);
      await overflow(page, `${where} wizard`);
      await page.keyboard.press("Escape");
      await wizard.waitFor({ state: "detached" });
      count("cards");
    }
  }
}

async function capture(page, size) {
  await go(page, "integrations?tab=capture");
  await page.getByTestId("add-capture").click();
  const types = await page.locator("input[name=capture-type]").count();
  for (let i = 0; i < types; i += 1) {
    if (i > 0) { await page.getByTestId("add-capture").click(); }
    await page.locator("input[name=capture-type]").nth(i).check();
    await openGuide(page, page.getByTestId("add-capture-guide"), `${size} add capture type ${i}`);
    await page.getByRole("button", { name: "Create connector" }).click();
    const panel = page.getByTestId("capture-panel");
    await panel.waitFor();
    await openGuide(page, panel.getByTestId("capture-guide"), `${size} capture connector ${i}`);
    await overflow(page, `${size} capture connector ${i}`);
    await page.keyboard.press("Escape");
    await panel.waitFor({ state: "detached" });
    count("capture types");
  }
}

async function allButtons(page, selector, where) {
  const ids = await page.locator(selector).evaluateAll((els) => els.map((e) => e.dataset.testid));
  if (!ids.length) issues.push(`${where}: no guide buttons found`);
  for (const id of ids) await openGuide(page, page.getByTestId(id).first(), `${where} ${id}`);
  await overflow(page, where);
}

async function setupAndApp(page, size) {
  await go(page, "setup?tab=checklist");
  await allButtons(page, "[data-testid^=checklist-guide-]", `${size} checklist`);
  await go(page, "setup?tab=connections");
  await allButtons(page, "[data-testid^=setup-guide-], [data-testid^=basics-guide-]", `${size} setup connectors`);
  await go(page, "setup?tab=team");
  await allButtons(page, "[data-testid=workspaces-guide], [data-testid=users-guide]", `${size} team`);
  await go(page, "setup?tab=backup");
  await allButtons(page, "[data-testid=backup-guide]", `${size} backup`);
  await go(page, "linkedin");
  await page.getByTestId("chrome-assistant").locator("summary").click();
  await allButtons(page, "[data-testid=agent-guide], [data-testid=extension-guide]", `${size} linkedin`);
  await go(page, "sources");
  await allButtons(page, "[data-testid^=source-guide-]", `${size} data sources`);
  await go(page, "help");
  await page.getByTestId("help-search").fill("gmail app password");
  const first = page.locator("[data-testid^=help-card-]").first();
  await openGuide(page, first.getByRole("button", { name: /Open guide/ }), `${size} help search`);
  await page.getByTestId("help-search").fill("");
  const all = await page.locator("[data-testid^=help-card-]").count();
  if (all < 90) issues.push(`${size} help: only ${all} guides listed`);
  await overflow(page, `${size} help`);
}

const browser = await chromium.launch(process.env.HEADED ? { headless: false, slowMo: 200 } : {});
for (const [size, width, height] of SIZES) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => issues.push(`${size}: page error ${e.message}`));
  page.on("console", (m) => m.type() === "error" && issues.push(`${size}: console ${m.text()}`));
  for (const step of [integrations, capture, setupAndApp].filter((f) => !process.env.ONLY || process.env.ONLY.split(",").includes(f.name))) {
    try { await step(page, size); } catch (e) { issues.push(`${size} ${step.name}: ${e.message.split("\n").slice(0, 3).join(" | ")}`); }
  }
  await ctx.close();
}
await browser.close();
console.log(JSON.stringify(counts));
console.log(issues.length ? `Problems (${issues.length}):\n${issues.join("\n")}` : "Every guide opened: no console errors, no sideways scrolling.");
process.exit(issues.length ? 1 : 0);
