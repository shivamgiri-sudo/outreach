// QA (areas "contacts", "reports", "password"), only through the web UI.
//   Same rig as qa_admin.mjs. cd web && E2E_UI=http://localhost:5284 node e2e/qa_reports.mjs
// Covers: extra phone / email on a lead (make main), a person with designation + LinkedIn, change log rows; logging a
// call on an extra number (wrong number flags only that number); Reports tiles, activity log "number used", email
// preview with analysis + download link, CSV download; forgot-password form and an invalid reset link.
import { chromium } from "playwright";

const UI = process.env.E2E_UI || "http://localhost:5284";
const ADMIN = process.env.E2E_PASSWORD || "qa-admin-pass-1";
const SHOTS = process.env.SHOT_DIR || "/tmp";
const CSV = ["company,contact_name,title,email,phone,website",
  "Sharma Logistics Pvt Ltd,Rohit Sharma,Head of HR,rohit.sharma@sharmalogistics.example,+91 98110 23456,https://sharmalogistics.example"].join("\n");
const results = [];
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 }, acceptDownloads: true });
const p = await ctx.newPage();
const errors = [];
p.on("response", (r) => { if (r.url().includes("/api/") && r.status() >= 400) errors.push(`${r.status()} ${r.url().replace(UI, "")}`); });
p.on("pageerror", (e) => errors.push(`pageerror ${e.message}`));
const nav = async (label) => { await p.locator("nav.side").getByRole("button", { name: label, exact: true }).click(); await p.waitForTimeout(900); };
async function step(name, fn) {
  try { await fn(); results.push([true, name]); } catch (e) { results.push([false, `${name} — ${e.message.split("\n")[0]}`]); }
}
const drawer = () => p.getByRole("dialog").first();
const openLead = async () => { await nav("Leads"); await p.getByRole("button", { name: "Sharma Logistics Pvt Ltd" }).first().click(); await drawer().getByTestId("contact-points").waitFor(); };

