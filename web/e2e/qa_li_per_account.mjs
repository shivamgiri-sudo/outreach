// QA (area "LinkedIn agent per account"), through the web UI. Same rig as qa_accounts.mjs.
//   cd web && E2E_UI=http://localhost:5284 node e2e/qa_li_per_account.mjs
// Covers: the owner creates a client account with LinkedIn ceilings, its workspace and admin in Setup > Accounts; the
// account's admin opens the LinkedIn page and sees the agent box for its own account only (none of Mas Callnet's LinkedIn
// accounts), the ceilings ("max N set by the platform admin"), cannot save above them, and adds its own cloud LinkedIn
// account, which the owner sees under that account and not under Mas Callnet; the owner's stop switch reaches the admin.
import { chromium } from "playwright";

const UI = process.env.E2E_UI || "http://localhost:5284";
const ADMIN = process.env.E2E_PASSWORD || "qa-admin-pass-1";
const SHOTS = process.env.SHOT_DIR || "/tmp";
const TAG = Date.now().toString(36).slice(-5);
const NAME = `Delta Li ${TAG}`;
const EMAIL = `admin-${TAG}@delta.example`;
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
const toLinkedIn = async (p) => { await p.evaluate(() => { location.hash = "#linkedin?tab=agent"; }); await p.waitForTimeout(1800); };
async function step(name, fn) {
  try { await fn(); results.push([true, name]); } catch (e) { results.push([false, `${name} — ${e.message.split("\n")[0]}`]); }
}
const api = (p, path, method = "GET", body, headers = {}) => p.evaluate(async ([path, method, body, headers]) => {
  const auth = headers.Authorization || `Bearer ${sessionStorage.getItem("outreach-token")}`;
  const res = await fetch(path, { method, headers: { "Content-Type": "application/json", ...headers, Authorization: auth },
    body: body ? JSON.stringify(body) : undefined });
  return { status: res.status, body: await res.json().catch(() => ({})) };
}, [path, method, body, headers]);
// An agent program reporting which LinkedIn account it is logged in to (what tools/linkedin_agent.mjs does).
async function agentSays(p, slug, name) {
  const tok = await api(p, "/api/extension/pair", "POST", { label: `qa ${slug}` });
  const r = await api(p, "/api/extension/agent", "GET", null, { Authorization: `Bearer ${tok.body.token}`, "X-LinkedIn-Account": slug, "X-LinkedIn-Name": name });
  if (r.status !== 200) throw new Error(`agent ${slug}: ${r.status} ${JSON.stringify(r.body)}`);
}

const owner = await session();
await step("Owner creates the client account with LinkedIn ceilings, its workspace and its admin", async () => {
  const p = owner.page;
  await signIn(p, "", ADMIN);
  await agentSays(p, `mcn-li-${TAG}`, "Mas Callnet Sales");
  await nav(p, "Setup");
  await p.getByTestId("setup-nav-accounts").click();
  await p.getByTestId("create-account").click();
  const d = p.getByTestId("account-new-dialog");
  await d.getByLabel("Company name").fill(NAME);
  await d.getByTestId("dialog-submit").click();
  await p.getByText(`Account “${NAME}” created.`).waitFor();
  await p.getByRole("button", { name: `Manage ${NAME}` }).click();
  let e = p.getByTestId("account-edit-dialog");
  await e.getByLabel("LinkedIn requests a day (max)").fill("5");
  await e.getByLabel("LinkedIn requests a week (max)").fill("30");
  await e.getByLabel("LinkedIn messages a day (max)").fill("10");
  await e.getByRole("button", { name: "Save account" }).click();
  await p.getByText("Account saved.").waitFor();
  const acc = (await api(p, "/api/accounts")).body.accounts.find((a) => a.name === NAME);
  if (acc?.limits?.linkedin_daily_connections_max !== 5) throw new Error(`limits not saved: ${JSON.stringify(acc?.limits)}`);
  if (!(await p.getByTestId("account-edit-dialog").count())) await p.getByRole("button", { name: `Manage ${NAME}` }).click();
  e = p.getByTestId("account-edit-dialog");
  await e.getByLabel("New workspace name").fill(`Delta Sales ${TAG}`);
  await e.getByRole("button", { name: "Add workspace" }).click();
  await p.getByText(`Workspace “Delta Sales ${TAG}” added.`).waitFor();
  e = p.getByTestId("account-edit-dialog");
  await e.getByLabel("Login email").fill(EMAIL);
  await e.getByLabel("Login name").fill("Dee Delta");
  await e.getByLabel("Login password").fill("long-password-1");
  await e.getByRole("button", { name: "Add login" }).click();
  await p.getByText("Login added.").waitFor();
  await e.getByRole("button", { name: "Close" }).first().click();
});

