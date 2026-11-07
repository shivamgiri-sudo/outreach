// QA (areas "alerts", "response times", "tracking UI"), only through the web UI.
//   Same rig as qa_admin.mjs. cd web && E2E_UI=http://localhost:5284 node e2e/qa_alerts.mjs
// Covers: a pasted reply and a call logged as interested raise bell alerts; Reports > Response times shows the
// numbers and owners can save targets, alert managers and the tracking switch; Outbox still renders.
import { chromium } from "playwright";

const UI = process.env.E2E_UI || "http://localhost:5284";
const ADMIN = process.env.E2E_PASSWORD || "qa-admin-pass-1";
const SHOTS = process.env.SHOT_DIR || "/tmp";
const CSV = ["company,contact_name,title,email,phone,website",
  "Sharma Logistics Pvt Ltd,Rohit Sharma,Head of HR,rohit.sharma@sharmalogistics.example,+91 98110 23456,https://sharmalogistics.example",
  "Kaveri Textiles,Lakshmi Iyer,HR Manager,lakshmi@kaveritextiles.example,9845012345,kaveritextiles.example"].join("\n");
const results = [];
const browser = await chromium.launch();
const p = await (await browser.newContext({ viewport: { width: 1400, height: 950 } })).newPage();
const errors = [];
p.on("response", (r) => { if (r.url().includes("/api/") && r.status() >= 400) errors.push(`${r.status()} ${r.url().replace(UI, "")}`); });
p.on("pageerror", (e) => errors.push(`pageerror ${e.message}`));
const nav = async (label) => { await p.locator("nav.side").getByRole("button", { name: label, exact: true }).click(); await p.waitForTimeout(900); };
async function step(name, fn) {
  try { await fn(); results.push([true, name]); } catch (e) { results.push([false, `${name} — ${e.message.split("\n")[0]}`]); }
}
const bellText = async () => {
  await p.reload(); await p.locator("main").waitFor(); await p.waitForTimeout(1500);
  await p.locator("button.bell").click();
  const t = await p.locator(".bell-panel").innerText();
  await p.locator("button.bell").click();
  return t;
};

await step("Sign in and import two leads", async () => {
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
await step("A pasted reply raises a 'New reply' alert on the bell", async () => {
  await p.getByRole("button", { name: "Sharma Logistics Pvt Ltd" }).first().click();
  const d = p.getByRole("dialog").first();
  await d.getByRole("tab", { name: "Outreach" }).click();
  await d.locator("#reply-box").fill("Can you share your pricing?");
  await d.getByRole("button", { name: "Log reply" }).click();
  await p.waitForTimeout(1200);
  await p.keyboard.press("Escape");
  const t = await bellText();
  if (!/New reply: Sharma Logistics/.test(t)) throw new Error(`bell: ${t.slice(0, 200)}`);
});
await step("A 'yes' reply raises a 'Lead said yes' alert", async () => {
  await nav("Leads");
  await p.getByRole("button", { name: "Kaveri Textiles" }).first().click();
  const d = p.getByRole("dialog").first();
  await d.getByRole("tab", { name: "Outreach" }).click();
  await d.locator("#reply-box").fill("Yes, interested. Please call me tomorrow.");
  await d.getByRole("button", { name: "Log reply" }).click();
  await p.waitForTimeout(1200);
  await p.keyboard.press("Escape");
  const t = await bellText();
  if (!/Lead said yes: Kaveri Textiles/.test(t)) throw new Error(`bell: ${t.slice(0, 240)}`);
});
await step("Reports > Response times shows the numbers", async () => {
  await nav("Reports");
  await p.getByRole("tab", { name: "Response times" }).click();
  await p.getByTestId("sla-tiles").waitFor();
  const tiles = await p.getByTestId("sla-tiles").innerText();
  if (!/Typical time to first attempt/.test(tiles) || !/Replies waiting, past target/.test(tiles)) throw new Error(tiles.slice(0, 200));
  await p.screenshot({ path: `${SHOTS}/response-times.png`, fullPage: true });
});
await step("Owner saves targets, alert managers and the tracking switch", async () => {
  const f = p.getByTestId("sla-settings");
  await f.getByLabel("First attempt within (hours)").fill("12");
  await f.getByLabel(/Also alert these people/).fill("Boss Person");
  await f.getByLabel("Track email opens and link clicks").uncheck();
  await f.getByRole("button", { name: "Save" }).click();
  await f.getByText("Saved.").waitFor();
  await p.getByTestId("sla-tiles").getByText("target 12 h").waitFor();
  await p.reload(); await p.locator("main").waitFor(); await p.waitForTimeout(1200);
  await nav("Reports");
  await p.getByRole("tab", { name: "Response times" }).click();
  const g = p.getByTestId("sla-settings");
  await g.waitFor();
  if (await g.getByLabel("Track email opens and link clicks").isChecked()) throw new Error("tracking switch not saved");
  if ((await g.getByLabel(/Also alert these people/).inputValue()) !== "Boss Person") throw new Error("managers not saved");
  await g.getByLabel("Track email opens and link clicks").check();
  await g.getByRole("button", { name: "Save" }).click();
  await g.getByText("Saved.").waitFor();
});
await step("Outbox renders", async () => {
  await nav("Outbox & sending");
  await p.locator("main").getByText(/Outbox/).first().waitFor();
});

await browser.close();
for (const [ok, name] of results) console.log(`${ok ? "PASS" : "FAIL"} ${name}`);
console.log(`errors: ${JSON.stringify(errors)}`);
process.exit(results.every(([ok]) => ok) && !errors.length ? 0 : 1);
