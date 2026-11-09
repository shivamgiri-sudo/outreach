// QA (area "Excel import"), only through the web UI. Same rig as qa_admin.mjs.
//   cd web && E2E_UI=http://localhost:5284 E2E_XLSX=/path/leads.xlsx node e2e/qa_excel.mjs
// Covers: an .xlsx with three sheets is uploaded in Import leads; the first sheet with data is read, another sheet can be
// picked, and the leads import exactly as from a CSV.
import { chromium } from "playwright";

const UI = process.env.E2E_UI || "http://localhost:5284";
const ADMIN = process.env.E2E_PASSWORD || "qa-admin-pass-1";
const XLSX = process.env.E2E_XLSX;
const results = [];
const browser = await chromium.launch();
const p = await (await browser.newContext({ viewport: { width: 1400, height: 950 } })).newPage();
const errors = [];
p.on("response", (r) => { if (r.url().includes("/api/") && r.status() >= 400) errors.push(`${r.status()} ${r.url().replace(UI, "")}`); });
p.on("pageerror", (e) => errors.push(`pageerror ${e.message}`));
async function step(name, fn) {
  try { await fn(); results.push([true, name]); } catch (e) { results.push([false, `${name} — ${e.message.split("\n")[0]}`]); }
}
await step("Sign in and open Import leads", async () => {
  await p.goto(UI);
  await p.getByLabel("Password").fill(ADMIN);
  await p.getByRole("button", { name: "Sign in" }).click();
  await p.locator("main").waitFor();
  const later = p.getByRole("button", { name: "Skip setup" });
  await later.waitFor({ timeout: 6000 }).then(async () => { await later.click(); await later.waitFor({ state: "detached", timeout: 20000 }); }, () => {});
  await p.locator("nav.side").getByRole("button", { name: "Leads", exact: true }).click();
  await p.getByRole("button", { name: "Import leads" }).click();
});
await step("Upload the .xlsx: the first sheet with data is read", async () => {
  await p.locator("#import-file").setInputFiles(XLSX);
  await p.getByTestId("import-loaded").filter({ hasText: 'sheet "Leads" · 2 rows' }).waitFor({ timeout: 15000 });
});
await step("Another sheet can be picked, then back", async () => {
  await p.getByTestId("import-sheet").selectOption("Other");
  await p.getByTestId("import-loaded").filter({ hasText: 'sheet "Other" · 1 rows' }).waitFor({ timeout: 15000 });
  await p.getByTestId("import-sheet").selectOption("Leads");
  await p.getByTestId("import-loaded").filter({ hasText: 'sheet "Leads"' }).waitFor({ timeout: 15000 });
});
await step("Columns, dry run and import work as for a CSV", async () => {
  await p.getByRole("button", { name: "Next: check the columns" }).click();
  await p.getByRole("button", { name: "Next: dry run" }).click();
  await p.getByRole("button", { name: /^Import: 2 new/ }).click();
  await p.getByText(/Done\. Added\s*2\s*new lead/).waitFor();
});
await browser.close();
for (const [ok, name] of results) console.log(`${ok ? "PASS" : "FAIL"} ${name}`);
console.log(`errors: ${JSON.stringify(errors)}`);
process.exit(results.every(([ok]) => ok) && !errors.length ? 0 : 1);
