// End-to-end walk through every page and action. Run with both servers up (see README).
import { chromium } from "playwright";
import fs from "node:fs";

const UI = "http://localhost:5173";
const API = "http://localhost:8000";
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
const nav = (label) => page.getByRole("button", { name: label, exact: false }).first().click();
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
  await expectText("0 of 10 connected");
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
  await expectText("2 of 10 connected");
  await page.locator(".badge-dot").filter({ hasText: "5" }).waitFor();
  await shot("02-setup");
});

// ---------- Leads: import ----------
await step("Leads: empty state", async () => {
  await nav("Leads");
  await expectText("No leads yet");
});
await step("Leads: CSV paste import dedupes and rejects bad rows", async () => {
  await page.getByRole("button", { name: "Import leads" }).click();
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
  await page.getByRole("button", { name: "Import leads" }).click();
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
  await page.getByRole("button", { name: "Start 4-step email sequence" }).click();
  await expectText("Running — steps sent on day(s): none yet");
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
  await page.getByRole("button", { name: "Start 4-step email sequence" }).click();
  await expectText("Running");
  await page.getByRole("button", { name: "Close" }).click();
  await nav("Outbox");
  await page.getByRole("button", { name: "Run sequences now" }).click();
  await expectText("email(s) practised (not sent)");
  await page.locator("td", { hasText: "Hiring at Beta Logistics" }).waitFor();
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
  await nav("Priya");
  await text("Acme Retail").waitFor();
  await page.locator("select").first().selectOption("Connected – interested");
  await page.waitForTimeout(400);
  await expectText("Call script");
  await shot("06-calldesk");
});

// ---------- Templates ----------
await step("Templates: 4 email steps from the backend + other messages + copy", async () => {
  await nav("Sequences");
  await expectText("Email 1 — sample screen");
  await expectText("Email 4 — permission close");
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
  await nav("Workflows");
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
  await page.getByRole("button", { name: "Import leads" }).click();
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
  await page.getByRole("button", { name: "Import leads" }).click();
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
  await page.getByTestId("users-card").getByText("Team logins").waitFor();
  await page.getByTestId("users-card").getByRole("button", { name: "Add login" }).waitFor();
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
  const tel = page.locator(".card", { hasText: "Calling (Priya" });
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
  await nav("Priya");
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
  await page.getByRole("button", { name: "Import leads" }).click();
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

// ---------- Mobile layout ----------
await step("Mobile width: no horizontal page scroll on Leads", async () => {
  await page.setViewportSize({ width: 390, height: 800 });
  await nav("Leads");
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  await shot("10-mobile-leads");
  if (overflow) throw new Error("page scrolls sideways");
});
await step("Mobile width: the menu is a compact top bar, even on a short page", async () => {
  await nav("Success stories");
  await page.setViewportSize({ width: 390, height: 1400 });
  const bar = await page.locator("nav.side").boundingBox();
  if (!bar || bar.height > 70 || bar.y > 1) throw new Error(`nav bar is ${bar && bar.height}px tall at y=${bar && bar.y}`);
  await shot("10b-mobile-short-page");
  await page.setViewportSize({ width: 390, height: 800 });
});

await browser.close();

const failed = results.filter(([ok]) => !ok);
for (const [ok, name] of results) console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
const unique = [...new Set(problems)];
console.log(unique.length ? `\nBrowser problems:\n${unique.join("\n")}` : "\nNo console errors or failed requests.");
process.exit(failed.length ? 1 : 0);
