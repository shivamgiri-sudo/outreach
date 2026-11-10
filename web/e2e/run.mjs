// End-to-end walk through every page and action. Run with both servers up (see README).
import { chromium } from "playwright";
import fs from "node:fs";

const UI = process.env.E2E_UI || "http://localhost:5173";
const API = process.env.E2E_API || "http://localhost:8000";
const SHOTS = process.env.SHOT_DIR || "e2e-shots";
fs.mkdirSync(SHOTS, { recursive: true });

const results = [];
const problems = [];
let page;

async function step(name, fn) {
  try {
    await fn();
    results.push([true, name]);
  } catch (e) {
    results.push([false, `${name} — ${e.message.split("\n")[0]}`]);
    await page.screenshot({ path: `${SHOTS}/FAIL-${results.length}.png` }).catch(() => {});
  }
}
// Menu click: on a phone the menu is behind the "Open menu" button, so open it first. Scoped to the main menu so a page
// button with the same words is never clicked by mistake.
const nav = async (label) => {
  const menu = page.getByRole("button", { name: "Open menu" });
  if (await menu.isVisible().catch(() => false)) await menu.click();
  await page.locator("nav.side").getByRole("button", { name: label, exact: false }).first().click();
};
// In-app confirm/prompt boxes (Dialog.jsx) replaced window.confirm/prompt: answer the open one.
const answerDialog = async (value) => {
  const box = page.getByTestId("app-dialog");
  await box.waitFor({ timeout: 5000 });
  if (value !== undefined) await box.getByTestId("dialog-input").fill(value);
  await box.getByTestId("dialog-ok").click();
  await box.waitFor({ state: "detached" });
};
// The LinkedIn page has tabs (Today, Stages, Agent & accounts, Messages, Settings & tools): open one if this build has them.
const liTab = async (name) => {
  const t = page.getByRole("tablist", { name: "LinkedIn sections" }).getByRole("tab", { name, exact: true });
  if (await t.count()) { await t.click(); await page.waitForTimeout(400); }
};
const text = (t) => page.getByText(t, { exact: false }).first();
const expectText = async (t, timeout = 5000) => text(t).waitFor({ timeout });
const closeDrawer = async () => {
  if (await page.locator(".drawer").count()) {
    await page.getByRole("button", { name: "Close" }).click();
    await page.locator(".drawer").waitFor({ state: "detached" });
  }
};
const shot = (n) => page.screenshot({ path: `${SHOTS}/${n}.png`, fullPage: true });

const IMPORT_CSV = `company,contact_name,title,email,phone,website,open_roles,company_size,country
Acme Retail,Ravi Kumar,Founder,hr@acme.invalid,98765 43210,https://acme.invalid,12,200,IN
Acme Retail,Ravi Kumar,Founder,hr@acme.invalid,98765 43210,https://acme.invalid,12,200,IN
Beta Logistics,Sam Rao,HR Head,sam@beta.invalid,,https://beta.invalid,5,80,IN
Ghost Co,Nobody,,not-an-email,,,0,5,US`;

const SHEET_CSV = `Company Name,Contact Person,Designation,Contact Number,Email ID,Open Position,City,Company Sector,Job Portal Source,Job Posting Link,Posting Date,Days Live,No. of Openings,Position Level,Caller Name,Call Outcome,Demo Date & Time,Lead Status,Next Follow-up Date,Remarks,Company Size (employees),LinkedIn Profile URL,Contact Confidence,Status,Remarks
Delta Steels,,,9000030003 / 9811122233,,Sales Head,Noida,Manufacturing,Other,https://w.com/9,07-Oct-2026,26,1,Manager,,,,Contacted,,note,Not verified,,Search link only,Not Connected,Not Responding`;

const JOB_HTML = `<script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting","title":"Telecaller","datePosted":"2026-09-20","hiringOrganization":{"@type":"Organization","name":"Epsilon BPO","sameAs":"https://epsilon.invalid"},"jobLocation":{"address":{"addressLocality":"Pune"}}}</script>`;

// HEADED=1 opens a visible browser so you can watch the run (slowed down a little).
const browser = await chromium.launch(process.env.HEADED ? { headless: false, slowMo: 250 } : {});
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
page = await ctx.newPage();
page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
page.on("console", (m) => m.type() === "error" && problems.push(`console: ${m.text()}`));
page.on("response", (r) => r.status() >= 400 && !r.url().includes("favicon") && problems.push(`HTTP ${r.status()} ${r.url()}`));
page.on("dialog", async (d) => {
  const t = d.message();
  await d.accept(t.includes("applicants") ? "120" : t.includes("Which company") ? "Zeta Foods" : t.includes("email") ? "owner@zeta.in" : "Yes, please send it");
});

await page.goto(UI);

// ---------- Dashboard (empty) ----------
await step("Dashboard loads with empty state and setup banner", async () => {
  await expectText("Dashboard");
  await expectText("connections are set up");
  await expectText("No hot leads waiting");
  await shot("01-dashboard-empty");
});

// ---------- Setup ----------
await step("Setup: opens on the go-live checklist; badge counts the required steps left", async () => {
  await nav("Setup");
  await expectText("Go-live checklist");
  await expectText("0 of 5 required steps done");
  await page.locator(".badge-dot").filter({ hasText: "5" }).waitFor();
  await page.getByRole("tab", { name: "Connections" }).click();
  await expectText("0 of 11 connected");
});
await step("Setup: incomplete API connector shows 'Missing details'", async () => {
  const card = page.locator(".card", { hasText: "WhatsApp Business" });
  await card.locator("input[placeholder*='gupshup']").fill("gupshup");
  await card.getByRole("button", { name: "Save" }).click();
  await card.getByText("Missing details").waitFor();
});
await step("Setup: complete API connector shows Connected and masks the key", async () => {
  const card = page.locator(".card", { hasText: "Apollo" }).first();
  await card.locator("input[type=password]").fill("abcd1234secret");
  await card.getByRole("button", { name: "Save" }).click();
  await card.getByText("Connected").waitFor();
  await card.locator("summary").click();
  await card.locator("input[type=password]").waitFor();
  const ph = await card.locator("input[type=password]").getAttribute("placeholder");
  if (ph !== "••••cret") throw new Error(`key not masked: ${ph}`);
});
await step("Setup: manual-only connector can be switched on", async () => {
  const card = page.locator(".card", { hasText: "LinkedIn Sales Navigator" });
  await card.getByRole("button", { name: "Use manual mode" }).click();
  await card.getByText("Connected").waitFor();
});
await step("Setup: counter and badge update", async () => {
  await expectText("2 of 11 connected");
  await page.locator(".badge-dot").filter({ hasText: "5" }).waitFor();
  await shot("02-setup");
});

// ---------- Leads: import ----------
await step("Leads: empty state", async () => {
  await nav("Leads");
  await expectText("No leads yet");
});
await step("Leads: CSV paste import dedupes and rejects bad rows", async () => {
  await page.getByRole("button", { name: "Quick paste" }).click();
  await page.locator("textarea").first().fill(IMPORT_CSV);
  await page.getByRole("button", { name: "Import", exact: true }).click();
  await expectText("Added 2");
  await expectText("skipped 1 duplicates");
  await expectText("rejected 1");
  await page.locator("tr.click").nth(1).waitFor();
  if ((await page.locator("tr.click").count()) !== 2) throw new Error("expected 2 leads");
});
await step("Leads: data-quality bar is shown", async () => {
  await expectText("Of 2 companies");
});
await step("Leads: team-sheet import (Leads tab) works", async () => {
  await page.getByRole("button", { name: "Quick paste" }).click();
  await page.locator("select").first().selectOption("team sheet — Leads tab");
  await page.locator("textarea").first().fill(SHEET_CSV);
  await page.getByRole("button", { name: "Import", exact: true }).click();
  await expectText("Read 1 sheet rows: added 1");
  await text("Delta Steels").waitFor();
});
await step("Leads: job-page reader creates a company with hiring signals", async () => {
  await page.getByRole("button", { name: "Read job pages" }).click();
  await page.locator("textarea[placeholder*='paste page HTML']").fill(JOB_HTML);
  await page.getByRole("button", { name: "Read jobs" }).click();
  await expectText("Read 1 job(s): 1 new company");
  await text("Epsilon BPO").waitFor();
});
await step("Leads: website contact finder refuses private addresses", async () => {
  await page.getByRole("button", { name: "Find contacts on a website" }).click();
  await page.locator("input[placeholder*='company.com/contact']").fill("http://127.0.0.1:8000/");
  await page.getByRole("button", { name: "Find & add" }).click();
  await expectText("not a public website");
});
await step("Leads: sorted by score (highest first)", async () => {
  const first = await page.locator("tr.click").first().innerText();
  if (!first.includes("Acme Retail")) throw new Error(`top lead is: ${first.split("\n")[0]}`);
  await shot("03-leads");
});