const admin = await session();
await step("The account's admin sees its own agent box, with none of Mas Callnet's LinkedIn accounts", async () => {
  const p = admin.page;
  await signIn(p, EMAIL, "long-password-1");
  await agentSays(p, `delta-li-${TAG}`, "Delta Sales LI");
  await toLinkedIn(p);
  const box = p.getByTestId("linkedin-agent");
  await box.waitFor();
  if (!(await p.getByTestId("li-agent-account").innerText()).includes(NAME)) throw new Error("account name missing in the agent box");
  const rows = await p.getByTestId("li-agent-accounts").innerText();
  if (!rows.includes(`delta-li-${TAG}`) || rows.includes(`mcn-li-${TAG}`) || rows.includes("Mas Callnet")) throw new Error(rows.slice(0, 300));
  await p.getByTestId("linkedin-cloud").waitFor();
  if (await p.getByTestId("cloud-where").count()) throw new Error("an admin must not choose where LinkedIn runs");
});
await step("Ceilings are shown and the admin cannot save above them", async () => {
  const p = admin.page;
  await p.getByTestId("li-agent-limits").locator("summary").click();
  const notes = await p.getByTestId("li-ceiling").allInnerTexts();
  if (!notes.some((t) => t.includes("max 5 set by the platform admin"))) throw new Error(JSON.stringify(notes));
  await p.getByTestId("li-daily-connections").fill("30");
  await p.getByTestId("li-weekly-connections").fill("90");
  await p.getByTestId("li-save-limits").click();
  await p.getByText(/lowered to 5 \(the platform maximum\)/).waitFor();
  if ((await p.getByTestId("li-daily-connections").inputValue()) !== "5") throw new Error("daily value not clamped");
  if ((await p.getByTestId("li-weekly-connections").inputValue()) !== "30") throw new Error("weekly value not clamped");
  await p.screenshot({ path: `${SHOTS}/li-per-account-admin.png`, fullPage: true });
});
await step("The admin switches its own agent on (Mas Callnet's stays off) and adds a cloud LinkedIn account", async () => {
  const p = admin.page;
  await p.getByTestId("linkedin-agent").getByRole("button", { name: "Switch on" }).click();
  await p.getByTestId("li-agent-state").filter({ hasNotText: "Switched off" }).waitFor();
  await p.getByLabel("Name for the LinkedIn account").fill(`Delta LI ${TAG}`);
  await p.getByTestId("cloud-add").click();
  await p.getByTestId("cloud-slot").filter({ hasText: `Delta LI ${TAG}` }).waitFor();
  const mcn = (await api(owner.page, "/api/linkedin-agent?account=mcn")).body;
  if (mcn.config.enabled) throw new Error("Mas Callnet's agent was switched on");
});
await step("The owner sees the slot under that account only; the overview labels it", async () => {
  const p = owner.page;
  const accs = (await api(p, "/api/accounts")).body.accounts;
  const id = accs.find((a) => a.name === NAME).id;
  const mine = (await api(p, `/api/linkedin-cloud?account=${id}`)).body.slots.map((s) => s.label);
  const mcn = (await api(p, "/api/linkedin-cloud?account=mcn")).body.slots.map((s) => s.label);
  if (!mine.includes(`Delta LI ${TAG}`) || mcn.includes(`Delta LI ${TAG}`)) throw new Error(`${mine} / ${mcn}`);
  await toLinkedIn(p);
  await p.getByTestId("li-account-filter").selectOption("all");
  await p.waitForTimeout(1500);
  const slot = p.getByTestId("cloud-slot").filter({ hasText: `Delta LI ${TAG}` });
  await slot.waitFor();
  if (!(await slot.innerText()).includes(NAME)) throw new Error("overview slot without its account name");
  await p.getByTestId("li-account-filter").selectOption("");
});
await step("The owner's stop switch reaches the admin's agent", async () => {
  const p = owner.page;
  await p.getByTestId("li-kill").click();
  await p.getByRole("button", { name: /^(OK|Yes|Confirm|Stop)/ }).last().click().catch(() => {});
  await p.getByTestId("li-kill-on").waitFor();
  await admin.page.reload(); await admin.page.locator("main").waitFor(); await toLinkedIn(admin.page);
  const state = await admin.page.getByTestId("li-agent-state").innerText();
  if (!/Stopped by the platform admin/.test(state)) throw new Error(state);
  await p.getByRole("button", { name: "Let agents run again" }).click();
  await p.getByTestId("li-kill").waitFor();
});
await step("No refused calls for the admin", async () => {
  const bad = admin.errors.filter((e) => !e.startsWith("401"));
  if (bad.length) throw new Error(bad.slice(0, 5).join(" | "));
});

await browser.close();
for (const [ok, name] of results) console.log(`${ok ? "PASS" : "FAIL"} ${name}`);
const failed = results.filter(([ok]) => !ok).length;
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