await step("Sign in and import a lead", async () => {
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
  await p.getByText("Added 1").waitFor();
});
await step("Add another phone and email, make the email main", async () => {
  await openLead();
  const cp = drawer().getByTestId("contact-points");
  await cp.getByLabel("Phone number").fill("99887 76655");
  await cp.getByRole("button", { name: "Add" }).click();
  await cp.getByText("+919988776655").waitFor();
  await cp.getByLabel("Add a").selectOption("email");
  await cp.getByLabel("Email address").fill("rohit@sharma-group.example");
  await cp.getByRole("button", { name: "Add" }).click();
  await cp.getByText("rohit@sharma-group.example").waitFor();
  await cp.getByTestId("point-email").filter({ hasText: "rohit@sharma-group.example" }).getByRole("button", { name: "Make main" }).click();
  await p.waitForTimeout(800);
  const row = await cp.getByTestId("point-email").filter({ hasText: "rohit@sharma-group.example" }).innerText();
  if (!row.includes("Main")) throw new Error(`not main: ${row}`);
});
await step("Add a person with designation and LinkedIn, then edit the designation", async () => {
  const lp = drawer().getByTestId("lead-people");
  await lp.getByRole("button", { name: "Add person" }).click();
  const f = lp.getByTestId("person-form");
  await f.getByLabel("Name").fill("Neha Jain");
  await f.getByLabel("Designation").fill("CHRO");
  await f.getByLabel("Phone").fill("90000 11122");
  await f.getByLabel("LinkedIn profile").fill("linkedin.com/in/neha-jain");
  await f.getByRole("button", { name: "Add person" }).click();
  await lp.getByText("Neha Jain", { exact: true }).waitFor();
  await lp.getByRole("link", { name: "LinkedIn" }).waitFor();
  await lp.getByRole("button", { name: "Edit" }).click();
  await lp.getByTestId("person-form").getByLabel("Designation").fill("VP HR");
  await lp.getByTestId("person-form").getByRole("button", { name: "Save" }).click();
  await lp.getByText("VP HR").waitFor();
});
await step("Log a wrong-number call on the extra phone: only that number is flagged", async () => {
  const d = drawer();
  await d.getByRole("tab", { name: /Activity/ }).click();
  await d.getByTestId("call-number").selectOption("+919988776655");
  await d.getByTestId("call-remarks").fill("number belongs to someone else");
  await d.getByLabel("Log a call in one click").getByRole("button", { name: "Wrong number", exact: true }).click();
  await p.getByTestId("app-dialog").waitFor(); await p.getByTestId("dialog-ok").click();
  await p.waitForTimeout(1000);
  await d.getByRole("tab", { name: /Overview|Lead/ }).first().click().catch(() => {});
  await p.waitForTimeout(600);
  const phones = await d.getByTestId("contact-points").innerText();
  if (!/\+919988776655[\s\S]*Wrong number/.test(phones)) throw new Error(`extra not flagged: ${phones.slice(0, 200)}`);
  if (/\+919811023456\s*Main\s*Wrong/.test(phones)) throw new Error("main number flagged");
  await p.keyboard.press("Escape");
});
await step("Reports: tiles, activity log shows the number used, daily table", async () => {
  await nav("Reports");
  await p.getByTestId("rep-calls").waitFor();
  const calls = (await p.getByTestId("rep-calls").locator("b").innerText()).trim();
  if (calls !== "1") throw new Error(`calls ${calls}`);
  await p.getByTestId("daily-table").waitFor();
  await p.getByRole("tab", { name: "Activity log" }).click();
  const log = await p.getByTestId("activity-log").innerText();
  if (!log.includes("+919988776655") || !log.includes("number belongs to someone else")) throw new Error(log.slice(0, 300));
  if (!log.includes("Lead added")) throw new Error("no lead added row");
  await p.getByRole("tab", { name: "By person" }).click();
  await p.getByTestId("team-report").waitFor();
  await p.screenshot({ path: `${SHOTS}/reports.png`, fullPage: true });
});
await step("Email this report: HTML preview with analysis and a download link", async () => {
  await p.getByLabel("Also send to").fill("manager@team.example");   // the admin-password login has no address of its own
  await p.getByTestId("report-email").click();
  await p.getByTestId("report-email-msg").filter({ hasText: /Report emailed|not delivered/ }).waitFor();
  const frame = p.frameLocator("[data-testid=report-preview]");
  await frame.getByText("What the numbers say").waitFor();
  await frame.getByText("Download the activity log (CSV)").waitFor();
  const href = await frame.getByRole("link", { name: "Download the activity log (CSV)" }).getAttribute("href");
  const res = await p.request.get(href.replace(/^https?:\/\/[^/]+/, UI));
  if (res.status() !== 200 || !(await res.text()).includes("Sharma")) throw new Error(`link ${res.status()}`);
  await p.screenshot({ path: `${SHOTS}/report-email.png`, fullPage: true });
});
await step("CSV download from Reports", async () => {
  const [dl] = await Promise.all([p.waitForEvent("download"), p.getByTestId("report-csv").click()]);
  if (!/activity-log/.test(dl.suggestedFilename())) throw new Error(dl.suggestedFilename());
});
await step("Forgot password form answers the same for any address; a bad reset link is refused", async () => {
  const c2 = await browser.newContext({ viewport: { width: 1200, height: 900 } });
  const q = await c2.newPage();
  await q.goto(UI);
  await q.getByTestId("forgot-link").click();
  await q.getByLabel("Work email").fill("nobody@nowhere.example");
  await q.getByRole("button", { name: "Email me a reset link" }).click();
  await q.getByText("If that address has a login, a reset link is on its way.").waitFor();
  await q.goto(`${UI}/#reset?token=not-a-real-token-123456`);
  await q.reload();
  await q.getByTestId("reset-form").waitFor();
  await q.getByLabel("New password", { exact: true }).fill("brand-new-pass-9");
  await q.getByLabel("New password again").fill("brand-new-pass-9");
  await q.getByRole("button", { name: "Set new password" }).click();
  await q.getByText("This reset link has expired or was already used.").waitFor();
  await c2.close();
});

await browser.close();
for (const [ok, name] of results) console.log(`${ok ? "PASS" : "FAIL"} ${name}`);
const unexpected = errors.filter((e) => !e.includes("/api/password/reset"));
console.log(`errors: ${JSON.stringify(unexpected)}`);
process.exit(results.every(([ok]) => ok) && !unexpected.length ? 0 : 1);