// ---------- Lead drawer ----------
await step("Drawer: opens with signals, suggestion and history", async () => {
  await page.locator("tr.click", { hasText: "Acme Retail" }).click();
  await expectText("Suggested next step");
  await expectText("Buying signals");
  await expectText("Imported from apollo");
});
await step("Drawer: cannot jump to Interested without consent (error shown)", async () => {
  // move new -> verified -> enriched -> contacted -> engaged using the primary button
  for (const to of ["verified", "enriched", "contacted", "engaged"]) {
    await page.getByRole("button", { name: `Move to ${to}` }).click();
    await expectText(`Stage: ${to}`);
  }
  await page.getByRole("button", { name: "Move to interested" }).click();
  await expectText("Cannot move from engaged to interested");
});
await step("Drawer: record consent then move to Interested", async () => {
  await page.getByRole("button", { name: "Record “yes” consent" }).click();
  await page.getByLabel("Their exact words, and where (saved as proof of consent)").fill("Yes, send it on WhatsApp — call with Priya");
  await page.getByRole("button", { name: "Save consent" }).click();
  await expectText("Consent saved");
  await page.getByRole("button", { name: "Move to interested" }).click();
  await expectText("Stage: interested");
});
await step("Drawer: create demo link", async () => {
  await page.getByRole("button", { name: "Create demo link" }).click();
  await expectText("Copied: http://localhost:8000/demo/");
  await expectText("Step: invited");
});
await step("Drawer: record product activity moves the funnel step", async () => {
  await page.locator("select").filter({ hasText: "Became a paying customer" }).selectOption("candidate_screened:5");
  await expectText("Step: first_candidate");
  await expectText("candidates screened 5");
});
await step("Drawer: free pilot request logged", async () => {
  await page.getByRole("button", { name: "Offer free pilot on their applicants" }).click();
  await page.getByLabel("How many applicants do they already have?").fill("120");
  await page.getByRole("button", { name: "Log pilot" }).click();
  await expectText("Free pilot on 120 existing applicants");
});
await step("Drawer: referral creates a new lead and gives bonus", async () => {
  await page.getByRole("button", { name: "Log a referral" }).click();
  await page.getByLabel("Which company do they recommend?").fill("Zeta Foods");
  await page.getByLabel("Contact email (optional)").fill("owner@zeta.in");
  await page.getByRole("button", { name: "Save referral" }).click();
  await expectText("+10 referral bonus");
});
await step("Drawer: WhatsApp first message goes out in practice mode and moves the stage", async () => {
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expectText("Stage: WhatsApp active");
  await expectText("first_after_yes — dry_run");
});
await step("Drawer: free-text WhatsApp refused until they message on WhatsApp (Meta's 24h rule)", async () => {
  await page.locator("select").filter({ hasText: "Write my own" }).selectOption("custom");
  await page.locator("textarea[placeholder='Your message']").fill("Thanks Ravi, sending the details now.");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expectText("within 24 hours of their last WhatsApp message");
  await page.locator("select").filter({ hasText: "Write my own" }).selectOption("first_after_yes");
});
await step("Drawer: WhatsApp refused for a lead without consent", async () => {
  await page.getByRole("button", { name: "Close" }).click();
  await page.locator("tr.click", { hasText: "Delta Steels" }).click();
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expectText("No recorded consent");
  await page.getByRole("button", { name: "Close" }).click();
});
await step("Drawer: reopen Acme for the sequence steps", async () => {
  await page.locator("tr.click", { hasText: "Acme Retail" }).click();
  await expectText("Stage: WhatsApp active");
});
await step("Drawer: start sequence, log note and reply", async () => {
  await page.getByRole("button", { name: "Start 6-step email sequence" }).click();
  await expectText("Running — no email sent yet");
  await page.locator("textarea").last().fill("Spoke to Ravi on the phone");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expectText("Spoke to Ravi on the phone");
  await page.locator("input[placeholder='Yes please send the story']").fill("Can you tell me the price?");
  await page.getByRole("button", { name: "Log reply" }).click();
  await expectText("Detected: question");
  await expectText("Stopped (replied)");
  await shot("04-drawer");
});
await step("Drawer: closes", async () => {
  await page.getByRole("button", { name: "Close" }).click();
  await page.locator(".drawer").waitFor({ state: "detached" });
});

// ---------- Outbox ----------
await step("Outbox: save settings", async () => {
  await nav("Outbox");
  await page.locator("input[type=number]").fill("5");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expectText("Settings saved");
});
await step("Outbox: run sequences sends a dry-run email with demo link and unsubscribe", async () => {
  await nav("Leads");
  await page.locator("tr.click", { hasText: "Beta Logistics" }).click();
  await page.getByRole("button", { name: "Start 6-step email sequence" }).click();
  await expectText("Running");
  await page.getByRole("button", { name: "Close" }).click();
  await nav("Outbox");
  await page.getByRole("button", { name: "Run sequences now" }).click();
  await expectText("email(s) practised (not sent)");
  await page.locator("td", { hasText: "Are you closing vacancies" }).waitFor();
  await page.locator("tbody .pill", { hasText: "practice" }).first().waitFor();
  await shot("05-outbox");
});
await step("Outbox: running again sends nothing new", async () => {
  await page.getByRole("button", { name: "Run sequences now" }).click();
  await expectText("0 email(s) practised");
});

await step("Outbox: WhatsApp table lists the practice messages", async () => {
  await expectText("WhatsApp messages");
  await page.locator("td", { hasText: "first_after_yes" }).first().waitFor();
});

// ---------- Call desk ----------
await step("Call desk: interested lead listed, outcome can be logged", async () => {
  await nav("Call desk");
  await text("Acme Retail").waitFor();
  await page.locator("select").first().selectOption("Connected – interested");
  await page.waitForTimeout(400);
  await expectText("Call script");
  await shot("06-calldesk");
});

// ---------- Templates ----------
await step("Templates: 4 email steps from the backend + other messages + copy", async () => {
  await nav("Sequences");
  await expectText("Email 1 — compromised hiring");
  await expectText("Email 4 — process gap (Day 12)");
  await expectText("Nudge: demo not opened");
  await page.getByRole("button", { name: "Copy" }).first().click();
  await expectText("Copied");
});

// ---------- Stories ----------
await step("Stories: add a story, permission flag shown", async () => {
  await nav("Success stories");
  await expectText("No stories yet");
  await page.locator("input[placeholder*='Retail chain']").fill("Orion Softworks onboarded");
  await page.locator("textarea").fill("Positions shared after demo");
  await page.getByRole("button", { name: "Save story" }).click();
  await expectText("No permission yet");
  await expectText("Orion Softworks onboarded");
});

// ---------- Prompt generator ----------
await step("Prompt generator: builds prompt with guardrails", async () => {
  await nav("Prompt generator");
  await page.locator("input[placeholder*='BPO']").fill("BPO");
  await page.locator("input[placeholder*='Telecaller']").fill("Telecaller");
  await page.getByRole("button", { name: "Generate prompt" }).click();
  await expectText("Never invent numbers");
  await expectText("BPO industry");
  await shot("07-prompt");
});

// ---------- Growth ----------
await step("Free experience: funnel, nudges, conversion heads-up render", async () => {
  await nav("Free experience");
  await expectText("Did they reach a result?");
  await expectText("First candidate screened");
  await expectText("Time for an honest heads-up");
});
await step("Free experience: change offer and capacity maths updates", async () => {
  const card = page.locator(".card", { hasText: "How many emails can we safely send" });
  const inputs = card.locator("input[type=number]");
  await inputs.nth(0).fill("3");
  await inputs.nth(1).fill("30");
  await card.getByRole("button", { name: "Recalculate" }).click();
  await expectText("90");
  await inputs.nth(1).fill("120");
  await card.getByRole("button", { name: "Recalculate" }).click();
  await expectText("risks the spam folder");
  await inputs.nth(1).fill("30");
  await card.getByRole("button", { name: "Recalculate" }).click();
});
await step("Free experience: partners add + status change", async () => {
  await page.locator("input[placeholder='Name']").fill("HR Hub Pune");
  await page.locator("input[placeholder='Contact']").fill("admin@hrhub.in");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expectText("HR Hub Pune");
  const sel = page.locator(".card", { hasText: "HR Hub Pune" }).locator("select").last();
  await sel.selectOption("active");
  await page.waitForTimeout(300);
  await shot("08-growth");
});

