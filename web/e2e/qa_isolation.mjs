// QA (area "workspaces"): a login limited to one client workspace, done only through the web UI.
//   Same rig as qa_admin.mjs. cd web && E2E_UI=http://localhost:5284 node e2e/qa_isolation.mjs
// Covers: owner adds a workspace and a login limited to it from the Add-login form; that login sees only its pages,
// no other client's leads or replies anywhere, no refused (403) calls on any page it can open, quick paste skips a
// contact held in another workspace, and a hidden page opened by link says it is not available.
import { chromium } from "playwright";

const UI = process.env.E2E_UI || "http://localhost:5284";
const ADMIN = process.env.E2E_PASSWORD || "qa-admin-pass-1";
const SHOTS = process.env.SHOT_DIR || "/tmp";
const MAIN_CSV = ["company,contact_name,title,email,phone,website",
  "Sharma Logistics Pvt Ltd,Rohit Sharma,Head of HR,rohit.sharma@sharmalogistics.example,+91 98110 23456,https://sharmalogistics.example",
  "Kaveri Textiles,Lakshmi Iyer,HR Manager,lakshmi@kaveritextiles.example,9845012345,kaveritextiles.example"].join("\n");
const CLIENT_CSV = ["company,contact_name,title,email,phone",
  "Kaveri Textiles,Lakshmi Iyer,HR Manager,lakshmi@kaveritextiles.example,9845012345",
  "Nandi Foods,Arun Gowda,HR Lead,arun@nandifoods.example,9900112233"].join("\n");
const SECRET_WORDS = ["Sharma", "Rohit", "Kaveri", "Lakshmi"];
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

const owner = await session();
await step("Owner imports leads, logs a reply, adds a workspace and a login limited to it", async () => {
  const p = owner.page;
  await signIn(p, "", ADMIN);
  await nav(p, "Leads");
  await p.getByRole("button", { name: "Quick paste" }).click();
  await p.locator("textarea").first().fill(MAIN_CSV);
  await p.getByRole("button", { name: "Import", exact: true }).click();
  await p.getByText("Added 2").waitFor();
  await nav(p, "Setup");
  await p.getByRole("tab", { name: "Team & workspaces" }).click();
  await p.getByLabel("New workspace name").fill("Client Foods");
  await p.getByRole("button", { name: "Add workspace" }).click();
  await p.getByText("Workspace “Client Foods” added.").waitFor();
  await p.reload(); await p.locator("main").waitFor(); await p.waitForTimeout(1200);
  await nav(p, "Setup");
  await p.getByRole("tab", { name: "Team & workspaces" }).click();
  const card = p.getByTestId("users-card");
  const form = card.locator("form.inline-form");
  await form.getByLabel("Email").fill("raj@client.example");
  await form.getByLabel("Name").fill("Raj Client");
  await form.getByLabel(/Password/).fill("long-password-1");
  await p.getByTestId("new-user-workspaces").getByLabel("Client Foods").check();
  await form.getByRole("button", { name: "Add login" }).click();
  await card.getByText("Login added for raj@client.example.").waitFor();
  const row = card.locator("li", { hasText: "raj@client.example" });
  if (!(await row.locator("label.check-row", { hasText: "Client Foods" }).locator("input").isChecked())) throw new Error("workspace not ticked on the new login");
});
await step("Owner logs a reply from Sharma (so Replies has another client's data)", async () => {
  const p = owner.page;
  await nav(p, "Leads");
  await p.getByRole("button", { name: "Sharma Logistics Pvt Ltd" }).first().click();
  const d = p.getByRole("dialog").first();
  await d.getByRole("tab", { name: /Activity/ }).click();
  const paste = d.getByRole("button", { name: /Paste a reply|Log a reply/ });
  if (await paste.count()) {
    await paste.first().click();
    await d.locator("textarea").last().fill("Yes Rohit here, interested, call me");
    await d.getByRole("button", { name: /Save reply|Log reply|Save/ }).last().click();
    await p.waitForTimeout(1200);
  }
  await p.keyboard.press("Escape");
});

