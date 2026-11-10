// QA (areas "LinkedIn auto-start", "communication plan"), only through the web UI. Same rig as qa_admin.mjs.
//   cd web && E2E_UI=http://localhost:5284 node e2e/qa_linkedin_plan.mjs
// Covers: the owner switches on daily auto-start and it stays on; a person is started on the LinkedIn sequence; the lead's
// Communication plan shows the LinkedIn steps (next / waiting / skipped), the email steps, and the follow-up to do.
import { chromium } from "playwright";

const UI = process.env.E2E_UI || "http://localhost:5284";
const ADMIN = process.env.E2E_PASSWORD || "qa-admin-pass-1";
const SHOTS = process.env.SHOT_DIR || "/tmp";
const CSV = ["company,contact_name,title,email,phone,website,linkedin_url",
  "Sharma Logistics Pvt Ltd,Rohit Sharma,Head of HR,rohit.sharma@sharmalogistics.example,+91 98110 23456,https://sharmalogistics.example,https://www.linkedin.com/in/rohit-sharma-qa"].join("\n");
const results = [];
const browser = await chromium.launch();
const p = await (await browser.newContext({ viewport: { width: 1400, height: 950 } })).newPage();
const errors = [];
p.on("response", (r) => { if (r.url().includes("/api/") && r.status() >= 400) errors.push(`${r.status()} ${r.url().replace(UI, "")}`); });
p.on("pageerror", (e) => errors.push(`pageerror ${e.message}`));
const nav = async (label) => { await p.locator("nav.side").getByRole("button", { name: label, exact: true }).click(); await p.waitForTimeout(900); };
async function step(name, fn) {
  try { await fn(); results.push([true, name]); } catch (e) { results.push([false, `${name} — ${e.message.split("\n")[0]}`]); }
}
const toLinkedIn = async () => { await p.evaluate(() => { location.hash = "#linkedin"; }); await p.waitForTimeout(1500); };
const settingsBox = () => p.locator("summary", { hasText: "LinkedIn settings" }).locator("xpath=ancestor::details[1]");

await step("Sign in and import a lead with a LinkedIn link", async () => {
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
await step("Owner switches on daily auto-start and it stays on", async () => {
  await toLinkedIn();
  await p.locator("summary", { hasText: "LinkedIn settings" }).click();
  await settingsBox().getByTestId("li-auto-start").check();
  await settingsBox().getByRole("button", { name: "Save" }).click();
  await p.waitForTimeout(1200);
  await p.reload(); await p.locator("main").waitFor(); await p.waitForTimeout(1500);
  await p.locator("summary", { hasText: "LinkedIn settings" }).click();
  if (!(await settingsBox().getByTestId("li-auto-start").isChecked())) throw new Error("auto-start not saved");
});
await step("Start the LinkedIn sequence for the suggested person", async () => {
  await p.getByRole("button", { name: "Start LinkedIn sequence" }).first().click();
  await p.getByText(/Rohit|Sharma Logistics/).first().waitFor();
  await p.waitForTimeout(1000);
});
await step("Communication plan shows LinkedIn and email steps and the follow-up", async () => {
  await nav("Leads");
  await p.getByRole("button", { name: "Sharma Logistics Pvt Ltd" }).first().click();
  const plan = p.getByRole("dialog").first().getByTestId("lead-plan");
  await plan.waitFor();
  const li = await plan.getByTestId("plan-linkedin").innerText();
  if (!/Step 1: connection request/.test(li) || !/Next/.test(li)) throw new Error(`linkedin: ${li.slice(0, 300)}`);
  if (!/Starts after they accept/.test(li) || !/Skipped until a case-study link/.test(li)) throw new Error(`linkedin waiting/skipped: ${li.slice(0, 400)}`);
  const em = await plan.getByTestId("plan-email").innerText();
  if (!/Day 1/.test(em) && !/Not on an email sequence/.test(em)) throw new Error(`email: ${em.slice(0, 200)}`);
  const fu = await plan.getByTestId("plan-followups").innerText();
  if (!/LinkedIn step 1/.test(fu)) throw new Error(`follow-ups: ${fu}`);
  await p.screenshot({ path: `${SHOTS}/lead-plan.png`, fullPage: true });
});
await step("Lead shows who has contacted this person on LinkedIn (own workspace only)", async () => {
  await p.getByRole("dialog").first().getByRole("tab", { name: "Outreach" }).click();
  const box = p.getByRole("dialog").first().getByTestId("li-contacts");
  await box.waitFor({ timeout: 8000 });
  const t = await box.innerText();
  if (!/this lead/.test(t) || !/on the sequence, nothing sent yet/.test(t)) throw new Error(t.slice(0, 300));
  await p.screenshot({ path: `${SHOTS}/lead-contacts.png`, fullPage: true });
  await p.keyboard.press("Escape");
});
await step("Start LinkedIn on a lead with no link: it is looked up, not refused", async () => {
  await nav("Leads");
  await p.getByRole("button", { name: "Quick paste" }).click();
  await p.locator("textarea").first().fill("company,contact_name,title,email\nFind Me Pvt Ltd,Anita Desai,HR Head,anita@findme.example");
  await p.getByRole("button", { name: "Import", exact: true }).click();
  await p.getByText("Added 1").waitFor();
  await p.getByRole("checkbox", { name: /Select all shown/ }).check();
  await p.getByRole("checkbox", { name: /Select Rohit/ }).uncheck().catch(() => {});
  await p.getByRole("button", { name: "Start LinkedIn outreach" }).click();
  await p.getByText(/being looked up/).first().waitFor({ timeout: 10000 });
  await p.getByRole("button", { name: "Find Me Pvt Ltd" }).first().click();
  const d = p.getByRole("dialog").first();
  await d.getByRole("tab", { name: "Outreach" }).click();
  await d.getByText(/Finding profile/).first().waitFor({ timeout: 8000 });
  await p.screenshot({ path: `${SHOTS}/lead-finding.png`, fullPage: true });
});

await browser.close();
for (const [ok, name] of results) console.log(`${ok ? "PASS" : "FAIL"} ${name}`);
console.log(`errors: ${JSON.stringify(errors)}`);
process.exit(results.every(([ok]) => ok) && !errors.length ? 0 : 1);