// ---------- Workflows ----------
await step("Workflows: add suggested rules, create a custom one, toggle and delete", async () => {
  await nav("Simple rules");
  await expectText("No rules yet");
  await page.getByRole("button", { name: "Add suggested rules" }).click();
  await expectText("Yes by email → WhatsApp the sample screen");
  await expectText("No reply after 14 days → pause (not now)");
  await page.locator("input[placeholder*='Yes by email']").fill("Pricing question → task");
  await page.locator("select").nth(0).selectOption("reply");
  await page.locator("select").nth(1).selectOption("question");
  await page.locator("select").nth(2).selectOption("task");
  await page.locator("input[placeholder='Text']").fill("Answer the pricing question");
  await page.getByRole("button", { name: "Add rule", exact: true }).click();
  await expectText("Rule added.");
  await expectText("Your rules (3)");
  await page.locator("div.row", { hasText: "No reply after 14 days" }).getByRole("checkbox").uncheck();
  await page.locator("div.row", { hasText: "No reply after 14 days" }).getByRole("button", { name: "Delete" }).click();
  await expectText("Your rules (2)");
  await shot("12-workflows");
});
await step("Workflows: time-based rules check runs", async () => {
  await page.getByRole("button", { name: "Check time-based rules now" }).click();
  await expectText("Checked time-based rules");
});
await step("Workflows: a 'yes' reply sends WhatsApp by rule, and an AI-less draft is offered first", async () => {
  await nav("Leads");
  await page.getByRole("button", { name: "Quick paste" }).click();
  await page.locator("textarea").first().fill("company,contact_name,email,phone,website\nOmega Pharma,Priya Shah,priya@omega.invalid,98111 33344,https://omega.invalid");
  await page.getByRole("button", { name: "Import", exact: true }).click();
  await expectText("Added 1");
  await page.locator("tr.click", { hasText: "Omega Pharma" }).click();
  await page.locator("input[placeholder='Yes please send the story']").fill("How much does it cost?");
  await page.getByRole("button", { name: "Suggest a reply" }).click();
  await expectText("they sound: question");
  await expectText("Connect the AI writer in Setup");
  await page.getByRole("button", { name: "Copy draft" }).click();
  await page.locator("input[placeholder='Yes please send the story']").fill("Yes please send the story");
  await page.getByRole("button", { name: "Log reply" }).click();
  await expectText("Detected: interested");
  await expectText("Stage: WhatsApp active");
  await expectText("sent WhatsApp first_after_yes");
  await shot("13-workflow-effect");
});
await step("Safety: 'Not interested' is a no — no consent, no WhatsApp, plain explanation shown", async () => {
  await closeDrawer();
  await nav("Leads");
  await page.getByRole("button", { name: "Quick paste" }).click();
  await page.locator("textarea").first().fill("company,contact_name,email,phone,website\nSigma Foods,Asha Rao,asha@sigma.invalid,98111 55566,https://sigma.invalid");
  await page.getByRole("button", { name: "Import", exact: true }).click();
  await expectText("Added 1");
  await page.locator("tr.click", { hasText: "Sigma Foods" }).click();
  const before = (await (await fetch(`${API}/api/whatsapp/outbox`)).json()).length;
  await page.locator("input[placeholder='Yes please send the story']").fill("Not interested, thanks");
  await page.getByRole("button", { name: "Log reply" }).click();
  await expectText("Detected: not_interested");
  await expectText("Moved to Disqualified");
  await expectText("Stage: disqualified");
  const after = (await (await fetch(`${API}/api/whatsapp/outbox`)).json()).length;
  if (after !== before) throw new Error("a WhatsApp message was sent after a 'no'");
});
await step("Safety: a 'sure' after unsubscribing keeps the lead unsubscribed", async () => {
  await page.locator("input[placeholder='Yes please send the story']").fill("Stop sending me emails");
  await page.getByRole("button", { name: "Log reply" }).click();
  await expectText("Detected: unsubscribe");
  await page.locator("input[placeholder='Yes please send the story']").fill("sure");
  await page.getByRole("button", { name: "Log reply" }).click();
  await expectText("nothing was changed automatically");
  await expectText("Stage: unsubscribed");
});
await step("Safety: an out-of-office reply is logged only", async () => {
  await closeDrawer();
  await nav("Leads");
  await page.locator("tr.click", { hasText: "Omega Pharma" }).click();
  await page.locator("input[placeholder='Yes please send the story']").fill("Automatic reply: I am out of office until Monday");
  await page.getByRole("button", { name: "Log reply" }).click();
  await expectText("Detected: auto_reply");
  await expectText("Logged only");
  await page.getByRole("button", { name: "Close" }).click();
});
await step("Leads: Apollo button appears once Apollo is connected in API mode", async () => {
  await page.getByRole("button", { name: "Look up emails in Apollo (10)" }).waitFor();
});
await step("Outbox: 'Send for real' is off by default and refused until the go-live requirements are met", async () => {
  await nav("Outbox");
  await expectText("Practice mode: nothing leaves the app");
  await expectText("Before you can switch on real sending");
  const box = page.getByRole("checkbox", { name: "Send for real" });
  if (await box.isChecked()) throw new Error("live sending should default to off");
  await box.check();
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expectText("Real sending stays off until");
  await nav("Dashboard");
  await nav("Outbox");
  await page.waitForTimeout(400);
  if (await page.getByRole("checkbox", { name: "Send for real" }).isChecked()) throw new Error("live sending was switched on without the requirements");
  await page.getByRole("button", { name: "Run sequences now" }).click();
  await expectText("practised");
  const statuses = await page.locator("tbody .pill").allInnerTexts();
  if (statuses.some((t) => t === "sent")) throw new Error("something was really sent without a connected domain");
});

// ---------- Go live and in-app integration ----------
await step("Go live: checklist shows required steps and what is missing", async () => {
  await nav("Setup");
  await expectText("required steps done");
  const leadsCard = page.locator(".card", { hasText: "Load your leads" });
  await leadsCard.getByText("Done").waitFor();
  const urlCard = page.locator(".card", { hasText: "Set your public web address" });
  await urlCard.getByText("To do").waitFor();
  await text("Switch on real sending").waitFor();
  await shot("14-golive-before");
});
await step("Go live: 'Fix this' opens the page where it is fixed", async () => {
  await page.locator(".card", { hasText: "Set your public web address" }).getByRole("button", { name: "Fix this" }).click();
  await page.getByRole("heading", { name: "Setup" }).waitFor();
});
await step("Setup basics: public address, generated secret and limits confirmation are saved", async () => {
  const addr = page.locator("input[placeholder*='outreach.yourcompany']");
  await addr.fill("https://outreach.test.invalid");
  await page.getByRole("button", { name: "Generate" }).click();
  await page.getByRole("checkbox", { name: /free limits/ }).check();
  await page.getByRole("button", { name: "Save basics" }).click();
  await expectText("Saved.");
});
await step("Setup basics: values survive a reload and the secret is masked", async () => {
  await page.reload();
  await nav("Setup");
  await page.getByRole("tab", { name: "Connections" }).click();
  const addr = page.locator("input[placeholder*='outreach.yourcompany']");
  let value = "";
  for (let i = 0; i < 20 && !value; i++) { value = await addr.inputValue(); if (!value) await page.waitForTimeout(150); }
  if (value !== "https://outreach.test.invalid") throw new Error(`address is "${value}"`);
  const secret = await page.locator("input[placeholder='Click Generate']").inputValue();
  if (!secret.startsWith("••••")) throw new Error(`secret not masked: ${secret}`);
  if (!(await page.getByRole("checkbox", { name: /free limits/ }).isChecked())) throw new Error("limits box not saved");
});
await step("Setup: privacy notice link is saved and the owner sees the Team logins card", async () => {
  await page.getByLabel("Privacy notice link (optional)").fill("https://bookmyinterview.test.invalid/privacy");
  await page.getByRole("button", { name: "Save basics" }).click();
  await expectText("Saved.");
  await page.getByRole("tab", { name: "Team & workspaces" }).click();
  await page.getByTestId("users-card").getByText("Team logins").waitFor();
  await page.getByTestId("users-card").getByRole("button", { name: "Add login" }).waitFor();
  await page.getByRole("tab", { name: "Connections" }).click();
  const settings = await (await fetch(`${API}/api/settings`)).json();
  if (settings.privacy_url !== "https://bookmyinterview.test.invalid/privacy") throw new Error(`privacy_url ${settings.privacy_url}`);
});
await step("Setup: test buttons answer honestly (WhatsApp, AI writer, sending domain)", async () => {
  const wa = page.locator(".card", { hasText: "WhatsApp Business" });
  await wa.getByRole("button", { name: "Test connection" }).click();
  await wa.getByText("practice mode").waitFor();
  const ai = page.locator(".card", { hasText: "AI writer" });
  await ai.getByRole("button", { name: "Test connection" }).click();
  await ai.getByText("Connect the AI writer first").waitFor();
  const mail = page.locator(".card", { hasText: "Cold-email sending domain" });
  await mail.locator("input[placeholder*='test email to']").fill("me@teammas.in");
  await mail.getByRole("button", { name: "Test connection" }).click();
  await mail.getByText("Connect the sending domain first").waitFor();
});
await step("Setup: calling link needs {phone} and then drives the call desk", async () => {
  const tel = page.locator(".card", { hasText: "Calling (click-to-call)" });
  await tel.locator("input[type=text]").first().fill("https://dial.invalid/call");
  await tel.getByRole("button", { name: "Save", exact: true }).click();
  await tel.getByRole("button", { name: "Test connection" }).click();
  await tel.getByText("must contain {phone}").waitFor();
  await tel.locator("summary").click();
  await tel.locator("input[type=text]").first().fill("https://dial.invalid/call?n={phone}");
  await tel.getByRole("button", { name: "Save", exact: true }).click();
  await tel.getByRole("button", { name: "Test connection" }).click();
  await tel.getByText("Looks right").waitFor();
});
await step("Call desk: phone numbers use the click-to-call link", async () => {
  await nav("Call desk");
  const link = page.locator("td a[href*='dial.invalid']").first();
  await link.waitFor();
  const href = await link.getAttribute("href");
  if (!href.includes("n=%2B91")) throw new Error(`bad dial link ${href}`);
});
await step("Go live: public address and limits now show Done; real sending still To do", async () => {
  await nav("Setup");
  await page.locator(".card", { hasText: "Set your public web address" }).getByText("Done").waitFor();
  await page.locator(".card", { hasText: "Confirm the free limits" }).getByText("Done").waitFor();
  await page.locator(".card", { hasText: "Switch on real sending" }).getByText("To do").waitFor();
  await page.locator(".card", { hasText: "Send yourself a test email" }).getByText("To do").waitFor();
  await shot("15-golive-after");
});
await step("Demo links now use the saved public address", async () => {
  const leads = await (await fetch(`${API}/api/leads`)).json();
  const acme = leads.find((l) => l.company === "Acme Retail");
  const r = await fetch(`${API}/api/leads/${acme.id}/demo-link`, { method: "POST" });
  const url = (await r.json()).url;
  if (!url.startsWith("https://outreach.test.invalid/demo/")) throw new Error(url);
});

