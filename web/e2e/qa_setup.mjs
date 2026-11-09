// QA (area "setup"): the Setup sub-navigation, workspace management and team members, done only through the web UI.
//   API: a fresh OUTREACH_DATA_FILE, OUTREACH_ADMIN_PASSWORD=qa-admin-pass-1, OUTREACH_EMAIL_DOMAIN_CHECK=0
//   UI:  npx vite build --outDir <temp>/dist, served by e2e/static_proxy.mjs with /api proxied to the API
//   cd web && E2E_UI=http://localhost:5284 SHOT_DIR=<rig>/shots node e2e/qa_setup.mjs
// Covers: create, rename and delete (moving the leads) a workspace; Add member needs an explicit workspace choice;
// the Manage menu; a member left with no workspace is shown as such; the sidebar paints the full page height;
// no horizontal scroll at 375 px; a screenshot of every Setup section at 1440 and 375 px.
import { chromium } from "playwright";

const UI = process.env.E2E_UI || "http://localhost:5284";
const ADMIN = process.env.E2E_PASSWORD || "qa-admin-pass-1";
const SHOTS = process.env.SHOT_DIR || "/tmp";
const MAIN_CSV = ["company,contact_name,title,email,phone,website",
  "Sharma Logistics Pvt Ltd,Rohit Sharma,Head of HR,rohit.sharma@sharmalogistics.example,+91 98110 23456,https://sharmalogistics.example"].join("\n");
const CLIENT_CSV = ["company,contact_name,title,email,phone",
  "Nandi Foods,Arun Gowda,HR Lead,arun@nandifoods.example,9900112233",
  "Cauvery Mills,Divya Rao,HR Manager,divya@cauverymills.example,9900445566"].join("\n");
const SECTIONS = ["checklist", "brand", "industry", "scoring", "workspaces", "members", "security", "audit", "connections", "backup", "health", "account"];
const results = [];
const shots = [];
const browser = await chromium.launch();

async function session(width = 1440, height = 900) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  const page = await ctx.newPage();
  const errors = [];
  page.on("response", (r) => { if (r.url().includes("/api/") && r.status() >= 500) errors.push(`${r.status()} ${r.url().replace(UI, "")}`); });
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
const nav = async (p, label) => { await p.locator("nav.side").getByRole("button", { name: label, exact: true }).click(); await p.waitForTimeout(800); };
const section = async (p, id) => { await p.evaluate((s) => { window.location.hash = `#setup/${s}`; }, id); await p.waitForTimeout(900); };
async function step(name, fn) {
  try { await fn(); results.push([true, name]); } catch (e) { results.push([false, `${name} — ${e.message.split("\n")[0]}`]); }
}
const dialog = (p, id) => p.getByTestId(id);

