// QA sweep (area "admin"): setup, roles and data safety, done only through the web UI as a person would.
//
//   API: a fresh OUTREACH_DATA_FILE, OUTREACH_ADMIN_PASSWORD=qa-admin-pass-1, OUTREACH_SUPER_ADMIN=boss@qa.test,
//        OUTREACH_BASE_URL set to the UI address
//   UI:  npx vite build --outDir <temp>/dist, served by e2e/static_proxy.mjs with /api proxied to the API
//   cd web && E2E_UI=http://localhost:5284 node e2e/qa_admin.mjs
// Regressions covered: workspace-limited logins saw another client's tasks/calls; viewers got 403s and no notice;
// non-owners had no way to change their own password; click-to-call accepted javascript:; CSV downloads were not audited.
import { chromium } from "playwright";

const UI = process.env.E2E_UI || "http://localhost:5284";
const ADMIN = process.env.E2E_PASSWORD || "qa-admin-pass-1";
const CSV = ["company,contact_name,title,email,phone,website",
  "Sharma Logistics Pvt Ltd,Rohit Sharma,Head of HR,rohit.sharma@sharmalogistics.example,+91 98110 23456,https://sharmalogistics.example",
  "Kaveri Textiles,Lakshmi Iyer,HR Manager,lakshmi@kaveritextiles.example,9845012345,kaveritextiles.example",
  "Konkan Realty Developers,Suresh Naik,Sales Head,suresh.naik@konkanrealty.example,9820098200,konkanrealty.example"].join("\n");
const results = [];
const browser = await chromium.launch();

async function session(width = 1400) {
  const ctx = await browser.newContext({ viewport: { width, height: 900 }, acceptDownloads: true });
  const page = await ctx.newPage();
  const errors = [];
  page.on("response", (r) => { if (r.url().includes("/api/") && r.status() >= 400) errors.push(`${r.status()} ${r.url().replace(UI, "")}`); });
  page.on("pageerror", (e) => errors.push(`pageerror ${e.message}`));
  return { ctx, page, errors };
}
async function signIn(page, email, password) {
  await page.goto(UI);
  await page.getByLabel("Password").waitFor();
  if (email) await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.locator("main").waitFor();
  const later = page.getByRole("button", { name: "Skip setup" });         // the first-run wizard (owners, fresh install)
  await later.waitFor({ timeout: 2500 }).then(() => later.click(), () => {});
}
const nav = async (page, label) => { await page.locator("nav.side").getByRole("button", { name: label, exact: true }).click(); await page.waitForTimeout(700); };
async function step(name, fn) {
  try { await fn(); results.push([true, name]); } catch (e) { results.push([false, `${name} — ${e.message.split("\n")[0]}`]); }
}

const owner = await session();
await step("Owner imports leads, makes a client workspace and moves two leads into it", async () => {
  await signIn(owner.page, "", ADMIN);
  const p = owner.page;
  await nav(p, "Leads");
  await p.getByRole("button", { name: "Quick paste" }).click();
  await p.locator("textarea").first().fill(CSV);
  await p.getByRole("button", { name: "Import", exact: true }).click();
  await p.getByText("Added 3").waitFor();
  await nav(p, "Setup");
  await p.getByRole("tab", { name: "Team & workspaces" }).click();
  await p.getByLabel("New workspace name").fill("Acme Staffing");
  await p.getByRole("button", { name: "Add workspace" }).click();
  await p.getByText("Workspace “Acme Staffing” added.").waitFor();
  await nav(p, "Leads");
  for (const c of ["Sharma Logistics Pvt Ltd", "Kaveri Textiles"]) await p.getByLabel(`Select ${c}`).check();
  await p.getByLabel("Move selected leads to a workspace").selectOption({ label: "Acme Staffing" });
  await p.getByRole("button", { name: "Yes, move them" }).click();
  await p.getByText("2 lead(s) moved.").waitFor();
  const opts = await p.locator("nav.side").getByLabel("Workspace").locator("option").allInnerTexts();
  if (!opts.includes("Acme Staffing (2)")) throw new Error(`menu counts not refreshed: ${opts.join(", ")}`);
});
await step("Each lead says yes (consent + call task), including the one outside the client workspace", async () => {
  const p = owner.page;
  for (const c of ["Konkan Realty Developers", "Kaveri Textiles"]) {
    await p.getByRole("button", { name: c }).first().click();
    const d = p.getByRole("dialog").first();
    await d.getByRole("button", { name: "Record “yes” consent" }).click();
    await d.getByLabel(/Their exact words/).fill("Yes, call me about a demo - phone, 6 Nov");
    const box = d.getByRole("checkbox", { name: /Move to Interested/ });
    if (!(await box.isChecked())) await box.check();
    await d.getByRole("button", { name: "Save consent" }).click();
    await d.getByText("Stage: Interested").waitFor();
    await p.keyboard.press("Escape");
    await p.waitForTimeout(400);
  }
});
await step("Owner adds a caller limited to the client workspace and a viewer", async () => {
  const p = owner.page;
  await nav(p, "Setup");
  await p.getByRole("tab", { name: "Team & workspaces" }).click();
  const card = p.getByTestId("users-card");
  for (const [email, role, pw] of [["asha@qa.test", "caller", "asha-pass-1234"], ["vik@qa.test", "viewer", "vik-pass-12345"]]) {
    await card.locator("form").getByLabel("Email").fill(email);
    await card.locator("form").getByLabel("Role").selectOption(role);
    await card.getByLabel("Password (10+ characters)").fill(pw);
    await card.getByRole("button", { name: "Add login" }).click();
    await card.getByText(`Login added for ${email}.`).waitFor();
  }
  await card.locator("li", { hasText: "asha@qa.test" }).getByLabel("Acme Staffing").click();
  await card.getByText("now sees only: Acme Staffing").waitFor();
});