// ---------- Ease of use ----------
await step("Today: lists what to do next and opens the lead; Escape closes the panel", async () => {
  await nav("Dashboard");
  await expectText("Today");
  const item = page.locator(".today-item", { hasText: "Answer a question" }).first();
  await item.waitFor();
  await item.getByRole("button", { name: "Open lead" }).click();
  await page.locator(".drawer[role=dialog]").waitFor();
  await page.keyboard.press("Escape");
  await page.locator(".drawer").waitFor({ state: "detached" });
});
await step("Today: a task can be marked done", async () => {
  const before = await page.locator(".today-item.k-task").count();
  await page.locator(".today-item.k-task").first().getByRole("button", { name: "Done" }).click();
  for (let i = 0; i < 20 && (await page.locator(".today-item.k-task").count()) === before; i++) await page.waitForTimeout(150);
  if ((await page.locator(".today-item.k-task").count()) !== before - 1) throw new Error("task not removed");
});
await step("Leads: search, stage filter and sort", async () => {
  await nav("Leads");
  await page.getByLabel("Search leads").fill("omega");
  await expectText("Showing 1 of");
  await page.getByLabel("Search leads").fill("");
  await page.getByLabel("Filter by stage").selectOption("unsubscribed");
  await page.locator("tr.click", { hasText: "Sigma Foods" }).waitFor();
  await page.getByLabel("Filter by stage").selectOption("");
  await page.locator(".th-sort", { hasText: "Company" }).click();
  const names = await page.locator(".leads-table tbody td[data-label=Company]").allInnerTexts();
  const sorted = [...names].sort((a, b) => a.localeCompare(b));
  if (names.join("|") !== sorted.join("|")) throw new Error(`not sorted: ${names.join(", ")}`);
});
await step("Leads: pick several leads and start the email sequence in one go", async () => {
  await page.getByLabel("Select Zeta Foods").check();
  await page.getByLabel("Select Omega Pharma").check();
  await expectText("2 selected");
  await page.getByRole("button", { name: "Start email sequence" }).click();
  await expectText("Started the email sequence for");
});
await step("Leads: Decision Makers tab adds the hand-found person to the right company", async () => {
  await page.getByRole("button", { name: "Quick paste" }).click();
  await page.locator("select").filter({ hasText: "team sheet — Leads tab" }).selectOption("team sheet — Decision Makers tab");
  await page.locator("textarea").first().fill("Company,Name,Designation (as shown),LinkedIn Profile URL,Where I found it,Confidence,Fits 50–400?\nEpsilon BPO,Nisha Verma,HR Head,https://www.linkedin.com/in/nisha-example,Company site,Medium,Yes");
  await page.getByRole("button", { name: "Import", exact: true }).click();
  await expectText("Matched 1 people to companies, filled 1");
  await page.locator("tr.click", { hasText: "Epsilon BPO" }).click();
  await expectText("Nisha Verma");
});
await step("Drawer: LinkedIn drafts are ready to copy (sent by hand)", async () => {
  await expectText("LinkedIn (you send these by hand)");
  await expectText("Connection note");
  await expectText("Open profile");
  await page.keyboard.press("Escape");
});
await step("Quick search: Ctrl+K jumps to a lead", async () => {
  await page.keyboard.press("Control+k");
  await page.getByLabel("Search pages and leads").fill("zeta");
  await page.getByRole("option", { name: /Zeta Foods/ }).waitFor();   // the lead list loads after the palette opens
  await page.keyboard.press("Enter");
  await page.locator("#drawer-title", { hasText: "Zeta Foods" }).waitFor();
  await page.keyboard.press("Escape");
});
await step("Data sources: providers listed; a comparison runs on the same leads without changing them", async () => {
  await nav("Data sources");
  await page.getByRole("heading", { name: "Data sources" }).waitFor();
  const before = await (await fetch(`${API}/api/leads`)).json();
  const boxes = page.locator(".card", { hasText: "Compare" }).locator("label");
  for (const label of await boxes.all()) {
    const box = label.locator("input[type=checkbox]");
    if (!(await box.count())) continue;
    const isWebsite = (await label.innerText()).toLowerCase().includes("website");
    if ((await box.isChecked()) !== isWebsite) await box.click();
  }
  await page.locator("#sample-size").fill("3");
  await page.getByRole("button", { name: "Run comparison" }).click();
  await expectText("Results for", 90000);
  const after = await (await fetch(`${API}/api/leads`)).json();
  if (JSON.stringify(before.map((l) => l.email)) !== JSON.stringify(after.map((l) => l.email))) throw new Error("comparison changed leads");
  await shot("16-data-sources");
});

// ---------- Insights ----------
await step("Insights: totals, rates, sources, sectors and top leads render", async () => {
  await nav("Insights");
  await expectText("Reply rate");
  await expectText("By lead source");
  await expectText("By sector");
  await expectText("Top leads right now");
  await page.locator("td", { hasText: "Acme Retail" }).first().waitFor();
  await shot("11-insights");
});

// ---------- Dashboard (with data) ----------
await step("Dashboard: counts, funnel and reminders reflect the data", async () => {
  await nav("Dashboard");
  await expectText("Total leads");
  await expectText("Funnel");
});

// ---------- Public pages ----------
await step("Public: demo page renders, escapes content, offers claim button; opens event recorded", async () => {
  const leads = await (await fetch(`${API}/api/leads`)).json();
  const acme = leads.find((l) => l.company === "Acme Retail");
  const dp = await ctx.newPage();
  await dp.goto(`${API}/demo/${acme.demo_token}`);
  await dp.getByText("A sample screen for Acme Retail").waitFor();
  await dp.getByText("Your first role is free for 30 days").waitFor();
  const href = await dp.getByRole("link", { name: "Use this draft for my role" }).getAttribute("href");
  if (!href.includes("ref=") || !href.includes("utm_campaign=free_first_role")) throw new Error(`bad claim link ${href}`);
  await dp.screenshot({ path: `${SHOTS}/09-demo-page.png`, fullPage: true });
  await dp.close();
});
await step("Public: the signed unsubscribe link from the email suppresses the lead; the unsigned form does not work", async () => {
  const leads = await (await fetch(`${API}/api/leads`)).json();
  const beta = leads.find((l) => l.company === "Beta Logistics");
  const mail = (await (await fetch(`${API}/api/outbox`)).json()).find((m) => m.lead_id === beta.id);
  const link = ((mail && mail.body.match(/\/u\/[\w.-]+/)) || [])[0];
  if (!link || !link.startsWith(`/u/${beta.id}.`)) throw new Error(`no signed unsubscribe link in the email: ${link}`);
  if ((await fetch(`${API}/u/${beta.id}`)).status !== 404) throw new Error("the unsigned link still works");
  if ((await fetch(`${API}${link.slice(0, -2)}xx`)).status !== 404) throw new Error("a tampered link works");
  const up = await ctx.newPage();
  await up.goto(`${API}${link}`);
  const before = await (await fetch(`${API}/api/leads/${beta.id}`)).json();
  if (before.stage === "unsubscribed") throw new Error("opening the link alone unsubscribed the lead");
  await up.getByRole("button", { name: "Yes, unsubscribe me" }).click();
  await up.getByText("You are unsubscribed").waitFor();
  await up.close();
  const after = await (await fetch(`${API}/api/leads/${beta.id}`)).json();
  if (after.stage !== "unsubscribed") throw new Error(`stage is ${after.stage}`);
});
await step("Public: unsubscribed lead cannot be enrolled again", async () => {
  const leads = await (await fetch(`${API}/api/leads`)).json();
  const beta = leads.find((l) => l.company === "Beta Logistics");
  const r = await fetch(`${API}/api/leads/${beta.id}/enroll`, { method: "POST" });
  if (r.status !== 400) throw new Error(`status ${r.status}`);
});
await step("Public: bad demo token gives 404", async () => {
  const r = await fetch(`${API}/demo/not-a-token`);
  if (r.status !== 404) throw new Error(`status ${r.status}`);
});

// ---------- Enrichment (API, fake network not available: expects graceful result) ----------
await step("Enrichment: run on leads without reachable sites completes without error", async () => {
  await nav("Leads");
  const btn = page.getByRole("button", { name: /Fill gaps from company websites/ });
  if (await btn.isEnabled()) {
    await btn.click();
    await expectText("Checked", 60000);
  }
  await text("Download list for Apollo").waitFor();
});
await step("Apollo export downloads a CSV", async () => {
  const r = await fetch(`${API}/api/enrich/apollo-export`);
  const t = await r.text();
  if (!t.startsWith("company,contact_name,title,domain,city,lead_id")) throw new Error("bad header");
});