const owner = await session();
const p = owner.page;
await step("Owner imports a lead and opens Setup: grouped sub-navigation, one header, summary only on Connections", async () => {
  await signIn(p, "", ADMIN);
  await nav(p, "Leads");
  await p.getByRole("button", { name: "Quick paste" }).click();
  await p.locator("textarea").first().fill(MAIN_CSV);
  await p.getByRole("button", { name: "Import", exact: true }).click();
  await p.getByText("Added 1").waitFor();
  await nav(p, "Setup");
  const groups = await p.locator(".setup-nav-title").allTextContents();
  if (groups.join("|") !== "Organisation|Team & access|Integrations|Data|My account") throw new Error(`groups ${groups.join("|")}`);
  if ((await p.locator(".setup-nav a").count()) !== 13) throw new Error("expected 13 sections");     // + Accounts (owners)
  await p.getByTestId("setup-nav-members").click();
  if (!(await p.evaluate(() => window.location.hash)).startsWith("#setup/members")) throw new Error("no deep link");
  if (await p.getByTestId("setup-summary").count()) throw new Error("connected summary on Team members");
  await p.getByTestId("setup-nav-connections").click();
  await p.getByTestId("setup-summary").waitFor();
  const auto = await p.locator(".connector input:not([type=radio]):not([type=checkbox])").evaluateAll((els) => els.map((e) => [e.name, e.type, e.autocomplete]));
  const bad = auto.filter(([n, t, a]) => /email|user|pass/i.test(n) || (t === "password" && a !== "new-password") || (t === "text" && a !== "off"));
  if (bad.length) throw new Error(`fields browsers may autofill: ${JSON.stringify(bad)}`);
  await p.reload(); await p.locator("main").waitFor(); await p.waitForTimeout(900);
  if ((await p.getByTestId("setup-section-title").innerText()) !== "Connections") throw new Error("deep link not kept on reload");
});
await step("Create a workspace, then rename it", async () => {
  await section(p, "workspaces");
  await p.getByTestId("create-workspace").click();
  await dialog(p, "ws-name-dialog").getByLabel("Workspace name").fill("Client Foods");
  await dialog(p, "ws-name-dialog").getByRole("button", { name: "Create workspace" }).click();
  await p.getByText("Workspace “Client Foods” added.").waitFor();
  await p.getByRole("button", { name: "Rename Client Foods" }).click();
  const d = dialog(p, "ws-name-dialog");
  await d.getByLabel("Workspace name").fill("Client Foods India");
  await d.getByRole("button", { name: "Save name" }).click();
  await p.getByText("Renamed to “Client Foods India”.").waitFor();
  await p.getByTestId("ws-Client Foods India").waitFor();
});
await step("Import two leads into the new workspace", async () => {
  await p.reload(); await p.locator("main").waitFor(); await p.waitForTimeout(1000);
  const picker = p.locator("nav.side").getByLabel("Workspace");
  const value = await picker.locator("option", { hasText: "Client Foods India" }).getAttribute("value");
  await picker.selectOption(value);
  await nav(p, "Leads");
  await p.getByRole("button", { name: "Quick paste" }).click();
  await p.locator("textarea").first().fill(CLIENT_CSV);
  await p.getByRole("button", { name: "Import", exact: true }).click();
  await p.getByText("Added 2").waitFor();
  await picker.selectOption("");
  await p.waitForTimeout(600);
});
await step("Add member: the workspace choice is required, then the login is added", async () => {
  await nav(p, "Setup");
  await section(p, "members");
  await p.getByTestId("add-member").click();
  const d = dialog(p, "add-member-dialog");
  await d.getByLabel("Work email").fill("raj@client.example");
  await d.getByLabel("Name").fill("Raj Client");
  await d.getByLabel("Starting password").fill("long-password-1");
  if (!(await d.getByTestId("dialog-submit").isDisabled())) throw new Error("Add member enabled without a workspace choice");
  await d.getByTestId("new-user-workspaces").getByLabel("Client Foods India").check();
  await d.getByTestId("dialog-submit").click();
  await p.getByText("Login added for raj@client.example.").waitFor();
  const row = p.getByTestId("member-raj@client.example");
  if (!(await row.innerText()).includes("Client Foods India")) throw new Error("workspace chip missing");
});
await step("The Manage menu lists the row's actions, Remove separated and red", async () => {
  await p.getByTestId("manage-raj@client.example").click();
  const menu = p.getByRole("menu");
  const items = (await menu.getByRole("menuitem").allInnerTexts()).map((t) => t.trim());
  for (const want of ["Change role…", "Workspaces…", "Reset password…", "Remove login…"]) if (!items.includes(want)) throw new Error(`menu: ${items.join(", ")}`);
  if (items[items.length - 1] !== "Remove login…") throw new Error("Remove is not last");
  await p.screenshot({ path: `${SHOTS}/setup-manage-menu-1440.png` }); shots.push(`${SHOTS}/setup-manage-menu-1440.png`);
  await menu.getByRole("menuitem", { name: "Remove login…" }).click();
  await dialog(p, "remove-dialog").waitFor();
  await dialog(p, "remove-dialog").getByRole("button", { name: "Cancel" }).click();
  await p.keyboard.press("Escape");
});
await step("Delete the workspace, moving its leads to Main; the member is shown with no workspace", async () => {
  await section(p, "workspaces");
  const mainBefore = Number(await p.getByTestId("ws-Main").locator("td").nth(1).innerText());
  await p.getByRole("button", { name: "Delete Client Foods India" }).click();
  const d = dialog(p, "delete-ws-dialog");
  await d.getByText("It holds 2 leads").waitFor();
  await d.getByLabel("Move them to another workspace").check();
  await d.getByLabel("Move to").selectOption("main");
  const submit = d.getByTestId("dialog-submit");
  await d.getByLabel(/Type .* to confirm/).fill("Client Foods");
  if (!(await submit.isDisabled())) throw new Error("delete enabled with the wrong name typed");
  await d.getByLabel(/Type .* to confirm/).fill("Client Foods India");
  await submit.click();
  await p.getByText(/Workspace “Client Foods India” deleted\. 2 leads moved to Main\./).waitFor();
  const mainAfter = Number(await p.getByTestId("ws-Main").locator("td").nth(1).innerText());
  if (mainAfter !== mainBefore + 2) throw new Error(`Main leads ${mainBefore} -> ${mainAfter}`);
  await section(p, "members");
  await p.getByTestId("member-raj@client.example").getByText("No workspace").waitFor();
});
await step("Sidebar paints the full height of a long page", async () => {
  await section(p, "connections");
  await p.locator(".connector").first().click();
  await p.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await p.waitForTimeout(400);
  const m = await p.evaluate(() => {
    const layout = document.querySelector(".layout");
    const side = document.querySelector("nav.side").getBoundingClientRect();
    const bg = getComputedStyle(layout).backgroundImage;
    return { doc: document.documentElement.scrollHeight, layout: layout.getBoundingClientRect().height, bg, sideBottom: side.bottom, vh: window.innerHeight };
  });
  if (m.doc <= m.vh) throw new Error("page is not long enough to test");
  if (m.layout + 1 < m.doc || !m.bg.includes("gradient") || m.sideBottom + 1 < m.vh) throw new Error(JSON.stringify(m));
  await p.screenshot({ path: `${SHOTS}/setup-sidebar-fullpage-1440.png`, fullPage: true }); shots.push(`${SHOTS}/setup-sidebar-fullpage-1440.png`);
});
await step("Screenshots of every Setup section at 1440 px", async () => {
  for (const id of SECTIONS) {
    await section(p, id);
    await p.evaluate(() => window.scrollTo(0, 0));
    const file = `${SHOTS}/setup-${id}-1440.png`;
    await p.screenshot({ path: file, fullPage: true });
    shots.push(file);
  }
});

