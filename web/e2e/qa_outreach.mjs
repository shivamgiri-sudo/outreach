// QA sweep (outreach): the bugs found by using sending, replies, unsubscribe and the call desk like a real user.
// Run against a FRESH API started with OUTREACH_ADMIN_PASSWORD=qa-admin-pass-1 OUTREACH_IGNORE_BUSINESS_HOURS=1:
//   E2E_UI=http://localhost:5282 node e2e/qa_outreach.mjs
import { chromium } from "playwright";

const UI = process.env.E2E_UI || "http://localhost:5173";
const PASSWORD = process.env.E2E_PASSWORD || "qa-admin-pass-1";
const results = [];
async function step(name, fn) {
  try { await fn(); results.push([true, name]); } catch (e) { results.push([false, `${name} — ${e.message.split("\n")[0]}`]); }
}
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, timezoneId: "Asia/Kolkata" });
const page = await ctx.newPage();
page.on("dialog", (d) => d.accept());
const nav = async (name) => { await page.locator("nav.side").getByRole("button", { name, exact: true }).click(); await page.waitForTimeout(600); };
const drawerClose = () => page.getByRole("dialog").filter({ has: page.locator("#drawer-title") }).getByRole("button", { name: "Close", exact: true }).click();
const openLead = async (name) => { await nav("Leads"); await page.getByText(name, { exact: true }).first().click(); await page.waitForTimeout(600); };
const okDialog = async () => { const d = page.getByTestId("app-dialog"); const t = await d.innerText(); await d.getByRole("button").last().click(); return t; };
const must = (cond, what) => { if (!cond) throw new Error(what); };