// ---------- Integrations hub (no real provider is called: booking links need no network) ----------
await step("Integrations: every channel tab opens and Resend is recommended", async () => {
  await nav("Integrations");
  for (const tab of ["inbound_email", "email_events", "whatsapp", "sms", "calls", "calendar", "custom", "email_send"]) {
    await page.getByTestId(`tab-${tab}`).click();     // test ids: the visible name gains "connected" once set up
  }
  await page.getByTestId("prov-email_send-resend").getByText("Recommended").waitFor();
});
await step("Integrations: booking-link wizard saves, tests and becomes active", async () => {
  await page.getByTestId("tab-calendar").click();
  await page.getByTestId("prov-calendar-booking_link").getByRole("button", { name: "Set up" }).click();
  await page.getByRole("button", { name: "Next" }).click();
  await page.getByLabel("Booking link *").fill("https://bookings.example.in/priya?name={name}&email={email}");
  await page.getByRole("button", { name: "Save and continue" }).click();
  await page.getByTestId("run-test").click();
  await page.getByTestId("test-result").getByText("Works").waitFor();
  await shot("11-integrations-wizard");
  await page.keyboard.press("Escape");
  await page.getByTestId("prov-calendar-booking_link").getByText("Active").waitFor();
});
await step("Integrations: custom connector preview escapes JSON and hides secret headers", async () => {
  await page.getByTestId("tab-sms").click();
  await page.getByTestId("prov-sms-custom_http").getByRole("button", { name: "Set up" }).click();
  await page.getByRole("button", { name: "2. Your details" }).click();
  await page.getByLabel("Body template").fill('{"to": "{{phone}}", "message": "{{text}}"}');
  await page.getByRole("button", { name: "+ Add header" }).click();
  await page.getByLabel("Header 1 name").fill("Authorization");
  await page.getByLabel("Header 1 value").fill("Bearer abc");
  await page.getByRole("checkbox", { name: "secret" }).check();
  const preview = await page.getByTestId("custom-preview").textContent();
  if (!preview.includes('\\"test\\"') || !preview.includes("Authorization: ••••")) throw new Error(preview);
  await page.keyboard.press("Escape");
});
await step("Meetings: lead panel gives a prefilled booking link; Meetings page opens", async () => {
  await nav("Leads");
  await page.locator("tr.click").first().click();
  await page.getByTestId("lead-meetings").getByRole("button", { name: /Book a meeting/ }).click();
  await page.getByRole("button", { name: "Get booking link" }).click();
  await page.getByTestId("lead-meetings").getByText("Send this link to the lead").waitFor();
  await page.keyboard.press("Escape");
  await nav("Meetings");
  await expectText("Upcoming demos and calls");
});
await step("Meetings: a booking link with a fixed time books a meeting and keeps the .ics invite as practice", async () => {
  await nav("Leads");
  await page.locator("tr.click", { hasText: "Acme Retail" }).click();
  const box = page.getByTestId("lead-meetings");
  await box.getByRole("button", { name: /Book a meeting/ }).click();
  await box.getByRole("checkbox", { name: /fixed time/ }).check();
  await box.getByText("We email the lead a calendar invite").waitFor();
  await box.getByRole("button", { name: "Book meeting" }).click();
  await box.getByText("invite kept as practice").waitFor();
  const rows = await (await fetch(`${API}/api/meetings`)).json();
  if (!rows.some((m) => m.provider === "booking_link" && m.invite === "dry_run")) throw new Error("no practice invite recorded");
  await page.keyboard.press("Escape");
});

await step("Activity: log a call with a follow-up; it shows in the full activity with its time", async () => {
  await nav("Leads");
  await page.locator("tr.click", { hasText: "Beta Logistics" }).click();
  await page.getByRole("button", { name: "Log a call / meeting / note" }).click();
  await page.getByLabel("Outcome").selectOption("call_back");
  await page.getByLabel("Notes (what was said, next steps)").fill("Asked to call back on Thursday about 5 roles");
  await page.getByLabel("Follow up on (IST, optional)").fill("2030-01-03T11:00");
  await page.getByLabel("Follow-up task").fill("Call Sam back about 5 roles");
  await page.locator("form.inline-form", { hasText: "What happened?" }).getByRole("button", { name: "Save", exact: true }).click();
  await expectText("a follow-up task was created");
  await page.locator(".trail-item", { hasText: "Asked to call back on Thursday" }).first().waitFor();
  await page.locator(".task-list li", { hasText: "Follow up" }).first().waitFor();
  const filters = page.getByRole("tablist", { name: "Filter activity" });
  await filters.getByRole("tab", { name: "Calls" }).click();
  await page.locator(".trail-item", { hasText: "Asked to call back on Thursday" }).first().waitFor();
  await filters.getByRole("tab", { name: "All", exact: true }).click();
  await page.keyboard.press("Escape");
});
await step("Tasks: the follow-up is listed with its owner time; it can be completed", async () => {
  await nav("Tasks & follow-ups");
  await expectText("Everything that needs doing");
  const row = page.locator(".task-list li", { hasText: "Call Sam back about 5 roles" }).first();
  await row.waitFor();
  await row.getByRole("button", { name: "Done" }).click();
  await expectText("Marked done");
  await page.locator(".card", { hasText: "Done recently" }).getByText("Call Sam back about 5 roles").waitFor();
});
await step("Reminders bell opens and lists reminders", async () => {
  await page.getByRole("button", { name: /Reminders:/ }).click();
  await page.getByRole("dialog", { name: "Reminders" }).waitFor();
  await page.getByRole("button", { name: /Reminders:/ }).click();
});

await step("LinkedIn: queue page explains the rules and saves settings", async () => {
  await nav("LinkedIn outreach");
  await liTab("Agent & accounts");
  await expectText("LinkedIn does not allow automation");
  await liTab("Settings & tools");
  await page.getByText("LinkedIn settings").click();
  await page.getByLabel("Case-study link (step 4 is skipped until a real link is set)").fill("https://bookmyinterview.ai/case-study");
  await page.locator("details", { hasText: "LinkedIn settings" }).getByRole("button", { name: "Save" }).click();
  await expectText("Settings saved");
});

// ---------- Smart import wizard and data hygiene ----------
const WIZARD_CSV = `First Name,Last Name,Title,Company,Company Name for Emails,Email,Mobile Phone,Person Linkedin Url,Website,# Employees,City,Country
NEHA,JOSHI,head - human resources,KAPPA TEXTILES PVT. LTD.,Kappa Textiles,Neha.Joshi@kappa.invalid,90000 70007,,www.kappa.invalid,51-200,Bangalore,India
Arun,Mehta,Founder & CEO,Lambda Foods,Lambda Foods,arun@lambda.invalid,+91 90000 80008,,lambda.invalid,"1,000+",Gurgaon,IN
Sam,Rao,HR Head,Beta Logistics,Beta Logistics,sam@beta.invalid,,,beta.invalid,51-200,Pune,IN
,,,,,not-an-email,,,,,,`;
await step("Import wizard: reads an Apollo export, dry-runs, imports and undoes", async () => {
  await closeDrawer();
  await nav("Leads");
  await page.getByRole("button", { name: "Import leads" }).click();
  await page.locator("#import-paste").fill(WIZARD_CSV);
  await page.getByRole("button", { name: "Next: check the columns" }).click();
  await expectText("Apollo export");
  await page.getByLabel("Field for # Employees").waitFor();
  await page.getByRole("button", { name: "Next: dry run" }).click();
  await expectText("Nothing has been saved yet");
  await expectText("Row 5:");
  await expectText("sam@beta.invalid is on the do-not-contact list");   // Beta unsubscribed earlier in this run
  await shot("11-import-dry-run");
  await page.getByRole("button", { name: /^Import: 2 new/ }).click();
  await expectText("Done. Added 2 new lead(s)");
  await page.getByRole("button", { name: "Undo this import" }).click();
  await expectText("Undone: removed 2 lead(s)");
  await page.getByRole("button", { name: "Close", exact: true }).last().click();
  if (await page.locator("tr.click", { hasText: "Kappa Textiles" }).count()) throw new Error("undo left the lead");
});
await step("Data hygiene: duplicate leads are shown side by side and merged", async () => {
  await fetch(`${API}/api/leads/import`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source: "manual", csv: "company,contact_name,email,phone\nACME RETAIL PVT LTD,Ravi Kumar,ravi.k@acme.invalid,98765 43210\n" }) });
  await nav("Data hygiene");
  await expectText("Overview of");
  await expectText("same phone");
  await shot("12-hygiene");
  await page.locator(".section-block", { hasText: "same phone" }).getByRole("button", { name: /Keep this one/ }).first().click();
  await answerDialog();
  await expectText("Merged 1 lead(s) into");
});

// ---------- Campaigns (no live sources: an unreachable careers page plus pasted careers-page HTML) ----------
const CAMPAIGN_JOB = (title, posted) => ({ "@context": "https://schema.org", "@type": "JobPosting", title, datePosted: posted,
  hiringOrganization: { "@type": "Organization", name: "Zeta Support Services", sameAs: "https://zeta-support.invalid" },
  jobLocation: { address: { addressLocality: "Pune" } } });
const CAMPAIGN_HTML = `<script type="application/ld+json">${JSON.stringify([CAMPAIGN_JOB("Customer Support Executive", "2026-09-01"), CAMPAIGN_JOB("Telecaller", "2026-09-10")])}</script>`;