const phone = await session(375, 812);
await step("At 375 px: the sub-navigation is a select, no horizontal scroll in any section, screenshots", async () => {
  const q = phone.page;
  await signIn(q, "", ADMIN);
  await q.evaluate(() => { window.location.hash = "#setup/members"; });
  await q.waitForTimeout(1200);
  await q.locator("#setup-section").waitFor();
  if (await q.locator(".setup-nav").isVisible()) throw new Error("desktop sub-navigation visible on a phone");
  const wide = [];
  for (const id of SECTIONS) {
    await q.locator("#setup-section").selectOption(id);
    await q.waitForTimeout(900);
    const w = await q.evaluate(() => document.documentElement.scrollWidth);
    if (w > 375) wide.push(`${id}: ${w}px`);
    const file = `${SHOTS}/setup-${id}-375.png`;
    await q.screenshot({ path: file, fullPage: true });
    shots.push(file);
  }
  await q.locator("#setup-section").selectOption("members");
  await q.waitForTimeout(700);
  await q.getByTestId("manage-raj@client.example").click();
  await q.screenshot({ path: `${SHOTS}/setup-manage-menu-375.png` }); shots.push(`${SHOTS}/setup-manage-menu-375.png`);
  if (wide.length) throw new Error(`horizontal scroll: ${wide.join(", ")}`);
});

await browser.close();
for (const [ok, name] of results) console.log(`${ok ? "PASS" : "FAIL"} ${name}`);
console.log(`errors: ${JSON.stringify([...owner.errors, ...phone.errors])}`);
console.log(`screenshots:\n${shots.join("\n")}`);
process.exit(results.every(([ok]) => ok) && !owner.errors.length && !phone.errors.length ? 0 : 1);
