// QA (area "analytics"): the Pipeline tab, done through the web UI as a person would.
//   Same rig as qa_admin.mjs (fresh API + built UI). cd web && E2E_UI=http://localhost:5284 node e2e/qa_pipeline.mjs
// Covers: funnel tiles total/attempted/connected/not interested/bad number match calls logged on the Activity tab,
// a tile opens its leads and a lead opens in the drawer, charts render, "added in period" toggle, phone width fits.
import { chromium } from "playwright";

const UI = process.env.E2E_UI || "http://localhost:5284";
const ADMIN = process.env.E2E_PASSWORD || "qa-admin-pass-1";
const SHOTS = process.env.SHOT_DIR || "/tmp";
const CSV = ["company,contact_name,title,email,phone,website",
  "Sharma Logistics Pvt Ltd,Rohit Sharma,Head of HR,rohit.sharma@sharmalogistics.example,+91 98110 23456,https://sharmalogistics.example",
  "Kaveri Textiles,Lakshmi Iyer,HR Manager,lakshmi@kaveritextiles.example,9845012345,kaveritextiles.example",
  "Konkan Realty Developers,Suresh Naik,Sales Head,suresh.naik@konkanrealty.example,9820098200,konkanrealty.example"].join("\n");
const results = [];
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
const p = await ctx.newPage();
const errors = [];
p.on("response", (r) => { if (r.url().includes("/api/") && r.status() >= 400) errors.push(`${r.status()} ${r.url().replace(UI, "")}`); });
p.on("pageerror", (e) => errors.push(`pageerror ${e.message}`));
const nav = async (label) => { await p.locator("nav.side").getByRole("button", { name: label, exact: true }).click(); await p.waitForTimeout(700); };
async function step(name, fn) {
  try { await fn(); results.push([true, name]); } catch (e) { results.push([false, `${name} — ${e.message.split("\n")[0]}`]); }
}
const tile = async (k) => Number((await p.getByTestId(`pipe-${k}`).locator("b").innerText()).trim());

async function logCall(company, outcome, confirm) {
  await nav("Leads");
  await p.getByRole("button", { name: company }).first().click();
  const d = p.getByRole("dialog").first();
  await d.getByRole("tab", { name: /Activity/ }).click();
  await d.getByLabel("Log a call in one click").getByRole("button", { name: outcome, exact: true }).click();
  if (confirm) { await p.getByTestId("app-dialog").waitFor(); await p.getByTestId("dialog-ok").click(); }
  await p.waitForTimeout(900);
  await p.keyboard.press("Escape");
  await p.waitForTimeout(500);
}

await step("Sign in and import 3 leads", async () => {
  await p.goto(UI);
  await p.getByLabel("Password").fill(ADMIN);
  await p.getByRole("button", { name: "Sign in" }).click();
  await p.locator("main").waitFor();
  const later = p.getByRole("button", { name: "Skip setup" });
  await later.waitFor({ timeout: 8000 }).then(async () => { await later.click(); await later.waitFor({ state: "detached", timeout: 20000 }); }, () => {});
  await nav("Leads");
  await p.getByRole("button", { name: "Quick paste" }).click();
  await p.locator("textarea").first().fill(CSV);
  await p.getByRole("button", { name: "Import", exact: true }).click();
  await p.getByText("Added 3").waitFor();
});
await step("Log calls on the Activity tab: not interested, no answer then inactive number", async () => {
  await logCall("Sharma Logistics Pvt Ltd", "Not interested", true);
  await logCall("Kaveri Textiles", "No answer", false);
  await logCall("Kaveri Textiles", "Inactive number", true);
});
await step("Pipeline tab is the default Analytics tab and tiles match", async () => {
  await nav("Analytics");
  await p.getByRole("tab", { name: "Pipeline", selected: true }).waitFor();
  await p.getByTestId("pipe-total").waitFor();
  const got = { total: await tile("total"), attempted: await tile("attempted"), connected: await tile("connected"),
    not_interested: await tile("not_interested"), bad_number: await tile("bad_number"), interested: await tile("interested") };
  const want = { total: 3, attempted: 2, connected: 1, not_interested: 1, bad_number: 1, interested: 0 };
  if (JSON.stringify(got) !== JSON.stringify(want)) throw new Error(`tiles ${JSON.stringify(got)}`);
});
await step("Charts render: funnel bars, donut, trend, call outcomes", async () => {
  if ((await p.getByTestId("pipeline-funnel").locator(".hbar-row").count()) !== 5) throw new Error("funnel bars");
  await p.getByTestId("trend-chart").waitFor();
  const calls = await p.getByTestId("call-outcomes").innerText();
  for (const t of ["Not interested", "No answer", "Inactive number"]) if (!calls.includes(t)) throw new Error(`call outcomes missing ${t}`);
  if (!(await p.locator(".donut-wrap svg").count())) throw new Error("no donut");
  await p.screenshot({ path: `${SHOTS}/pipeline-desktop.png`, fullPage: true });
});
await step("Not interested tile lists Sharma and opens the lead", async () => {
  await p.getByTestId("pipe-not_interested").click();
  const drill = p.getByTestId("pipeline-drill");
  await drill.getByRole("button", { name: "Sharma Logistics Pvt Ltd" }).click();
  await p.waitForTimeout(1500);
  const dt = await p.getByRole("dialog").first().innerText();
  if (!dt.includes("Sharma")) throw new Error(`drawer: ${dt.slice(0, 160)}`);
  await p.keyboard.press("Escape");
  await p.waitForTimeout(400);
  await drill.getByRole("button", { name: "Close" }).click();
});
await step("Not attempted bar lists Konkan only", async () => {
  await p.getByRole("button", { name: /^Not attempted: 1/ }).click();
  await p.getByTestId("pipeline-drill").getByRole("button", { name: "Konkan Realty Developers" }).waitFor();
  const text = await p.getByTestId("pipeline-drill").innerText();
  if (!text.includes("Konkan Realty Developers") || text.includes("Sharma")) throw new Error(text.slice(0, 200));
});
await step("By owner and by source tables show all 3 leads", async () => {
  for (const id of ["pipeline-by-owner", "pipeline-by-source"]) {
    const row = await p.getByTestId(id).locator("tbody tr").first().innerText();
    if (!/\b3\b/.test(row)) throw new Error(`${id}: ${row}`);
  }
});
await step("Only-added-in-period toggle keeps today's leads", async () => {
  await p.getByTestId("pipeline-added").check();
  await p.waitForTimeout(800);
  if ((await tile("total")) !== 3) throw new Error("total changed");
});
await step("Phone width: no sideways scroll", async () => {
  await p.setViewportSize({ width: 390, height: 844 });
  await p.waitForTimeout(500);
  const over = await p.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  await p.screenshot({ path: `${SHOTS}/pipeline-phone.png`, fullPage: true });
  if (over > 2) throw new Error(`overflow ${over}px`);
});

await browser.close();
for (const [ok, name] of results) console.log(`${ok ? "PASS" : "FAIL"} ${name}`);
console.log(`errors: ${JSON.stringify(errors)}`);
process.exit(results.every(([ok]) => ok) && !errors.length ? 0 : 1);