await step("Campaigns: create a campaign with the 4-step wizard", async () => {
  await nav("Campaigns");
  await expectText("No campaigns yet");
  await page.getByRole("button", { name: "New campaign" }).click();
  await page.getByLabel("Campaign name").fill("Pune support hiring");
  await page.getByRole("button", { name: "+ BPO" }).click();
  await page.getByRole("button", { name: "+ Pune" }).click();
  await page.getByRole("button", { name: "+ customer support" }).click();
  await page.getByRole("button", { name: "51-200" }).click();
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await page.getByLabel("Careers page links (one per line)").fill("https://careers.unreachable.invalid/jobs");
  await page.getByText("Site blocks automatic reading?").click();
  await page.getByLabel("Page link (optional)").fill("https://zeta-support.invalid/careers");
  await page.getByLabel("Page HTML").fill(CAMPAIGN_HTML);
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expectText("Start the email sequence automatically for qualifying leads");
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expectText("2 careers page(s)");
});
await step("Campaigns: preview lists what would be added, shows the careers-page error and saves nothing", async () => {
  await page.getByRole("button", { name: "Run now (preview)" }).click();
  const report = page.getByTestId("run-report").filter({ hasText: "Preview: nothing was saved" });
  await report.waitFor({ timeout: 30000 });
  await report.getByText("careers.unreachable.invalid").waitFor();
  await page.getByTestId("preview-table").getByText("Zeta Support Services").waitFor();
  await page.getByTestId("preview-table").getByText("Would add").waitFor();
  const leads = await (await fetch(`${API}/api/leads`)).json();
  if (leads.some((l) => l.company === "Zeta Support Services")) throw new Error("the preview saved a lead");
  await shot("12-campaign-preview");
});
await step("Campaigns: real run gives a report with numbers and a per-source error; the caller is told", async () => {
  await page.getByRole("button", { name: "Run for real" }).click();
  const report = page.getByTestId("run-report").filter({ hasText: "Run report" });
  await report.waitFor({ timeout: 90000 });
  await report.getByText("New leads").waitFor();
  await report.getByText("Careers page:").waitFor();
  const leads = await (await fetch(`${API}/api/leads`)).json();
  const zeta = leads.find((l) => l.company === "Zeta Support Services");
  if (!zeta || !zeta.campaign_id || zeta.open_roles !== 2) throw new Error("campaign lead missing, untagged or without job signals");
  if (zeta.sequence) throw new Error("a lead was enrolled although automatic enrolment was off");
  const notes = await (await fetch(`${API}/api/notifications`)).json();
  if (!notes.some((n) => n.title.startsWith("Campaign Pune support hiring: 1 new leads"))) throw new Error("caller not notified");
});
await step("Campaigns: open the campaign's leads, the lead panel shows where it came from; fits a phone screen", async () => {
  await page.getByRole("button", { name: "Open these leads" }).click();
  await page.getByTestId("campaign-leads").getByText("Zeta Support Services").click();
  await page.locator(".drawer").getByText("Added by campaign Pune support hiring").first().waitFor();
  await page.keyboard.press("Escape");
  await page.locator(".drawer").waitFor({ state: "detached" });
  await page.getByText("Runs", { exact: true }).waitFor();
  await shot("13-campaign-detail");
  await page.setViewportSize({ width: 390, height: 800 });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  await page.setViewportSize({ width: 1280, height: 900 });
  if (overflow) throw new Error("campaign page scrolls sideways on a phone");
});

await step("Privacy: the owner downloads a lead's data and erases it; it cannot be imported again", async () => {
  await fetch(`${API}/api/leads/import`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ csv: "company,contact_name,email\nOmega Erase Co,Om Prakash,om@omega-erase.invalid\n", source: "e2e" }) });
  await nav("Leads");
  await page.getByLabel("Search leads").fill("Omega Erase");
  await page.locator("tr.click", { hasText: "Omega Erase Co" }).click();
  const privacy = page.getByTestId("privacy-actions");
  const [download] = await Promise.all([page.waitForEvent("download"), privacy.getByRole("button", { name: "Download their data" }).click()]);
  if (!download.suggestedFilename().endsWith(".json")) throw new Error(download.suggestedFilename());
  await privacy.getByRole("button", { name: "Erase their data…" }).click();
  await privacy.getByRole("button", { name: "Yes, erase everything" }).click();
  await page.locator(".drawer").waitFor({ state: "detached" });
  const again = await (await fetch(`${API}/api/leads/import`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ csv: "company,email\nOmega Again,om@omega-erase.invalid\n", source: "e2e" }) })).json();
  if (again.created !== 0 || again.suppressed !== 1) throw new Error(JSON.stringify(again));
  await page.getByLabel("Search leads").fill("");
});

// ---------- Downloads and backup ----------
const downloadFrom = async (locator) => {
  const [download] = await Promise.all([page.waitForEvent("download", { timeout: 15000 }), locator.click()]);
  return { name: download.suggestedFilename(), path: await download.path(), text: fs.readFileSync(await download.path(), "utf-8") };
};
await step("Downloads: Leads 'Download all' saves a CSV with the header row", async () => {
  await nav("Leads");
  const file = await downloadFrom(page.getByTestId("download-leads-all"));
  if (!/^outreach-leads-\d{4}-\d{2}-\d{2}\.csv$/.test(file.name)) throw new Error(`file name ${file.name}`);
  if (!file.text.startsWith("﻿id,company,contact_name,title,")) throw new Error(`header: ${file.text.slice(0, 80)}`);
  for (const column of ["score", "band", "consent", "consent_at_ist", "sequence_status", "owner", "linkedin_status"]) {
    if (!file.text.split("\r\n")[0].split(",").includes(column)) throw new Error(`no ${column} column`);
  }
  if (!file.text.includes("Acme Retail")) throw new Error("Acme Retail is not in the file");
});
await step("Downloads: Outbox 'Replies' saves the replies received", async () => {
  await nav("Outbox");
  const file = await downloadFrom(page.getByTestId("download-inbox"));
  if (!/^outreach-inbox-\d{4}-\d{2}-\d{2}\.csv$/.test(file.name)) throw new Error(`file name ${file.name}`);
  if (!file.text.startsWith("﻿received_at_ist,received_at,channel,from,subject,text,label,matched,lead_id")) throw new Error(`header: ${file.text.slice(0, 80)}`);
  const inbox = await (await fetch(`${API}/api/inbox`)).json();
  if (inbox.length === 0 && file.text.trim().split("\r\n").length !== 1) throw new Error("rows in the file but no replies captured");
  for (const m of inbox) if (m.from && !file.text.includes(m.from)) throw new Error(`reply from ${m.from} missing`);
});
await step("Backup: Setup downloads a backup and the restore dry run shows the counts", async () => {
  await nav("Setup");
  await page.getByRole("tab", { name: "Backup & restore" }).click();
  const file = await downloadFrom(page.getByTestId("download-backup"));
  if (!/^outreach-backup-\d{4}-\d{2}-\d{2}-\d{4}-IST\.json\.gz$/.test(file.name)) throw new Error(`file name ${file.name}`);
  await page.locator("#restore-file").setInputFiles(file.path);
  await page.getByRole("button", { name: "Check this file" }).click();
  const plan = page.getByTestId("restore-plan");
  await plan.getByText("Nothing has been changed yet").waitFor();
  await plan.getByText("Real sending (email, WhatsApp and SMS) is OFF after a restore").waitFor();
  const leads = await (await fetch(`${API}/api/leads`)).json();
  const cells = await page.getByTestId("restore-summary").locator("tr", { hasText: "Leads" }).first().locator("td").allInnerTexts();
  if (cells[1] !== String(leads.length) || cells[2] !== String(leads.length)) throw new Error(`leads row ${cells.join(" | ")}, expected ${leads.length}`);
  await shot("14-backup-dry-run");
  await plan.getByRole("button", { name: "Cancel" }).click();
  const after = await (await fetch(`${API}/api/leads`)).json();
  if (after.length !== leads.length) throw new Error("the dry run changed the data");
});

