// QA (area "limits"): account limits end to end, through the web UI.
//   Same rig as qa_console.mjs.  cd web && E2E_UI=http://localhost:5291 node e2e/qa_limits.mjs
// Covers: the owner sets a client's "Imports a day" limit in Setup > Accounts; the client's admin hits it and gets the clear
// message; the client's Setup > Usage & limits card shows the usage; the console shows the 100% alert in its Alerts strip
// and the usage in the account detail; the owner raising the limit lets the client import again.
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
const csv = (n, tag) => "company,contact_name,email\n" + Array.from({ length: n }, (_, i) => `${tag}${i} Co,P${i},p${i}@${tag.toLowerCase()}${i}.example`).join("\n");
const MESSAGE = "Your account reached its daily imports limit of 2; ask the platform admin to raise it.";

const sup = await session();
const client = await session();
await step("Set-up: owner makes account Limit Ltd with a workspace and an admin", async () => {
  await signIn(sup.page, "", ADMIN);
  const acct = await api(sup.page, "/api/accounts", "POST", { name: "Limit Ltd" });
  const ws = await api(sup.page, "/api/workspaces", "POST", { name: "Limit Sales", account_id: acct.body.id });
  const u = await api(sup.page, "/api/users", "POST", { email: "admin@limit.example", name: "Lia Limit", role: "admin", password: "limit-long-password-1",
    workspaces: [ws.body.id], account_id: acct.body.id });
  if (u.status !== 200) throw new Error(JSON.stringify(u.body));
  await signIn(client.page, "admin@limit.example", "limit-long-password-1");
});
await step("The owner sets Imports a day = 2 in Setup > Accounts > Manage", async () => {
  const p = sup.page;
  await nav(p, "Setup");
  await p.getByTestId("setup-nav-accounts").click();
  await p.getByRole("button", { name: "Manage Limit Ltd" }).click();
  const e = p.getByTestId("account-edit-dialog");
  await e.getByLabel("Imports a day").fill("2");
  await e.getByTestId("dialog-submit").first().click();
  await p.getByText("Account saved.").waitFor();
  await e.getByRole("button", { name: "Close" }).first().click();
});
await step("The client admin imports two leads, then the third import is refused with the clear message", async () => {
  const p = client.page;
  const ok = await api(p, "/api/leads/import", "POST", { csv: csv(2, "Ok") });
  if (ok.status !== 200) throw new Error(JSON.stringify(ok));
  const no = await api(p, "/api/leads/import", "POST", { csv: csv(1, "No") });
  if (no.status !== 429 || no.body.detail !== MESSAGE) throw new Error(JSON.stringify(no));
  const smart = await api(p, "/api/import/commit", "POST", { csv: csv(1, "Smart"), source: "upload" });
  if (smart.status !== 429 || smart.body.detail !== MESSAGE) throw new Error(JSON.stringify(smart));
});
await step("Setup > Usage & limits shows the usage against the limit for the client's admin", async () => {
  const p = client.page;
  await p.reload();
  await p.locator("main").waitFor();
  await nav(p, "Setup");
  await p.getByTestId("setup-nav-usage").click();
  const row = p.getByTestId("usage-imports_per_day");
  await row.waitFor();
  const text = await row.innerText();
  if (!/2\s+2/.test(text.replace(/\n|\t/g, " ")) || !/Limit reached/.test(text)) throw new Error(text);
  await p.getByTestId("account-whatsapp").waitFor();
  await p.screenshot({ path: `${SHOTS}/limits-usage.png` });
});
await step("The client admin's bell shows the 100% alert", async () => {
  const r = await api(client.page, "/api/notifications");
  if (!JSON.stringify(r.body).includes("reached its daily imports limit")) throw new Error(JSON.stringify(r.body).slice(0, 300));
});
await step("The console's Alerts strip lists it, and the account detail shows the usage", async () => {
  const p = sup.page;
  await p.reload();
  await p.locator("main").waitFor();
  await nav(p, "Setup");
  await p.getByTestId("setup-nav-accounts").click();
  const strip = p.getByTestId("console-alerts");
  await strip.waitFor();
  if (!/Limit Ltd reached its daily imports limit/.test(await strip.innerText())) throw new Error(await strip.innerText());
  await p.getByRole("button", { name: "Details Limit Ltd" }).first().click();
  const d = p.getByTestId("account-detail");
  const t = (await d.getByTestId("usage-imports_per_day").innerText()).replace(/\s+/g, " ");
  if (!/2 2/.test(t)) throw new Error(t);
  await p.screenshot({ path: `${SHOTS}/limits-console.png` });
  await d.getByRole("button", { name: "Close" }).first().click().catch(() => {});
});
await step("Raising the limit lets the client import again", async () => {
  const accts = await api(sup.page, "/api/accounts");
  const id = accts.body.accounts.find((a) => a.name === "Limit Ltd").id;
  const up = await api(sup.page, `/api/accounts/${id}`, "PUT", { limits: { imports_per_day: 10 } });
  if (up.status !== 200) throw new Error(JSON.stringify(up));
  const again = await api(client.page, "/api/leads/import", "POST", { csv: csv(1, "Again") });
  if (again.status !== 200) throw new Error(JSON.stringify(again));
});

await browser.close();
for (const [ok, name] of results) console.log(`${ok ? "PASS" : "FAIL"} ${name}`);
console.log(`super errors: ${JSON.stringify(sup.errors)}\nclient errors: ${JSON.stringify(client.errors)}`);
process.exit(results.every(([ok]) => ok) ? 0 : 1);