const raj = await session();
let menu = [];
await step("Limited login sees only its pages and an empty Leads list", async () => {
  const p = raj.page;
  await signIn(p, "raj@client.example", "long-password-1");
  menu = (await p.locator("nav.side").getByRole("button").allInnerTexts()).map((t) => t.trim()).filter(Boolean);
  for (const hidden of ["Campaigns", "Prospect search", "LinkedIn outreach", "Data hygiene", "Buying signals", "Flow builder"])
    if (menu.includes(hidden)) throw new Error(`menu shows ${hidden}`);
  await nav(p, "Leads");
  const text = await p.locator("main").innerText();
  for (const w of SECRET_WORDS) if (text.includes(w)) throw new Error(`Leads shows ${w}`);
});
await step("Quick paste: new lead added, contact held in another workspace skipped as a duplicate (where it is stays hidden)", async () => {
  const p = raj.page;
  await p.getByRole("button", { name: "Quick paste" }).click();
  await p.locator("textarea").first().fill(CLIENT_CSV);
  await p.getByRole("button", { name: "Import", exact: true }).click();
  await p.getByText("Added 1").waitFor();
  await p.getByText(/skipped 1 duplicates/).waitFor();
  if (/another workspace/.test(await p.locator("main").innerText())) throw new Error("told that another workspace holds it");
  await p.getByRole("button", { name: "Nandi Foods" }).first().waitFor();
});
await step("Every page in the menu: no refused calls and no other client's data", async () => {
  const p = raj.page;
  const pages = menu.filter((t) => !/^(Search|More|Sign out|Log out)/i.test(t) && t.length < 40);
  const leaks = [];
  for (const label of pages) {
    const btn = p.locator("nav.side").getByRole("button", { name: label, exact: true });
    if (!(await btn.count())) continue;
    await btn.first().click();
    await p.waitForTimeout(1500);
    const text = await p.locator("main").innerText();
    for (const w of SECRET_WORDS) if (text.includes(w)) leaks.push(`${label}: ${w}`);
    for (const tab of await p.locator("main [role=tab]").all()) {
      await tab.click().catch(() => {});
      await p.waitForTimeout(700);
      const t = await p.locator("main").innerText();
      for (const w of SECRET_WORDS) if (t.includes(w)) leaks.push(`${label}/${(await tab.innerText()).trim()}: ${w}`);
    }
  }
  const refused = raj.errors.filter((e) => e.startsWith("403"));
  if (leaks.length || refused.length) throw new Error(`leaks ${JSON.stringify([...new Set(leaks)])} refused ${JSON.stringify([...new Set(refused)])}`);
  await p.screenshot({ path: `${SHOTS}/limited-menu.png` });
});
await step("A hidden page opened by link says it is not available", async () => {
  const p = raj.page;
  await p.evaluate(() => { window.location.hash = "#campaigns"; });
  await p.waitForTimeout(2500);
  const text = await p.locator("main").innerText().catch(() => "(no main)");
  if (!text.includes("This page is not available for logins limited to a workspace.")) throw new Error(text.replace(/\s+/g, " ").slice(0, 200));
});
await step("Owner still sees both clients", async () => {
  const p = owner.page;
  await nav(p, "Leads");
  await p.getByRole("button", { name: "Sharma Logistics Pvt Ltd" }).first().waitFor();
});

const admin = await session();
await step("Owner makes a workspace admin for Client Foods from the Add-login form", async () => {
  const p = owner.page;
  await nav(p, "Setup");
  await p.getByRole("tab", { name: "Team & workspaces" }).click();
  const card = p.getByTestId("users-card");
  const form = card.locator("form.inline-form");
  await form.getByLabel("Email").fill("lead@client.example");
  await form.getByLabel("Name").fill("Client Lead");
  await form.getByLabel("Role").selectOption("admin");
  await form.getByLabel(/Password/).fill("long-password-2");
  await p.getByTestId("new-user-workspaces").getByLabel("Client Foods").check();
  await form.getByRole("button", { name: "Add login" }).click();
  await card.getByText("Login added for lead@client.example.").waitFor();
});
await step("Workspace admin sees only its workspace's logins and adds a caller into it", async () => {
  const p = admin.page;
  await signIn(p, "lead@client.example", "long-password-2");
  await nav(p, "Setup");
  await p.getByRole("tab", { name: "Team & workspaces" }).click();
  const card = p.getByTestId("users-card");
  await card.getByText("raj@client.example").waitFor();
  const text = await card.innerText();
  if (/Add workspace/.test(await p.locator("main").innerText())) throw new Error("admin sees Add workspace");
  if (!/lead@client.example/.test(text) || /Super admin|qa-admin|boss@qa.test/.test(text)) throw new Error(`users card: ${text.slice(0, 200)}`);
  const roles = await card.locator("form.inline-form").getByLabel("Role").locator("option").allInnerTexts();
  if (roles.includes("owner")) throw new Error("admin may pick owner");
  const form = card.locator("form.inline-form");
  await form.getByLabel("Email").fill("agent@client.example");
  await form.getByLabel("Name").fill("Client Agent");
  await form.getByLabel(/Password/).fill("long-password-3");
  await form.getByRole("button", { name: "Add login" }).click();
  await card.getByText("Login added for agent@client.example.").waitFor();
  const refused = admin.errors.filter((e) => e.startsWith("403"));
  if (refused.length) throw new Error(`refused ${JSON.stringify(refused)}`);
});
await step("The admin's new caller sees only Client Foods (Nandi Foods), nothing from main", async () => {
  const s = await session();
  await signIn(s.page, "agent@client.example", "long-password-3");
  await nav(s.page, "Leads");
  await s.page.getByRole("button", { name: "Nandi Foods" }).first().waitFor();
  const text = await s.page.locator("main").innerText();
  for (const w of SECRET_WORDS) if (text.includes(w)) throw new Error(`agent sees ${w}`);
});

await browser.close();
for (const [ok, name] of results) console.log(`${ok ? "PASS" : "FAIL"} ${name}`);
const other = raj.errors.filter((e) => !e.startsWith("403"));
console.log(`owner errors: ${JSON.stringify(owner.errors)}\nlimited errors: ${JSON.stringify(other)}`);
process.exit(results.every(([ok]) => ok) ? 0 : 1);
