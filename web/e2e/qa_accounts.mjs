// QA (area "accounts"): a second client company (account) set up and used only through the web UI.
//   Same rig as qa_admin.mjs / qa_isolation.mjs. cd web && E2E_UI=http://localhost:5284 node e2e/qa_accounts.mjs
// Covers: the owner creates account "Bravo Corp", its workspace and its admin from Setup > Accounts; the owner gets an
// Account picker; Bravo's admin sees the account name, none of Mas Callnet's leads on any page and no refused calls;
// the same contact is imported in main and in Bravo (two copies, one per account); Bravo's sending is held without its
// own sending identity or a grant; Setup > Workspaces shows the account column to the owner.
import { chromium } from "playwright";

const UI = process.env.E2E_UI || "http://localhost:5284";
const ADMIN = process.env.E2E_PASSWORD || "qa-admin-pass-1";
const SHOTS = process.env.SHOT_DIR || "/tmp";
const MAIN_CSV = ["company,contact_name,title,email,phone,website",
  "Sharma Logistics Pvt Ltd,Rohit Sharma,Head of HR,rohit.sharma@sharmalogistics.example,+91 98110 23456,https://sharmalogistics.example",
  "Kaveri Textiles,Lakshmi Iyer,HR Manager,lakshmi@kaveritextiles.example,9845012345,kaveritextiles.example"].join("\n");
const SHARED_CSV = ["company,contact_name,title,email,phone,website",
  "Kaveri Textiles,Lakshmi Iyer,HR Manager,lakshmi@kaveritextiles.example,9845012345,kaveritextiles.example",
  "Nandi Foods,Arun Gowda,HR Lead,arun@nandifoods.example,9900112233,https://nandifoods.example"].join("\n");
const SECRET_WORDS = ["Sharma", "Rohit"];          // only in Mas Callnet's main workspace
const results = [];
const browser = await chromium.launch();

async function session() {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on("response", (r) => { if (r.url().includes("/api/") && r.status() >= 400) errors.push(`${r.status()} ${r.url().replace(UI, "")}`); });
  page.on("pageerror", (e) => errors.push(`pageerror ${e.message}`));
  return { page, errors };
}
async function signIn(p, email, password) {
  await p.goto(UI);
  await p.getByLabel("Password").waitFor();
  if (email) await p.getByLabel("Work email").fill(email);
  await p.getByLabel("Password").fill(password);
  await p.getByRole("button", { name: "Sign in" }).click();
  await p.locator("main").waitFor();
  const later = p.getByRole("button", { name: "Skip setup" });
  await later.waitFor({ timeout: 6000 }).then(async () => { await later.click(); await later.waitFor({ state: "detached", timeout: 20000 }); }, () => {});
}
const nav = async (p, label) => { await p.locator("nav.side").getByRole("button", { name: label, exact: true }).click(); await p.waitForTimeout(900); };
async function step(name, fn) {
  try { await fn(); results.push([true, name]); } catch (e) { results.push([false, `${name} — ${e.message.split("\n")[0]}`]); }
}
const api = (p, path, method = "GET", body) => p.evaluate(async ([path, method, body]) => {
  const res = await fetch(path, { method, headers: { "Content-Type": "application/json", Authorization: `Bearer ${sessionStorage.getItem("outreach-token")}` },
    body: body ? JSON.stringify(body) : undefined });
  return { status: res.status, body: await res.json().catch(() => ({})) };
}, [path, method, body]);

const owner = await session();
await step("Owner imports two leads into main", async () => {
  const p = owner.page;
  await signIn(p, "", ADMIN);
  await nav(p, "Leads");
  await p.getByRole("button", { name: "Quick paste" }).click();
  await p.locator("textarea").first().fill(MAIN_CSV);
  await p.getByRole("button", { name: "Import", exact: true }).click();
  await p.getByText("Added 2").waitFor();
});
await step("Owner creates account Bravo Corp, its workspace and its admin in Setup > Accounts", async () => {
  const p = owner.page;
  await nav(p, "Setup");
  await p.getByTestId("setup-nav-accounts").click();
  await p.getByTestId("create-account").click();
  const d = p.getByTestId("account-new-dialog");
  await d.getByLabel("Company name").fill("Bravo Corp");
  await d.getByTestId("dialog-submit").click();
  await p.getByText("Account “Bravo Corp” created.").waitFor();
  await p.getByRole("button", { name: "Manage Bravo Corp" }).click();
  let e = p.getByTestId("account-edit-dialog");
  await e.getByLabel("New workspace name").fill("Bravo Sales");
  await e.getByRole("button", { name: "Add workspace" }).click();
  await p.getByText("Workspace “Bravo Sales” added.").waitFor();
  e = p.getByTestId("account-edit-dialog");
  await e.getByLabel("Login email").fill("admin@bravo.example");
  await e.getByLabel("Login name").fill("Bea Bravo");
  await e.getByLabel("Login password").fill("long-password-1");
  await e.getByRole("button", { name: "Add login" }).click();
  await p.getByText("Login added.").waitFor();
  await e.getByRole("button", { name: "Close" }).first().click();
  const row = await p.getByTestId("account-Bravo Corp").innerText();
  if (!/Held/.test(row)) throw new Error(`Bravo's sending should show Held: ${row}`);
});
await step("Owner has an Account picker (Mas Callnet by default, Bravo Corp listed)", async () => {
  const p = owner.page;
  await p.reload(); await p.locator("main").waitFor(); await p.waitForTimeout(1200);
  const picker = p.getByTestId("account-picker");
  await picker.waitFor();
  const opts = await picker.locator("option").allInnerTexts();
  if (!opts.some((o) => o.includes("Bravo Corp")) || (await picker.inputValue()) !== "mcn") throw new Error(`picker ${JSON.stringify(opts)}`);
});

