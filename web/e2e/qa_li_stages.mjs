// QA (area "LinkedIn stage board, account per lead"), only through the web UI. Same rig as qa_admin.mjs.
// Covers: leads with LinkedIn links show on the stage board ("not contacted", then "request queued" after a start), a
// stage opens its list, the CSV downloads, the lead drawer offers "Send from LinkedIn account", the Leads stage filter.
import { chromium } from "playwright";

const UI = process.env.E2E_UI || "http://localhost:5284";
const ADMIN = process.env.E2E_PASSWORD || "qa-admin-pass-1";
const SHOTS = process.env.SHOT_DIR || "/tmp";
const CSV = ["company,contact_name,title,email,website,linkedin_url",
  "Sharma Logistics,Rohit Sharma,Head of HR,rohit@sharmalogistics.example,https://sharmalogistics.example,https://www.linkedin.com/in/rohit-sharma-qa",
  "Kaveri Textiles,Lakshmi Iyer,HR Manager,lakshmi@kaveritextiles.example,https://kaveritextiles.example,https://www.linkedin.com/in/lakshmi-iyer-qa"].join("\n");
const results = [];
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 }, acceptDownloads: true });
const p = await ctx.newPage();
const errors = [];
p.on("response", (r) => { if (r.url().includes("/api/") && r.status() >= 400) errors.push(`${r.status()} ${r.url().replace(UI, "")}`); });
p.on("pageerror", (e) => errors.push(`pageerror ${e.message}`));
const nav = async (label) => { await p.locator("nav.side").getByRole("button", { name: label, exact: true }).click(); await p.waitForTimeout(900); };
const toLinkedIn = async (tab = "stages") => { await p.evaluate((t) => { location.hash = `#linkedin?tab=${t}`; }, tab); await p.waitForTimeout(1500); };
async function step(name, fn) {
  try { await fn(); results.push([true, name]); } catch (e) { results.push([false, `${name} — ${e.message.split("\n")[0]}`]); }
}
await step("Sign in and import two leads with LinkedIn links", async () => {
  await p.goto(UI);
  await p.getByLabel("Password").fill(ADMIN);
  await p.getByRole("button", { name: "Sign in" }).click();
  await p.locator("main").waitFor();
  const later = p.getByRole("button", { name: "Skip setup" });
  await later.waitFor({ timeout: 6000 }).then(async () => { await later.click(); await later.waitFor({ state: "detached", timeout: 20000 }); }, () => {});
  await nav("Leads");
  await p.getByRole("button", { name: "Quick paste" }).click();
  await p.locator("textarea").first().fill(CSV);
  await p.getByRole("button", { name: "Import", exact: true }).click();
  await p.getByText("Added 2").waitFor();
});
await step("Stage board counts them as not contacted", async () => {
  await toLinkedIn();
  const board = p.getByTestId("li-stage-board");
  await board.waitFor();
  const text = await board.innerText();
  if (!/not contacted/i.test(text)) throw new Error(text.slice(0, 300));
});
await step("After starting one, it moves to the next stage and the list opens", async () => {
  await toLinkedIn("today");
  await p.getByRole("button", { name: "Start LinkedIn sequence" }).first().click();
  await p.waitForTimeout(1500);
  await toLinkedIn("stages");
  await p.reload(); await p.locator("main").waitFor(); await p.waitForTimeout(1500);
  const board = p.getByTestId("li-stage-board");
  const row = board.locator("tr", { hasText: /queued|waiting to connect|request/i }).first();
  await row.click();
  await p.getByTestId("li-stage-list").waitFor();
  const list = await p.getByTestId("li-stage-list").innerText();
  if (!/Sharma|Kaveri/.test(list)) throw new Error(list.slice(0, 300));
  await p.screenshot({ path: `${SHOTS}/li-stage-board.png`, fullPage: true });
});
await step("Stage list downloads as CSV", async () => {
  const [dl] = await Promise.all([p.waitForEvent("download"), p.getByTestId("li-stage-csv").click()]);
  if (!/\.csv$/.test(dl.suggestedFilename())) throw new Error(dl.suggestedFilename());
});
await step("Lead drawer: no LinkedIn account connected yet, so no account picker (it appears once an account exists)", async () => {
  await nav("Leads");
  await p.getByRole("button", { name: "Kaveri Textiles" }).first().click();
  const d = p.getByRole("dialog").first();
  await d.getByRole("tab", { name: "Outreach" }).click();
  await p.waitForTimeout(1200);
  if (await d.getByTestId("linkedin-account-pick").count()) throw new Error("picker shown without any account");
  await p.keyboard.press("Escape");
});
await step("Leads list filters by LinkedIn stage", async () => {
  const f = p.getByTestId("li-stage-filter");
  const opts = await f.locator("option").allInnerTexts();
  const pick = opts.find((o) => /not contacted/i.test(o));
  if (!pick) throw new Error(opts.join(" | "));
  await f.selectOption({ label: pick });
  await p.waitForTimeout(800);
  const rows = await p.locator("main table tbody tr").count();
  if (rows !== 1) throw new Error(`rows ${rows}`);
});
await browser.close();
for (const [ok, name] of results) console.log(`${ok ? "PASS" : "FAIL"} ${name}`);
console.log(`errors: ${JSON.stringify(errors)}`);
process.exit(results.every(([ok]) => ok) && !errors.length ? 0 : 1);
