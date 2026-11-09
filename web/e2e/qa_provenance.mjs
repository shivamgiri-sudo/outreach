// QA ("why was this sent?"), only through the web UI. Needs a running app with at least one sequence email and one
// one-off email in the outbox.   cd web && E2E_UI=http://localhost:5173 node e2e/qa_provenance.mjs
// Covers: Automatic / Manual pills, the "Who sent it" filter, the "Why it was sent" panel, the Internal emails tab and
// the CSV download with provenance columns.
import { chromium } from "playwright";

const UI = process.env.E2E_UI || "http://localhost:5284";
const ADMIN = process.env.E2E_PASSWORD || "qa-admin-pass-1";
const SHOTS = process.env.SHOT_DIR || "/tmp";
const results = [];
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 }, acceptDownloads: true });
const p = await ctx.newPage();
const errors = [];
p.on("response", (r) => { if (r.url().includes("/api/") && r.status() >= 400) errors.push(`${r.status()} ${r.url().replace(UI, "")}`); });
p.on("pageerror", (e) => errors.push(`pageerror ${e.message}`));
async function step(name, fn) {
  try { await fn(); results.push([true, name]); } catch (e) { results.push([false, `${name} — ${e.message.split("\n")[0]}`]); }
}

await step("Sign in and open the Outbox", async () => {
  await p.goto(UI);
  await p.getByLabel("Password").fill(ADMIN);
  await p.getByRole("button", { name: "Sign in" }).click();
  await p.locator("main").waitFor();
  const later = p.getByRole("button", { name: "Skip setup" });
  await later.waitFor({ timeout: 6000 }).then(async () => { await later.click(); await later.waitFor({ state: "detached", timeout: 20000 }); }, () => {});
  await p.evaluate(() => { window.location.hash = "outbox"; });
  await p.getByTestId("outbox-table").waitFor({ timeout: 10000 });
});

await step("Rows show Automatic and Manual pills", async () => {
  const pills = await p.getByTestId("outbox-table").getByTestId("origin-pill").allInnerTexts();
  if (!pills.includes("Automatic") || !pills.includes("Manual")) throw new Error(`pills: ${pills.join(", ")}`);
});

await step("Why it was sent opens the chain", async () => {
  await p.getByTestId("why-toggle").first().click();
  const text = await p.getByTestId("why-row").innerText();
  if (!/Why it was sent/.test(text) || !/(Sent automatically|Sent by)/.test(text)) throw new Error(text);
  await p.screenshot({ path: `${SHOTS}/provenance-why.png`, fullPage: false });
});

await step("Who sent it filter keeps only manual rows", async () => {
  await p.getByLabel("Who sent it").selectOption("manual");
  await p.waitForTimeout(800);
  const pills = await p.getByTestId("outbox-table").getByTestId("origin-pill").allInnerTexts();
  if (!pills.length || pills.some((x) => x !== "Manual")) throw new Error(`pills: ${pills.join(", ")}`);
  await p.getByLabel("Who sent it").selectOption("");
});

await step("Download with why gives a CSV with provenance columns", async () => {
  const [dl] = await Promise.all([p.waitForEvent("download"), p.getByTestId("export-audit").click()]);
  const path = await dl.path();
  const { readFileSync } = await import("node:fs");
  const head = readFileSync(path, "utf8").split("\n")[0];
  for (const col of ["origin_type", "trigger", "actor_name", "enrolled_by", "enrolled_via", "consent_basis", "why"]) {
    if (!head.includes(col)) throw new Error(`missing ${col}: ${head}`);
  }
});

await step("Internal emails tab renders", async () => {
  await p.getByTestId("tab-internal").click();
  await p.waitForTimeout(800);
  const text = await p.locator("main").innerText();
  if (!/No internal emails yet|When \(IST\)/.test(text)) throw new Error("internal tab did not render");
  await p.screenshot({ path: `${SHOTS}/provenance-internal.png`, fullPage: false });
});

await browser.close();
for (const [ok, name] of results) console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
if (errors.length) console.log("API/page errors:\n  " + errors.join("\n  "));
process.exit(results.every(([ok]) => ok) && !errors.length ? 0 : 1);