const bravo = await session();
let menu = [];
await step("Bravo's admin sees the account name and none of Mas Callnet's leads", async () => {
  const p = bravo.page;
  await signIn(p, "admin@bravo.example", "long-password-1");
  await p.getByTestId("account-name").waitFor();
  if ((await p.getByTestId("account-name").textContent()).trim() !== "Bravo Corp") throw new Error("account name not shown");
  menu = (await p.locator("nav.side").getByRole("button").allInnerTexts()).map((t) => t.trim()).filter(Boolean);
  await nav(p, "Leads");
  const text = await p.locator("main").innerText();
  for (const w of [...SECRET_WORDS, "Kaveri"]) if (text.includes(w)) throw new Error(`Leads shows ${w}`);
});
await step("The same contact is imported in Bravo too (no duplicate: another company's copy)", async () => {
  const p = bravo.page;
  await p.getByRole("button", { name: "Quick paste" }).click();
  await p.locator("textarea").first().fill(SHARED_CSV);
  await p.getByRole("button", { name: "Import", exact: true }).click();
  await p.getByText("Added 2").waitFor();
  if (!/skipped 0 duplicates/.test(await p.locator("main").innerText())) throw new Error("reported a duplicate");
  await p.getByRole("button", { name: "Kaveri Textiles" }).first().waitFor();
  const all = await api(owner.page, "/api/leads");
  const kaveri = all.body.filter((l) => l.company === "Kaveri Textiles");
  if (kaveri.length !== 2) throw new Error(`owner sees ${kaveri.length} Kaveri leads`);
});
await step("Bravo's sending is held without its own sending identity or a grant", async () => {
  const p = bravo.page;
  const leads = (await api(p, "/api/leads")).body;
  const lead = leads.find((l) => l.company === "Nandi Foods");
  const enrol = await api(p, `/api/leads/${lead.id}/enroll`, "POST", {});
  if (enrol.status !== 200) throw new Error(`enrol ${enrol.status} ${JSON.stringify(enrol.body)}`);
  const now = await api(p, `/api/leads/${lead.id}/email-now`, "POST", {});
  if (now.status !== 400 || !/sending domain/.test(now.body.detail || "")) throw new Error(`email-now ${now.status} ${JSON.stringify(now.body)}`);
  const live = await api(p, "/api/settings/live-sending");
  if (!JSON.stringify(live.body.blockers).includes("connect your sending domain")) throw new Error(`live ${JSON.stringify(live.body)}`);
});
await step("Every page in Bravo's menu: no refused calls and no Mas Callnet data", async () => {
  const p = bravo.page;
  const pages = menu.filter((t) => !/^(Search|More|Sign out|Log out)/i.test(t) && t.length < 40);
  const leaks = [];
  for (const label of pages) {
    const btn = p.locator("nav.side").getByRole("button", { name: label, exact: true });
    if (!(await btn.count())) continue;
    await btn.first().click();
    await p.waitForTimeout(1500);
    const text = await p.locator("main").innerText();
    for (const w of [...SECRET_WORDS, "Mas Callnet"]) if (text.includes(w)) leaks.push(`${label}: ${w}`);
  }
  const refused = bravo.errors.filter((e) => e.startsWith("403"));
  if (leaks.length || refused.length) throw new Error(`leaks ${JSON.stringify([...new Set(leaks)])} refused ${JSON.stringify([...new Set(refused)])}`);
  await p.screenshot({ path: `${SHOTS}/client-account-menu.png` });
});
await step("Owner sees the account column in Setup > Workspaces", async () => {
  const p = owner.page;
  await nav(p, "Setup");
  await p.getByTestId("setup-nav-workspaces").click();
  await p.getByTestId("ws-Bravo Sales").waitFor();
  const row = await p.getByTestId("ws-Bravo Sales").innerText();
  if (!row.includes("Bravo Corp")) throw new Error(`row ${row}`);
});

await browser.close();
for (const [ok, name] of results) console.log(`${ok ? "PASS" : "FAIL"} ${name}`);
const other = bravo.errors.filter((e) => !e.startsWith("403") && !e.includes("/email-now"));
console.log(`owner errors: ${JSON.stringify(owner.errors)}\nclient errors: ${JSON.stringify(other)}`);
process.exit(results.every(([ok]) => ok) ? 0 : 1);