await step("Sign in and fill 'Who signs the emails' in setup", async () => {
  await page.goto(UI);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByLabel("Company name").fill("Mas Callnet");
  await page.getByLabel("Product or service").fill("Bulk hiring and calling teams");
  await page.getByLabel("Who signs the emails").fill("Shivam Giri");
  await page.getByRole("button", { name: "Save and continue" }).click();
  await page.waitForTimeout(600);
  await page.getByRole("button", { name: "Skip setup" }).click();
});
await step("Paste two leads", async () => {
  await nav("Leads");
  await page.getByRole("button", { name: "Quick paste" }).click();
  await page.locator("textarea").first().fill("company,contact_name,title,email,phone\nSwiggy,Sneha Iyer,Talent Partner,sneha.iyer@swiggy.example,+91 90080 45678\n"
    + "Zomato Ltd,Priya Sharma,HR Manager,priya.sharma@zomato.example,+91 99001 23456\n");
  await page.getByRole("button", { name: "Import", exact: true }).click();
  await page.getByText(/Added 2/).waitFor();
});
await step("Default sequence preview is signed by the person, not 'Team <product>'", async () => {
  await nav("Sequences & templates");
  await page.locator("details summary", { hasText: "Steps" }).first().click();
  const names = (await page.locator("main details").first().innerText());
  must(!/BookMyInterview/i.test(names), "a step still names BookMyInterview");
  await page.getByRole("button", { name: "Preview designed email" }).first().click();
  const body = await page.frameLocator('iframe[title="Designed email preview"]').locator("body").innerText();
  must(body.includes("Shivam Giri") && !body.includes("Team Bulk hiring"), "sign-off: " + body.slice(-120));
  await page.getByRole("dialog").getByRole("button", { name: "Close" }).click();
});
await step("Send email now: lead panel and activity say practised, Outbox keeps it a sequence row", async () => {
  await openLead("Swiggy");
  await page.getByRole("tab", { name: "Outreach" }).click();
  await page.getByRole("button", { name: /email sequence$/ }).click();
  await page.getByText("Running — no email sent yet").waitFor();
  await page.getByRole("button", { name: "Send email now" }).click();
  await page.getByTestId("app-dialog").getByRole("button", { name: "Send now" }).click();
  must((await okDialog()).includes("Practised (not sent)"), "no practised note");
  await page.getByText("practised (not sent) on day 1").waitFor();
  await page.getByRole("tab", { name: "Activity" }).click();
  await page.getByText("Practice only (not sent)").first().waitFor();
  must(!(await page.getByText(/Sent \/ done · practice/).count()), "contradictory 'Sent / done · practice'");
  await drawerClose();
  await nav("Outbox & sending");
  await page.getByTestId("outbox-filters").getByLabel("Kind").selectOption("sequence");
  await page.getByTestId("outbox-table").getByText("sent now by").waitFor();
});
await step("Unsubscribe page names the company and reopening says already unsubscribed", async () => {
  await page.getByTestId("outbox-filters").getByLabel("Kind").selectOption("");
  await page.getByRole("button", { name: /^View email to sneha/ }).first().click();
  const link = (await page.getByTestId("email-body").innerText()).match(/https?:\/\/\S+\/u\/\S+/)[0];
  await page.getByRole("dialog", { name: "Email" }).getByRole("button", { name: "Close" }).click();
  const r = await (await browser.newContext()).newPage();
  await r.goto(link.replace(/^https?:\/\/[^/]+/, UI));
  await r.getByText("Stop all emails from Mas Callnet?").waitFor();
  await r.getByRole("button", { name: "Yes, unsubscribe me" }).click();
  await r.getByText("You are unsubscribed").waitFor();
  await r.goto(link.replace(/^https?:\/\/[^/]+/, UI));
  await r.getByText("already unsubscribed").waitFor();
  await r.goto(link.replace(/^https?:\/\/[^/]+/, UI).replace(/.$/, "x").replace(/xx$/, "xy"));
  await r.getByText("not valid").waitFor();
  must(!(await r.locator("body").innerText()).includes('"detail"'), "raw JSON shown");
});
await step("'Can we talk on Tuesday?' is read as a yes; thread shows the words; WhatsApp practice is confirmed", async () => {
  await openLead("Zomato Ltd");
  await page.getByRole("tab", { name: "Outreach" }).click();
  await page.locator("#reply-box").fill("Thanks. We are ramping up hiring next month. Can we talk on Tuesday at 3pm? WhatsApp me the details.");   // naming WhatsApp = WhatsApp consent (per-channel rule)
  await page.getByRole("button", { name: "Log reply" }).click();
  await page.getByText(/^Detected: interested/).waitFor();
  await page.getByLabel("WhatsApp message", { exact: true }).selectOption("first_after_yes");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  must((await okDialog()).includes("Practised (not sent)"), "no WhatsApp confirmation");
  await drawerClose();
  await nav("Replies");
  const row = page.getByTestId("reply-row").filter({ hasText: "Zomato" });
  await row.getByRole("button", { name: "Thread" }).click();
  const t = await row.getByTestId("reply-thread").innerText();
  must(t.includes("read as interested") && !t.includes("[interested]"), "thread: " + t.slice(-160));
});
await step("A team login can be assigned a reply", async () => {
  await nav("Setup");
  await page.getByRole("tab", { name: "Team & workspaces" }).click();
  const card = page.getByTestId("users-card");
  await card.getByLabel("Email").fill("priya.caller@mascallnet.example");
  await card.getByLabel("Name").fill("Priya Verma");
  await card.getByLabel("Role").selectOption("caller");
  await card.getByLabel("Password (10+ characters)").fill("caller-password-1");
  await card.getByRole("button", { name: "Add login" }).click();
  await page.waitForTimeout(600);
  await nav("Replies");
  await page.getByLabel("Only ones that need a person").uncheck();
  await page.getByTestId("reply-row").filter({ hasText: "Zomato" }).getByLabel("Assign to").selectOption({ label: "Priya Verma" });
  await page.getByText("Assigned to Priya Verma.").waitFor();
});
await step("Call script never reads out a blank brand value", async () => {
  await nav("Call desk");
  const s = await page.getByTestId("call-script").innerText();
  must(!/\{\{/.test(s), "raw placeholder in script: " + s);
});

await browser.close();
for (const [ok, name] of results) console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
process.exit(results.every(([ok]) => ok) ? 0 : 1);
