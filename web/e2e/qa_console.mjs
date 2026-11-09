// QA (area "console"): the super-admin console in Setup > Accounts, through the web UI.
//   Same rig as qa_signup.mjs.  cd web && E2E_UI=http://localhost:5291 node e2e/qa_console.mjs
// Covers: suspend signs the client out at once and reactivate lets it back in; editing limits; "View as" shows the big
// read-only banner, refuses writes, and Stop returns to the super admin; "Stop all sending" shows a banner to everyone.
import { chromium } from "playwright";

const UI = process.env.E2E_UI || "http://localhost:5291";
const ADMIN = process.env.E2E_PASSWORD || "qa-admin-pass-1";
const SHOTS = process.env.SHOT_DIR || "/tmp";
const results = [];
const browser = await chromium.launch();

async function session() {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(`pageerror ${e.message}`));
  return { page, errors };
}
async function step(name, fn) {
  try { await fn(); results.push([true, name]); } catch (e) { results.push([false, `${name} — ${e.message.split("\n")[0]}`]); }
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
const api = (p, path, method = "GET", body) => p.evaluate(async ([path, method, body]) => {
  const res = await fetch(path, { method, headers: { "Content-Type": "application/json", Authorization: `Bearer ${sessionStorage.getItem("outreach-token")}` },
    body: body ? JSON.stringify(body) : undefined });
  return { status: res.status, body: await res.json().catch(() => ({})) };
}, [path, method, body]);
async function accountsPage(p) {
  await nav(p, "Setup");
  await p.getByTestId("setup-nav-accounts").click();
  await p.getByTestId("stop-all").waitFor();
}
const details = async (p, name) => { await p.getByRole("button", { name: `Details ${name}` }).first().click(); return p.getByTestId("account-detail"); };

const sup = await session();
const client = await session();
await step("Set-up: super admin makes account Echo Ltd with a workspace and an admin; the admin imports a lead", async () => {
  await signIn(sup.page, "", ADMIN);
  const acct = await api(sup.page, "/api/accounts", "POST", { name: "Echo Ltd" });
  const ws = await api(sup.page, "/api/workspaces", "POST", { name: "Echo Sales", account_id: acct.body.id });
  const u = await api(sup.page, "/api/users", "POST", { email: "admin@echo.example", name: "Eve Echo", role: "admin", password: "echo-long-password-1",
    workspaces: [ws.body.id], account_id: acct.body.id });
  if (u.status !== 200) throw new Error(JSON.stringify(u.body));
  await signIn(client.page, "admin@echo.example", "echo-long-password-1");
  const imp = await api(client.page, "/api/leads/import", "POST", { csv: "company,contact_name,email\nEcho Lead Co,Ela,ela@echolead.example" });
  if (imp.status !== 200) throw new Error(JSON.stringify(imp.body));
});
await step("Suspend from the console signs the client out at once; Reactivate lets them sign in again", async () => {
  const p = sup.page;
  await accountsPage(p);
  const d = await details(p, "Echo Ltd");
  await d.getByTestId("detail-suspend").click();
  const t = p.getByTestId("console-text-dialog");
  await t.locator("textarea").fill("QA suspension");
  await t.getByTestId("console-text-submit").click();
  await p.getByText("“Echo Ltd” suspended").waitFor();
  await client.page.reload();
  await client.page.getByLabel("Password").waitFor({ timeout: 10000 });          // back on the sign-in screen
  const d2 = await details(p, "Echo Ltd");
  await d2.getByTestId("detail-reactivate").click();
  await p.getByText("“Echo Ltd” reactivated.").waitFor();
  await signIn(client.page, "admin@echo.example", "echo-long-password-1");
});
await step("Edit limits from the console (Logins 5, Emails a day 40)", async () => {
  const p = sup.page;
  await p.getByRole("button", { name: "Manage Echo Ltd" }).click();
  const e = p.getByTestId("account-edit-dialog");
  await e.getByLabel("Logins").fill("5");
  await e.getByLabel("Emails a day").fill("40");
  await e.getByTestId("dialog-submit").click();
  await p.getByText("Account saved.").waitFor();
  await e.getByRole("button", { name: "Close" }).first().click();
  const row = await p.getByTestId("account-Echo Ltd").innerText();
  if (!/seats 5/.test(row) || !/daily sends 40/.test(row)) throw new Error(row);
});
await step("View as: big read-only banner, the client's data, writes refused, Stop returns to the super admin", async () => {
  const p = sup.page;
  const d = await details(p, "Echo Ltd");
  await d.getByRole("button", { name: "View as admin@echo.example" }).click();
  const banner = p.getByTestId("view-as-banner");
  await banner.waitFor({ timeout: 15000 });
  if (!/Viewing as Eve Echo \(read-only\)/.test(await banner.innerText())) throw new Error(await banner.innerText());
  await nav(p, "Leads");
  await p.getByText("Echo Lead Co").first().waitFor();
  const w = await api(p, "/api/leads/import", "POST", { csv: "company,email\nNope,nope@nope.example" });
  if (w.status !== 403 || !/Read-only/.test(w.body.detail)) throw new Error(JSON.stringify(w));
  await p.screenshot({ path: `${SHOTS}/console-view-as.png` });
  await p.getByTestId("view-as-stop").click();
  await p.getByTestId("stop-all").waitFor({ timeout: 15000 });
  if (await p.getByTestId("view-as-banner").count()) throw new Error("banner still shown");
  const log = await api(client.page, "/api/account/audit");
  if (!JSON.stringify(log.body).includes("view_as.request")) throw new Error("the client's admin does not see the view-as in its log");
});
await step("Stop all sending: banner for the super admin and the client; resume clears it", async () => {
  const p = sup.page;
  p.once("dialog", (dlg) => dlg.accept());
  await p.getByLabel("Reason for stopping").fill("QA drill");
  await p.getByTestId("stop-all-on").click();
  await p.getByTestId("stop-all-banner").waitFor();
  await client.page.reload();
  await client.page.getByTestId("stop-all-banner").waitFor({ timeout: 15000 });
  if (!/QA drill/.test(await client.page.getByTestId("stop-all-banner").innerText())) throw new Error("no reason in the banner");
  await client.page.screenshot({ path: `${SHOTS}/console-stop-all.png` });
  await p.getByTestId("stop-all-off").click();
  await p.getByTestId("stop-all-on").waitFor();
  await client.page.reload();
  await client.page.locator("main").waitFor();
  await client.page.waitForTimeout(1500);
  if (await client.page.getByTestId("stop-all-banner").count()) throw new Error("banner still shown after resume");
});

await browser.close();
for (const [ok, name] of results) console.log(`${ok ? "PASS" : "FAIL"} ${name}`);
console.log(`super errors: ${JSON.stringify(sup.errors)}\nclient errors: ${JSON.stringify(client.errors)}`);
process.exit(results.every(([ok]) => ok) ? 0 : 1);