// ---------- Flow builder ----------
const centre = async (loc) => { const b = await loc.boundingBox(); return [b.x + b.width / 2, b.y + b.height / 2]; };
const dragTo = async (from, to) => {
  const [x1, y1] = await centre(from);
  const [x2, y2] = await centre(to);
  await page.mouse.move(x1, y1);
  await page.mouse.down();
  await page.mouse.move((x1 + x2) / 2, (y1 + y2) / 2, { steps: 5 });
  await page.mouse.move(x2, y2, { steps: 5 });
  await page.mouse.up();
};
const flowNode = (prefix) => page.locator(`g.fb-node[aria-label^="${prefix}"]`).first();
const nodeId = (prefix) => flowNode(prefix).getAttribute("data-node-id");
let builtFlowUrl = "";
await step("Flow builder: a flow from the Cold email template shows its 12 steps on the canvas", async () => {
  await nav("Flow builder");
  await expectText("No flows yet");
  await page.getByRole("button", { name: "New from template" }).click();
  await page.getByRole("button", { name: "Use template: Cold email 6-step" }).click();
  await page.getByTestId("flow-canvas").waitFor();
  await page.locator("g.fb-node").nth(11).waitFor();
  const [nodes, edges] = [await page.locator("g.fb-node").count(), await page.locator("g.fb-edge").count()];
  if (nodes !== 12 || edges !== 11) throw new Error(`expected 12 steps and 11 connections, got ${nodes} / ${edges}`);
  await shot("14-flow-template");
});
await step("Flow builder: add an If step and connect its yes / no outputs by dragging", async () => {
  await page.getByRole("button", { name: "← All flows" }).click();
  await page.getByRole("button", { name: "Blank flow" }).click();
  await page.getByTestId("flow-canvas").waitFor();
  for (const name of ["If / else", "Send email", "Stop"]) { await page.getByTestId("add-step").click(); await page.getByRole("option", { name: `Add step: ${name}` }).click(); }
  await page.getByRole("button", { name: "Auto-layout" }).click();
  const trigger = await nodeId("Added by hand");
  await dragTo(page.locator(`[data-port="${trigger}:next"] .fb-port-hit`), flowNode("If / else").locator(".fb-card"));
  await page.locator("g.fb-edge").first().waitFor();
  await page.getByRole("button", { name: "Auto-layout" }).click();
  const check = await nodeId("If / else");
  await dragTo(page.locator(`[data-port="${check}:yes"] .fb-port-hit`), flowNode("Send email").locator(".fb-card"));
  await dragTo(page.locator(`[data-port="${check}:no"] .fb-port-hit`), flowNode("Stop").locator(".fb-card"));
  if ((await page.locator("g.fb-edge").count()) !== 3) throw new Error("expected 3 connections");
  await page.locator("text.fb-edge-label", { hasText: "yes" }).waitFor();
  await page.locator("text.fb-edge-label", { hasText: "no" }).waitFor();
  await page.keyboard.press("Control+z");
  if ((await page.locator("g.fb-edge").count()) !== 2) throw new Error("undo did not remove the last connection");
  await page.keyboard.press("Control+y");
  if ((await page.locator("g.fb-edge").count()) !== 3) throw new Error("redo did not restore it");
});
await step("Flow builder: the email step shows its problem in red until the subject is written", async () => {
  await page.getByRole("button", { name: "Validate" }).click();
  await flowNode("Send email").getByTestId("node-error").filter({ hasText: "Write the email subject" }).waitFor();
  await flowNode("Send email").locator(".fb-card").click();
  const panel = page.getByTestId("node-config");
  await panel.getByLabel("Subject", { exact: true }).fill("{{role}} hiring at {{company}}");
  await panel.getByLabel("Email text").click();
  await panel.getByRole("button", { name: "{{first_name}}" }).click();
  if (!(await panel.getByLabel("Email text").inputValue()).includes("{{first_name}}")) throw new Error("placeholder chip not inserted");
  await page.getByRole("button", { name: "Validate" }).click();
  await expectText("No problems found");
  if (await page.getByTestId("node-error").count()) throw new Error("a step still shows a problem");
});
await step("Flow builder: save, then simulate on a lead: the path is highlighted and listed", async () => {
  await page.locator("#fb-name").fill("E2E branch flow");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expectText("Saved.");
  await page.getByRole("button", { name: "Run simulation" }).click();
  await page.getByTestId("sim-result").waitFor();
  const lit = await page.locator("g.fb-node.on").count();
  const litEdges = await page.locator("g.fb-edge.on").count();
  if (lit < 3 || litEdges < 2) throw new Error(`path not highlighted (${lit} steps, ${litEdges} connections)`);
  await page.getByTestId("sim-result").getByText("If / else").waitFor();
  await shot("15-flow-simulate");
});
await step("Flow builder: activate; the flow list shows it as Active", async () => {
  await page.getByRole("button", { name: "Activate" }).click();
  await page.getByTestId("flow-status").filter({ hasText: "Active" }).waitFor();
  builtFlowUrl = page.url();
  await page.getByRole("button", { name: "← All flows" }).click();
  await page.locator("tr", { hasText: "E2E branch flow" }).getByText("Active").waitFor();
});
await step("Flow builder: on a phone the flow opens as a list of steps with no sideways scroll", async () => {
  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto(builtFlowUrl);
  const list = page.getByTestId("flow-list-view");
  await list.waitFor();
  await list.getByText("If yes:").waitFor();
  await list.getByText("If no:").waitFor();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  await shot("16-flow-phone-list");
  await page.setViewportSize({ width: 1280, height: 900 });
  if (overflow) throw new Error("flow editor scrolls sideways on a phone");
});

// ---------- Chrome assistant (pairing token) ----------
await step("LinkedIn: Chrome assistant pairing token is created, shown once, works only for the extension routes, and can be revoked", async () => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await nav("LinkedIn outreach");
  await liTab("Settings & tools");
  const card = page.getByTestId("chrome-assistant");
  await card.locator("summary").click();
  await card.getByLabel("Name for this computer").fill("E2E laptop");
  await card.getByRole("button", { name: "Create pairing token" }).click();
  await expectText("shown only once");
  const token = (await page.getByTestId("pair-token").innerText()).trim();
  if (!/^mcnx_[A-Za-z0-9_-]{40,}$/.test(token)) throw new Error(`unexpected token shape: ${token.slice(0, 8)}…`);
  const headers = { Authorization: `Bearer ${token}` };
  const queue = await page.request.get(`${API}/api/extension/queue`, { headers });
  if (queue.status() !== 200) throw new Error(`extension queue answered ${queue.status()}`);
  const other = await page.request.get(`${API}/api/leads`, { headers });
  if (other.status() !== 403) throw new Error(`/api/leads with a pairing token answered ${other.status()}, expected 403`);
  await shot("17-chrome-assistant-token");
  await card.getByRole("button", { name: "I have copied it" }).click();
  await nav("Leads");
  await nav("LinkedIn outreach");
  await liTab("Settings & tools");
  await page.getByTestId("chrome-assistant").locator("summary").click();
  if (await page.getByText(token).count()) throw new Error("the raw token is still on the page after it was dismissed");
  const row = page.getByTestId("pair-row").filter({ hasText: "E2E laptop" });
  await row.waitFor();
  await row.getByRole("button", { name: "Revoke" }).click();
  await expectText("Pairing token revoked");
  await page.getByTestId("pair-row").filter({ hasText: "E2E laptop" }).waitFor({ state: "detached" });
  const after = await page.request.get(`${API}/api/extension/queue`, { headers });
  if (after.status() !== 401) throw new Error(`revoked token answered ${after.status()}, expected 401`);
});
// ---------- Find & enrich leads, LinkedIn tool bridge ----------
await step("Find & enrich: the card opens a drawer; Apollo not connected is explained; Preview lists what it would do and saves nothing", async () => {
  // Earlier steps saved a fake Apollo key; switch it off so no step here can reach the real Apollo API.
  const off = await page.request.put(`${API}/api/integrations/apollo`, { data: { mode: "manual", values: {} } });
  if (!off.ok()) throw new Error("could not switch Apollo off");
  await nav("Dashboard");
  await page.getByTestId("find-card").waitFor();
  const before = await (await page.request.get(`${API}/api/leads`)).json();
  await page.getByTestId("find-card").getByRole("button", { name: "Find & enrich leads" }).click();
  const drawer = page.getByTestId("find-drawer");
  await drawer.waitFor();
  await drawer.getByText("Fill in contacts for leads I already have").waitFor();
  await drawer.getByText("Apollo: not connected").waitFor();
  await drawer.getByRole("button", { name: "Connect Apollo, Hunter or Google Places in Integrations" }).waitFor();
  for (const city of ["Noida", "Delhi", "Gurgaon"]) await drawer.getByRole("button", { name: `Remove ${city}` }).waitFor();
  await drawer.getByTestId("find-estimate").waitFor();
  await drawer.getByText("Fill in contacts for leads I already have").click();
  await drawer.getByRole("button", { name: "Preview" }).click();
  await drawer.getByTestId("find-preview-table").waitFor({ timeout: 30000 });
  await drawer.getByText("Read the website's public contact pages").first().waitFor();
  await drawer.getByText("Preview: nothing was saved").waitFor();
  const after = await (await page.request.get(`${API}/api/leads`)).json();
  if (JSON.stringify(before) !== JSON.stringify(after)) throw new Error("the preview changed leads");
  await shot("17-find-preview");
});
await step("Find & enrich: Run now shows progress, then a report that names what could not be read", async () => {
  const drawer = page.getByTestId("find-drawer");
  await drawer.getByRole("button", { name: "Run now" }).click();
  await drawer.getByTestId("find-report").waitFor({ timeout: 60000 });
  await drawer.getByRole("heading", { name: "Done" }).waitFor();
  await drawer.getByText("What didn't work").waitFor();
  const report = await drawer.getByTestId("find-report").innerText();
  if (!/could not be read|no website/i.test(report)) throw new Error(`report does not explain the problem: ${report.slice(0, 200)}`);
  await shot("18-find-report");
  await closeDrawer();
});
await step("LinkedIn tool: the export button downloads a CSV with the tool's columns; the results import reports what it could not match", async () => {
  await nav("LinkedIn outreach");
  await liTab("Settings & tools");
  await page.getByText("Automating LinkedIn is against LinkedIn's terms").waitFor();
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Export list for a LinkedIn tool" }).click()]);
  if (!/^outreach-linkedin-tool-\d{4}-\d{2}-\d{2}\.csv$/.test(download.suggestedFilename())) throw new Error(`file name: ${download.suggestedFilename()}`);
  const body = fs.readFileSync(await download.path(), "utf8");
  if (!body.replace(/^\uFEFF/, "").startsWith("firstName,lastName,companyName,title,profileUrl,message1,message2,message3,message4,message5")) throw new Error(`columns: ${body.slice(0, 120)}`);
  await page.getByTestId("import-linkedin-results").waitFor();
  await page.locator("input[type=file]").setInputFiles({ name: "results.csv", mimeType: "text/csv", buffer: Buffer.from("profileUrl,status\nhttps://www.linkedin.com/in/nobody-here,connected\n") });
  await page.getByTestId("linkedin-results").getByText("1 not found in your leads").waitFor();
});