const asha = await session();
await step("The limited caller never sees the other client's lead on Dashboard, Call desk or Tasks", async () => {
  await signIn(asha.page, "asha@qa.test", "asha-pass-1234");
  for (const page of ["Dashboard", "Call desk", "Tasks & follow-ups", "Leads", "Companies"]) {
    await nav(asha.page, page);
    await asha.page.waitForTimeout(800);
    const text = await asha.page.locator("main").innerText();
    if (text.includes("Konkan")) throw new Error(`${page} shows Konkan Realty`);
  }
  await nav(asha.page, "Tasks & follow-ups");
  await asha.page.getByText("Kaveri Textiles").first().waitFor();
});
await step("…and changes her own password from Setup > My password (all sessions sign out)", async () => {
  const p = asha.page;
  await nav(p, "Setup");
  if (await p.getByRole("tab", { name: "Backup & restore" }).count()) throw new Error("caller sees the Backup tab");
  await p.getByRole("tab", { name: "My password" }).click();
  const f = p.getByTestId("own-password");
  await f.getByLabel("Current password").fill("asha-pass-1234");
  await f.getByLabel("New password (10+ characters)").fill("asha-new-pass-99");
  await f.getByLabel("New password again").fill("asha-new-pass-99");
  await f.getByRole("button", { name: "Change password" }).click();
  await p.getByRole("button", { name: "Sign in" }).waitFor({ timeout: 8000 });
  await signIn(p, "asha@qa.test", "asha-new-pass-99");
});

const vik = await session();
await step("A viewer sees the view-only note, no Hiring engine, and no refused calls on LinkedIn or Free experience", async () => {
  await signIn(vik.page, "vik@qa.test", "vik-pass-12345");
  await vik.page.getByTestId("viewer-note").waitFor();
  if (await vik.page.locator("nav.side").getByRole("button", { name: "Hiring engine", exact: true }).count()) throw new Error("Hiring engine in the viewer menu");
  vik.errors.length = 0;
  await nav(vik.page, "LinkedIn outreach");
  await vik.page.waitForTimeout(1500);
  if (vik.errors.length) throw new Error(vik.errors.join("; "));
  await nav(vik.page, "Free experience");
  await vik.page.getByRole("button", { name: "Save offer" }).click();
  await vik.page.getByText("Your role (viewer) cannot make changes").waitFor();
  if (vik.errors.some((e) => e.startsWith("pageerror"))) throw new Error(vik.errors.join("; "));
});

await step("Click-to-call refuses a javascript: link with a clear message", async () => {
  const p = owner.page;
  await nav(p, "Integrations");
  await p.getByRole("tab", { name: "Calls", exact: true }).click();
  await p.getByTestId("prov-calls-click_to_call").getByRole("button", { name: /Set up|Open/ }).click();
  const wz = p.getByTestId("wizard");
  await wz.getByRole("button", { name: "Next" }).click();
  await wz.locator("#f-click_to_call_url").fill("javascript:alert({phone})");
  await wz.getByRole("button", { name: "Save and continue" }).click();
  await wz.getByText("The link must start with https://").waitFor();
  await wz.getByRole("button", { name: "Close" }).first().click();
});

await step("A leads CSV download is written to the Audit tab", async () => {
  const p = owner.page;
  await nav(p, "Leads");
  await p.getByLabel("Select all shown").check();
  await Promise.all([p.waitForEvent("download"), p.getByTestId("download-leads-picked").click()]);
  await nav(p, "Setup");
  await p.getByRole("tab", { name: "Audit" }).click();
  await p.getByLabel("Action").selectOption({ label: "Downloads (CSV/JSON)" });
  await p.getByText("leads.csv").first().waitFor();
});

await browser.close();
for (const [ok, name] of results) console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
const failed = results.filter(([ok]) => !ok).length;
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