// ---------- Lead sources (no real provider is called: the free test needs no network without a company identifier) ----------
await step("Sources: Integrations > Lead data lists the new source cards with steps and terms", async () => {
  await nav("Integrations");
  await page.getByTestId("tab-lead_data").click();
  for (const id of ["greenhouse", "lever", "adzuna", "brave_search", "osm_overpass", "snov", "kickbox", "bouncer"]) {
    await page.getByTestId(`prov-lead_data-${id}`).waitFor({ timeout: 5000 });
  }
  const cards = await page.locator('[data-testid^="prov-lead_data-"]').count();
  if (cards < 30) throw new Error(`only ${cards} Lead data cards`);
  await page.getByTestId("prov-lead_data-greenhouse").getByRole("button", { name: /Set up|Open/ }).click();
  await page.getByTestId("source-terms").waitFor();
  await expectText("Find a company's identifier");
  await shot("12-sources-lead-data");
  await page.keyboard.press("Escape");
});
await step("Sources: a free source saves and its Test connection shows a result", async () => {
  await page.getByTestId("prov-lead_data-greenhouse").getByRole("button", { name: /Set up|Open/ }).click();
  await page.getByRole("button", { name: "Next" }).click();
  await page.getByRole("button", { name: "Save and continue" }).click();
  await page.getByTestId("run-test").click();
  await page.getByTestId("test-result").getByText("needs no key").waitFor({ timeout: 8000 });
  await page.keyboard.press("Escape");
  await page.getByTestId("prov-lead_data-kickbox").getByRole("button", { name: "Set up" }).click();
  await page.getByRole("button", { name: "Next" }).click();
  await page.getByLabel("API key *").fill("live_not_a_real_key");
  await page.getByRole("button", { name: "Save and continue" }).click();
  await page.getByTestId("run-test").click();
  await page.getByTestId("test-result").getByText("no free key check").waitFor({ timeout: 8000 });
  await page.keyboard.press("Escape");
});

// ---------- Lead capture (inbound) ----------
let captureUrl = "", captureToken = "";
await step("Lead capture: add a webhook connector and see its address and token", async () => {
  await nav("Integrations");
  await page.getByTestId("tab-capture").click();
  await page.getByTestId("add-capture").click();
  await page.getByLabel("Connector name").fill("E2E webhook");
  await page.getByRole("button", { name: "Create connector" }).click();
  await page.getByTestId("capture-url").waitFor();
  captureUrl = (await page.getByTestId("capture-url").innerText()).trim();
  captureToken = (await page.getByTestId("capture-token").innerText()).trim();
  if (!/\/api\/inbound\/cap[0-9a-f]{8}$/.test(captureUrl)) throw new Error(`odd address ${captureUrl}`);
  if (captureToken.length < 20) throw new Error("no token shown");
  await expectText("Example (curl)");
  await shot("11-lead-capture-connector");
});
await step("Lead capture: Send a test lead reports that it arrived", async () => {
  await page.getByTestId("send-test").click();
  await page.getByTestId("test-result").getByText("Test lead arrived").waitFor();
});
await step("Lead capture: a lead posted to the address arrives with its campaign, and a repeat is ignored", async () => {
  const path = new URL(captureUrl).pathname;
  const post = () => page.request.post(`${API}${path}`, {
    headers: { "X-Capture-Token": captureToken },
    data: { full_name: "Inbound Ira", email: "ira@inbound-e2e.invalid", mobile: "98100 77001", company: "Inbound E2E Traders", lead_id: "E2E-1", utm_campaign: "diwali-e2e", utm_source: "facebook" },
  });
  const first = await (await post()).json();
  if (first.status !== "new") throw new Error(`expected new, got ${JSON.stringify(first)}`);
  const again = await (await post()).json();
  if (again.status !== "replay") throw new Error(`expected replay, got ${JSON.stringify(again)}`);
  const bad = await page.request.post(`${API}${path}`, { headers: { "X-Capture-Token": "nope" }, data: { email: "x@y.in" } });
  if (bad.status() !== 401) throw new Error(`a wrong token must be refused, got ${bad.status()}`);
  await page.getByRole("button", { name: "Close" }).click();
  await page.getByTestId("capture-log").getByText("New lead").first().waitFor();
});
await step("Lead capture: the lead is on Leads with its source label, campaign and Inbound badge; the source filter works", async () => {
  await nav("Leads");
  const row = page.locator("tr.click", { hasText: "Inbound E2E Traders" });
  await row.waitFor();
  await row.getByText("Webhook (Zapier / Make / n8n)").waitFor();
  await row.getByText("Inbound", { exact: true }).waitFor();
  await row.getByText("diwali-e2e").waitFor();
  await page.locator("details.multi-filter summary").click();
  await page.getByRole("group", { name: "Sources" }).getByLabel(/Webhook \(Zapier/).check();
  await page.locator("tr.click").first().waitFor();
  const filtered = await page.locator("tr.click").count();
  if (filtered !== 2) throw new Error(`expected the test lead and the real one, saw ${filtered} rows`);
  await page.getByLabel("Inbound only").check();
  if (await page.locator("tr.click").count() !== 2) throw new Error("inbound only should still show both");
  await page.getByLabel("Inbound only").uncheck();
  await page.getByRole("button", { name: "Clear sources" }).click();
  await page.locator("details.multi-filter summary").click();
  await shot("12-leads-source-filter");
});
await step("Lead capture: the lead drawer shows where it came from", async () => {
  await page.locator("tr.click", { hasText: "Inbound E2E Traders" }).click();
  const origin = page.getByTestId("lead-origin");
  await origin.getByText("Where this lead came from").waitFor();
  await origin.getByText("diwali-e2e").first().waitFor();
  await closeDrawer();
});
await step("Lead capture: Dashboard shows 'Where leads come from' with the inbound lead, not the test lead", async () => {
  await nav("Dashboard");
  const card = page.getByTestId("where-leads-come-from");
  await card.getByText("Where leads come from").waitFor();
  const row = card.locator("tr", { hasText: "Webhook (Zapier / Make / n8n)" });
  await row.waitFor();
  await row.getByText("Inbound", { exact: true }).waitFor();
  const leadsCell = (await row.locator("td").nth(1).innerText()).trim();
  if (leadsCell !== "1") throw new Error(`the test lead must not be counted, saw ${leadsCell}`);
  await shot("13-dashboard-sources");
});
await step("Lead capture: Insights labels the sources and offers cost per lead", async () => {
  await nav("Insights");
  await expectText("Stage by source");
  await expectText("Webhook (Zapier / Make / n8n)");
  await expectText("Cost per lead");
});

// ---------- Mobile layout ----------
await step("Mobile width: no horizontal page scroll on Leads", async () => {
  await page.setViewportSize({ width: 390, height: 800 });
  await nav("Leads");
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  await shot("10-mobile-leads");
  if (overflow) throw new Error("page scrolls sideways");
});
await step("Mobile width: no horizontal page scroll on Data hygiene and the import wizard", async () => {
  await nav("Data hygiene");
  await expectText("Overview of");
  let overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  if (overflow) throw new Error("Data hygiene scrolls sideways");
  await nav("Leads");
  await page.getByRole("button", { name: "Import leads" }).click();
  await page.locator("#import-paste").fill(WIZARD_CSV);
  await page.getByRole("button", { name: "Next: check the columns" }).click();
  await expectText("Apollo export");
  overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  await shot("13-mobile-import-columns");
  if (overflow) throw new Error("import wizard scrolls sideways");
});
await step("Mobile width: the menu is a compact top bar, even on a short page", async () => {
  await nav("Success stories");
  await page.setViewportSize({ width: 390, height: 1400 });
  const bar = await page.locator("header.topbar").boundingBox();
  if (!bar || bar.height > 70 || bar.y > 1) throw new Error(`top bar is ${bar && bar.height}px tall at y=${bar && bar.y}`);
  await page.locator("nav.side").waitFor({ state: "hidden", timeout: 2000 }).catch(() => { throw new Error("the full menu shows without opening it"); });
  await shot("10b-mobile-short-page");
  await page.setViewportSize({ width: 390, height: 800 });
});

await step("Mobile width: Lead capture and its panel do not scroll sideways", async () => {
  await nav("Integrations");
  await page.getByTestId("tab-capture").click();
  await page.getByTestId("add-capture").waitFor();
  let overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  if (overflow) throw new Error("Lead capture scrolls sideways");
  await page.getByRole("button", { name: "Open", exact: true }).first().click();
  await page.getByTestId("capture-panel").waitFor();
  overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  await shot("14-mobile-lead-capture");
  await page.getByRole("button", { name: "Close" }).click();
  if (overflow) throw new Error("the connector panel scrolls sideways");
});

// ---------- Links to sub-views (hash routing) ----------
await step("Links: an open lead is in the address; reload keeps the drawer, Back closes it", async () => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await nav("Leads");
  await page.locator("tr.click").first().click();
  await page.locator(".drawer").waitFor();
  if (!/#leads\?lead=/.test(page.url())) throw new Error(`address is ${page.url()}`);
  await page.reload();
  await page.locator(".drawer").waitFor();
  await page.goBack();
  await page.locator(".drawer").waitFor({ state: "detached" });
});
await step("Links: the designed-email preview has its own address and closes with Escape", async () => {
  await nav("Sequences");
  await page.getByRole("button", { name: "Preview designed email" }).first().click();
  await page.getByTestId("template-preview").waitFor();
  if (!/#templates\?preview=0/.test(page.url())) throw new Error(`address is ${page.url()}`);
  await page.reload();
  await page.getByTestId("template-preview").waitFor();
  await page.keyboard.press("Escape");
  await page.getByTestId("template-preview").waitFor({ state: "detached" });
  if (/preview=/.test(page.url())) throw new Error("closing left the preview in the address");
});

await browser.close();

const failed = results.filter(([ok]) => !ok);
for (const [ok, name] of results) console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
const unique = [...new Set(problems)];
console.log(unique.length ? `\nBrowser problems:\n${unique.join("\n")}` : "\nNo console errors or failed requests.");
process.exit(failed.length ? 1 : 0);
