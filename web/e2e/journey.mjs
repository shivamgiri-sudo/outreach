// A day in the life of the outreach team, through the real UI, at desktop and phone width.
//
//   cd web && npm install && node e2e/journey.mjs
//
// The script starts its own servers so nothing else has to be running:
//   - the API (uvicorn) on E2E_API's port with OUTREACH_ADMIN_PASSWORD=journey-pass, OUTREACH_IGNORE_BUSINESS_HOURS=1
//     and a throwaway OUTREACH_DATA_FILE (a fresh one per screen width, deleted afterwards);
//   - Vite on E2E_UI's port, started through Vite's JS API with the project's vite.config.js plus an inline override of
//     server.proxy so /api, /u and /demo go to E2E_API instead of the default 127.0.0.1:8000. vite.config.js is not changed.
// JOURNEY_PYTHON picks the Python (default: ../.venv, else python3). JOURNEY_WIDTHS=desktop or phone runs one width.
// HEADED=1 shows the browser. Screenshots go to web/journey-shots/<width>/ (git-ignored).
import { chromium } from "playwright";
import { createServer } from "vite";
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const E2E_API = process.env.E2E_API || "http://127.0.0.1:8801";
const E2E_UI = process.env.E2E_UI || "http://127.0.0.1:5201";
const PASSWORD = "journey-pass";
const CALLER = { email: "priya.caller@outreach.invalid", name: "Priya", password: "caller-pass-2026" };
const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const ROOT = path.resolve(WEB, "..");
const FIXTURES = path.join(ROOT, "tests", "qa", "fixtures", "browser-journey");
const SHOTS = process.env.SHOT_DIR || path.join(WEB, "journey-shots");
const WIDTHS = [["desktop", 1366, 900], ["phone", 390, 844]]
  .filter(([name]) => !process.env.JOURNEY_WIDTHS || process.env.JOURNEY_WIDTHS.split(",").includes(name));
const PYTHON = process.env.JOURNEY_PYTHON || [path.join(ROOT, ".venv", "bin", "python"), path.join(ROOT, ".venv", "Scripts", "python.exe")]
  .find((p) => fs.existsSync(p)) || "python3";
const fixture = (name) => path.join(FIXTURES, name);
const istDate = (days = 0) => new Date(Date.now() + (330 + days * 1440) * 60000).toISOString().slice(0, 10);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------- servers ----------
async function startApi() {
  // a leftover server on the port would answer the health check and the journey would run against its old data
  if (await fetch(`${E2E_API}/api/auth/status`).then(() => true, () => false)) throw new Error(`something already answers on ${E2E_API}; stop it first`);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "outreach-journey-"));
  const env = { ...process.env, OUTREACH_ADMIN_PASSWORD: PASSWORD, OUTREACH_DATA_FILE: path.join(dir, "store.json"),
    OUTREACH_IGNORE_BUSINESS_HOURS: "1" };
  for (const k of ["OUTREACH_AUTORUN", "OUTREACH_TEST_MODE", "OUTREACH_SECRET_KEY", "OUTREACH_BASE_URL"]) delete env[k];
  const port = new URL(E2E_API).port;
  const proc = spawn(PYTHON, ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", port, "--no-access-log"],
    { cwd: ROOT, env, stdio: ["ignore", "ignore", "pipe"] });
  let log = "";
  proc.stderr.on("data", (d) => { log += d; });
  for (let i = 0; i < 100; i++) {
    if (proc.exitCode !== null) throw new Error(`API exited: ${log.slice(-800)}`);
    try { if ((await fetch(`${E2E_API}/api/auth/status`)).ok) return { proc, dir }; } catch { /* not up yet */ }
    await wait(200);
  }
  proc.kill();
  throw new Error(`API did not start on ${E2E_API}: ${log.slice(-800)}`);
}
async function stopApi({ proc, dir }) {
  proc.kill();
  await new Promise((r) => (proc.exitCode !== null ? r() : proc.once("exit", r)));
  fs.rmSync(dir, { recursive: true, force: true });
}
async function startUi() {
  const ui = new URL(E2E_UI);
  const server = await createServer({
    root: WEB, configFile: path.join(WEB, "vite.config.js"), logLevel: "warn", clearScreen: false,
    server: { host: ui.hostname, port: Number(ui.port), strictPort: true,
      proxy: { "/api": E2E_API, "/u": E2E_API, "/demo": E2E_API } },
  });
  await server.listen();
  return server;
}

// ---------- one journey at one screen width ----------
async function journey(browser, [size, width, height]) {
  const shots = path.join(SHOTS, size);
  fs.rmSync(shots, { recursive: true, force: true });
  fs.mkdirSync(shots, { recursive: true });
  const ctx = await browser.newContext({ viewport: { width, height }, acceptDownloads: true });
  await ctx.grantPermissions(["clipboard-read", "clipboard-write"], { origin: E2E_UI });
  ctx.setDefaultTimeout(15000);
  const page = await ctx.newPage();
  const results = [];
  const problems = [];
  let allowed = [];          // HTTP refusals the current step causes on purpose: [{status, part}]
  let current = "";
  const isAllowed = (status, url) => allowed.some((a) => a.status === status && url.includes(a.part));
  page.on("pageerror", (e) => problems.push(`[${current}] page error: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const url = m.location()?.url || "";
    const status = Number((m.text().match(/status of (\d{3})/) || [])[1]);
    if (!(status && isAllowed(status, url))) problems.push(`[${current}] console: ${m.text()} ${url}`);
  });
  page.on("response", (r) => {
    if (r.status() >= 400 && !r.url().includes("favicon") && !isAllowed(r.status(), r.url())) problems.push(`[${current}] HTTP ${r.status()} ${r.url()}`);
  });
  page.on("dialog", (d) => d.accept());

  const step = async (name, fn, expectRefusals = []) => {
    current = name; allowed = expectRefusals;
    const n = String(results.length + 1).padStart(2, "0");
    try {
      const note = await fn();
      if (size === "phone") {
        const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        if (over > 1) {
          // name the deepest boxes that are wider than they should be, so a failure points at the cause
          const culprits = await page.evaluate(() => [...document.querySelectorAll("main *")]
            .filter((el) => el.clientWidth && el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflowX === "visible"
              && ![...el.children].some((c) => c.clientWidth && c.scrollWidth > c.clientWidth + 1))
            .slice(0, 3).map((el) => `${el.tagName.toLowerCase()}: "${(el.textContent || "").trim().slice(0, 60)}"`));
          throw new Error(`page scrolls sideways by ${over}px (${culprits.join("; ")})`);
        }
      }
      results.push([true, name, note || ""]);
    } catch (e) {
      results.push([false, name, e.message.split("\n")[0]]);
    }
    if (process.env.JOURNEY_VERBOSE) console.log(`[${size}] ${results.at(-1)[0] ? "PASS" : "FAIL"} ${name} ${results.at(-1)[2]}`);
    if (!results.at(-1)[0] && process.env.JOURNEY_STOP_ON_FAIL) {
      console.log((await page.locator("body").innerText().catch(() => "")).slice(0, 1500), problems);
      throw new Error("stopped at the first failing step (JOURNEY_STOP_ON_FAIL)");
    }
    await page.screenshot({ path: path.join(shots, `${n}-${name.replace(/[^a-z0-9]+/gi, "-").toLowerCase().slice(0, 60)}.png`), fullPage: true })
      .catch(() => {});
    allowed = [];
  };
  const nav = (label) => page.getByRole("button", { name: label, exact: true }).first().click();
  const see = (t, timeout = 8000) => page.getByText(t, { exact: false }).first().waitFor({ timeout });
  const token = () => page.evaluate(() => sessionStorage.getItem("outreach-token"));
  const api = async (p, opts = {}, tok) => {
    const t = tok ?? (await token());
    const r = await fetch(`${E2E_API}${p}`, { ...opts, headers: { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}) } });
    return { status: r.status, body: await r.json().catch(() => null) };
  };
  const leadByCompany = async (company) => (await api("/api/leads")).body.find((l) => l.company === company);
  const kpi = async (label) => Number(await page.locator(".kpi", { hasText: label }).first().locator("b").innerText());
  const drawer = () => page.locator(".drawer");
  const closeDrawer = async () => {
    if (await drawer().count()) { await page.keyboard.press("Escape"); await drawer().waitFor({ state: "detached" }); }
  };
  const openLead = async (company) => {
    await closeDrawer();
    await nav("Leads");
    await page.getByLabel("Search leads").fill(company);
    await page.locator("tr.click", { hasText: company }).first().locator("td[data-label=Company] button").click();
    await page.locator("#drawer-title", { hasText: company }).waitFor();
  };
  const signIn = async (password, email = "") => {
    await page.getByLabel("Password").waitFor();
    await page.getByLabel("Work email").fill(email);
    await page.getByLabel("Password").fill(password);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.getByRole("button", { name: "Sign out" }).waitFor();
  };
  const signOut = async () => {
    await page.getByRole("button", { name: "Sign out" }).click();
    await page.getByLabel("Password").waitFor();
  };
  const wizardUpload = async (file, source = "") => {
    await closeDrawer();
    await nav("Leads");
    await page.getByRole("button", { name: "Import leads" }).click();
    if (file.endsWith(".tsv")) {
      await page.locator("#import-paste").fill(fs.readFileSync(fixture(file), "utf8"));    // copied from Google Sheets
    } else {
      await page.locator("#import-file").setInputFiles(fixture(file));
      await see(`Loaded ${file}`);
    }
    if (source) await page.locator("#import-source").selectOption(source);
    await page.getByRole("button", { name: "Next: check the columns" }).click();
  };
  const logReply = async (company, text, label, stageText) => {
    await openLead(company);
    await page.locator("#reply-box").fill(text);
    await page.getByRole("button", { name: "Log reply" }).click();
    await page.getByRole("status").filter({ hasText: `Detected: ${label}` }).waitFor();
    if (stageText) await page.locator("h3", { hasText: `Stage: ${stageText}` }).waitFor();
    const lead = await leadByCompany(company);
    await closeDrawer();
    return lead;
  };

  // ======================= Morning: the owner signs in =======================
  await page.goto(E2E_UI);
  await step("Owner: a wrong password is refused, the right one opens the dashboard", async () => {
    await page.getByLabel("Password").fill("journey-pass-typo");
    await page.getByRole("button", { name: "Sign in" }).click();
    await see("Wrong password");
    await signIn(PASSWORD);
    await page.getByRole("heading", { name: "Dashboard" }).waitFor();
    await see("Import your first list of leads to get started.");
  }, [{ status: 401, part: "/api/login" }]);

  // ======================= Three lists from three tools =======================
  await step("Import 1: Apollo export (upload): recognised, dry run, left-out rows, import", async () => {
    await wizardUpload("apollo-contacts-export.csv");
    await see("This looks like: Apollo export");
    if (await page.getByLabel("Field for Email", { exact: true }).inputValue() !== "email") throw new Error("Email column not mapped");
    if (await page.getByLabel("Field for Apollo Contact Id").inputValue() !== "ignore") throw new Error("Apollo id should not be imported");
    await page.getByRole("button", { name: "Next: dry run" }).click();
    await see("Nothing has been saved yet. This is what importing would do with 133 rows.");
    const [created, dups, left] = [await kpi("new leads"), await kpi("duplicates inside this file"), await kpi("left out")];
    if (created !== 108 || dups !== 23 || left !== 2) throw new Error(`dry run said ${created} new, ${dups} dups, ${left} left out`);
    await see("Row 77: Email 'ravi@@example.invalid' is not a valid address");
    const [dl] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Download left-out rows (CSV)" }).click()]);
    const csv = fs.readFileSync(await dl.path(), "utf8");
    if (!csv.startsWith('"Row","Why it was left out"') || !csv.includes("ravi@@example.invalid")) throw new Error("left-out CSV is wrong");
    await page.getByText("Things we fixed or noticed").click();
    await see("Numbers outside India (kept): 1");
    await page.getByRole("button", { name: "Import: 108 new, 0 updated" }).click();
    await see("Done. Added 108 new lead(s)");
    await page.getByRole("button", { name: "Close", exact: true }).last().click();
    if ((await api("/api/leads")).body.length !== 108) throw new Error("lead count after Apollo import");
    return "108 new, 23 in-file duplicates, 2 left out";
  });
  await step("Import 2: Sales Navigator via Evaboot (Excel ; + BOM): fix a wrong column guess, merges and conflicts", async () => {
    await wizardUpload("evaboot-salesnav-hr-leaders.csv", "sales_navigator");
    await see("This looks like: LinkedIn Sales Navigator export");
    if (await page.getByLabel("Field for First Name").inputValue() !== "first_name") throw new Error("BOM broke the first header");
    const snUrl = page.getByLabel("Field for Sales Navigator URL");
    const guessed = await snUrl.inputValue();
    if (guessed !== "ignore") await snUrl.selectOption("ignore");          // a Sales Navigator link is not the company website
    await page.getByRole("button", { name: "Next: dry run" }).click();
    await see("what importing would do with 57 rows");
    const [created, merged] = [await kpi("new leads"), await kpi("existing leads get new details")];
    if (created !== 41 || merged !== 12) throw new Error(`dry run said ${created} new, ${merged} merged`);
    await see("Added to leads you already have (12)");
    await page.locator("p", { hasText: "(same email)" }).first().waitFor();
    await page.getByRole("button", { name: "Import: 41 new, 12 updated" }).click();
    await see("6 different value(s) are waiting for you under Data hygiene");
    await page.getByRole("button", { name: "Close", exact: true }).last().click();
    const sahyadri = await leadByCompany("Sahyadri BPO Services");
    if (!sahyadri.linkedin_url?.includes("linkedin.com/in/")) throw new Error("LinkedIn URL not added to the Apollo lead");
    return `Sales Navigator URL column was guessed as "${guessed}"; set to Don't import`;
  });
  await step("Import 3: team Google Sheet Leads tab (pasted, tab separated): job signals, shared inboxes", async () => {
    await wizardUpload("team-sheet-leads-tab.tsv", "bmi_sheet");
    await see("This looks like: Team sheet — Leads tab");
    await page.getByRole("button", { name: "Next: dry run" }).click();
    await see("what importing would do with 42 rows");
    const [created, merged] = [await kpi("new leads"), await kpi("existing leads get new details")];
    if (created !== 32 || merged !== 10) throw new Error(`dry run said ${created} new, ${merged} merged`);
    await page.getByText("Things we fixed or noticed").click();
    await see("Shared inboxes (info@, hr@…): 4");
    await page.getByRole("button", { name: "Import: 32 new, 10 updated" }).click();
    await see("Done. Added 32 new lead(s) and added details to 10 existing lead(s)");
    await page.getByRole("button", { name: "Close", exact: true }).last().click();
    await see("Showing 181 of 181");
    const chambal = await leadByCompany("Chambal Facility Services");
    if (chambal.open_roles !== 25 || chambal.phone !== "+919000010300") throw new Error("sheet row not standardised");
    return "181 leads after three imports";
  });

  // ======================= Hygiene =======================
  await step("Data hygiene: overview, duplicate group merged, a differing value kept", async () => {
    await nav("Data hygiene");
    await see("Overview of 181 leads");
    if (await kpi("groups of duplicates") !== 2) throw new Error("expected 2 duplicate groups");
    if (await kpi("numbers outside India") !== 1) throw new Error("expected 1 foreign number");
    const block = page.locator(".section-block", { hasText: "Kesari Group Holdings" });
    await block.getByText("same phone").waitFor();
    await block.locator(".lead-card").filter({ has: page.locator("b", { hasText: /^Kesari Logistics$/ }) })
      .getByRole("button", { name: "Keep this one, merge the others in" }).click();
    await see("Merged 1 lead(s) into Kesari Logistics.");
    await see("Overview of 180 leads");
    const row = page.locator(".row", { hasText: "Sahyadri BPO Services · Title" }).first();
    await row.getByRole("button", { name: "Keep current" }).click();
    await see("Kept the current value.");
    const kesari = await leadByCompany("Kesari Logistics");
    if (!(kesari.other_contacts || []).length && !(kesari.merged_ids || []).length) throw new Error("merge did not keep the other record");
    return "2 duplicate groups (same phone), 1 merged";
  });

  // ======================= Campaign preview =======================
  const job = (title, org, site, city) => ({ "@context": "https://schema.org", "@type": "JobPosting", title, datePosted: "2026-10-12",
    hiringOrganization: { "@type": "Organization", name: org, sameAs: site }, jobLocation: { address: { addressLocality: city } } });
  const careersHtml = `<html><body><script type="application/ld+json">${JSON.stringify([
    job("Voice Process Associate", "Thar Contact Centre Pvt Ltd", "https://thar-contact.invalid", "Pune"),
    job("Telecaller (Hindi/Marathi)", "Thar Contact Centre Pvt Ltd", "https://thar-contact.invalid", "Pune"),
    job("Customer Support Executive", "Sahyadri BPO Services", "https://sahyadri-bpo.invalid", "Pune")])}</script></body></html>`;
  await step("Campaign: create with the wizard and preview; nothing is saved", async () => {
    await nav("Campaigns");
    await page.getByRole("button", { name: "New campaign" }).click();
    await page.getByLabel("Campaign name").fill("Pune BPOs hiring support staff (Nov)");
    for (const chip of ["+ BPO", "+ Pune", "+ customer support", "+ telecaller"]) await page.getByRole("button", { name: chip }).click();
    await page.getByRole("button", { name: "51-200" }).click();
    await page.getByRole("button", { name: "Next", exact: true }).click();
    await page.getByText("Site blocks automatic reading?").click();
    await page.getByLabel("Page link (optional)").fill("https://thar-contact.invalid/careers");
    await page.getByLabel("Page HTML").fill(careersHtml);
    await page.getByLabel("Search your leads").fill("Teesta");
    await page.locator("label", { hasText: "Teesta" }).first().locator("input[type=checkbox]").check();
    await see("1 picked");
    await page.getByRole("button", { name: "Next", exact: true }).click();
    if (await page.getByLabel("Who gets the update after each run").inputValue() !== "Priya") throw new Error("caller not prefilled");
    await page.getByRole("button", { name: "Next", exact: true }).click();
    await see("1 careers page(s), 1 imported lead(s)");
    const before = (await api("/api/leads")).body.length;
    await page.getByRole("button", { name: "Run now (preview)" }).click();
    await page.getByTestId("run-report").filter({ hasText: "Preview: nothing was saved" }).waitFor({ timeout: 30000 });
    const table = page.getByTestId("preview-table");
    await table.locator("tr", { hasText: "Thar Contact Centre" }).getByText("Would add").waitFor();
    await table.locator("tr", { hasText: "Sahyadri BPO Services" }).waitFor();
    const after = (await api("/api/leads")).body;
    if (after.length !== before || after.some((l) => /thar contact/i.test(l.company || ""))) throw new Error("the preview saved leads");
    const saved = (await api("/api/campaigns")).body;
    if (saved.length !== 1 || saved[0].status !== "draft") throw new Error("campaign not saved as a draft");
  });

  // ======================= Sequences =======================
  const HEROES = ["Sahyadri BPO Services", "Narmada Retail", "Indus Fintech", "Ganga Foods", "Vindhya Staffing Solutions", "Aravali Healthcare",
    "Kaveri Textiles", "Shivalik Telecom Services", "Godavari Infra", "Malabar Security Services", "Deccan Auto Components", "Kesari Logistics"];
  await step("Leads: pick 13 leads and start the sequence; the disqualified one is refused", async () => {
    await nav("Leads");
    for (const company of [...HEROES, "Ganga Facility Services"]) {
      await page.getByLabel("Search leads").fill(company);
      await page.getByLabel(`Select ${company}`, { exact: true }).check();
    }
    await page.getByLabel("Search leads").fill("");
    await see("13 selected");
    await page.getByRole("button", { name: "Start email sequence" }).click();
    await see("Started the email sequence for 12 of 13. The rest have no email, are already in it, or cannot be emailed.");
    const enrolled = (await api("/api/leads")).body.filter((l) => l.sequence);
    if (enrolled.length !== 12) throw new Error(`${enrolled.length} enrolled`);
  }, [{ status: 400, part: "/enroll" }]);
  await step("Outbox: run sequences in practice; a few per run, never Email 1 twice, signed unsubscribe in every email", async () => {
    await nav("Outbox & sending");
    await see("Practice mode: nothing leaves the app");
    const counts = [];
    for (let run = 0; run < 4; run++) {
      // the status line keeps the previous run's text until the answer arrives, so read the count from the answer
      const [resp] = await Promise.all([page.waitForResponse((r) => r.url().includes("/api/sequences/run")),
        page.getByRole("button", { name: "Run sequences now" }).click()]);
      const { sent } = await resp.json();
      await page.getByRole("status").filter({ hasText: `${sent} email(s) practised (not sent)` }).waitFor();
      counts.push(sent);
    }
    const box = (await api("/api/outbox")).body;
    if (box.length !== 12 || counts.join(",") !== "5,5,2,0") throw new Error(`runs practised ${counts}; outbox ${box.length}`);
    if (new Set(box.map((m) => m.lead_id)).size !== 12) throw new Error("a lead got Email 1 twice");
    if (box.some((m) => m.status !== "dry_run" || !/\/u\/[\w]+\.[\w-]+/.test(m.body) || m.body.includes("{{"))) throw new Error("bad practice email");
    await page.locator("tbody .pill", { hasText: "practice" }).first().waitFor();
    return `runs practised ${counts.join(" / ")}`;
  });

  // ======================= Replies =======================
  const REPLIES = [
    ["Sahyadri BPO Services", "Yes please, send the sample screen for our telecaller role. Call me after 3 pm.", "interested", "Interested"],
    ["Malabar Security Services", "Haan ji, bhej dijiye sample. Hamare paas 40 guards ki requirement hai.", "interested", "Interested"],
    ["Narmada Retail", "Not interested, thanks. We already use Naukri for this.", "not_interested", "Disqualified"],
    ["Deccan Auto Components", "Honestly this is not for us right now.", "not_interested", "Disqualified"],
    ["Indus Fintech", "Please remove me from your list and delete my data.", "unsubscribe", "Unsubscribed"],
    ["Ganga Foods", "Abhi nahi, Diwali ke baad baat karte hain.", "not_now", "Not now"],
    ["Vindhya Staffing Solutions", "I am not the right person. Please contact our HR head, Ms. Kavya Menon.", "wrong_person", null],
    ["Aravali Healthcare", "How much does it cost after the free role?", "question", null],
    ["Kaveri Textiles", "Automatic reply: I am out of office until 10 November with limited access to email.", "auto_reply", null],
    ["Shivalik Telecom Services", "Delivery Status Notification (Failure)\n550 5.1.1 The email account that you tried to reach does not exist.", "bounce", null],
    ["Godavari Infra", "Will check with my team and revert.", "other", null],
  ];
  await step("Replies: one of every kind pasted on leads; a no is never a yes", async () => {
    for (const [company, text, label, stage] of REPLIES) {
      const lead = await logReply(company, text, label, stage);
      if (!lead.sequence?.stopped && label !== "auto_reply") throw new Error(`${company}: sequence not stopped`);
      if (["not_interested", "not_now", "unsubscribe", "bounce", "other", "question", "wrong_person", "auto_reply"].includes(label) && lead.consent)
        throw new Error(`${company}: consent recorded for a ${label}`);
      if (label === "interested" && !lead.consent) throw new Error(`${company}: yes without consent`);
    }
    const suppressed = (await api("/api/settings")).status === 200;
    if (!suppressed) throw new Error("settings unreadable");
    const wa = (await api("/api/whatsapp/outbox")).body;
    if (wa.length) throw new Error("a WhatsApp message went out without a rule");
    return `${REPLIES.length} replies, ${new Set(REPLIES.map((r) => r[2])).size} kinds`;
  });

  // ======================= Today and Tasks =======================
  await step("Today: Priya's call task, replies to handle, and the erasure request are listed", async () => {
    await nav("Dashboard");
    const today = page.locator(".card.today");
    await today.locator(".today-item", { hasText: "Sahyadri BPO Services" }).filter({ hasText: "Call them" })
      .getByRole("button", { name: "Open call desk" }).waitFor();
    await today.locator(".today-item", { hasText: "Answer a question" }).filter({ hasText: "Aravali Healthcare" }).waitFor();
    await today.locator(".today-item", { hasText: "Find the right contact" }).filter({ hasText: "Vindhya Staffing Solutions" }).waitFor();
    await today.locator(".today-item", { hasText: "Read a reply" }).filter({ hasText: "Godavari Infra" }).waitFor();
    await today.locator(".today-item", { hasText: "Erasure request" }).filter({ hasText: "Indus Fintech" }).waitFor({ timeout: 3000 });
  });
  await step("Tasks: the call task belongs to Priya; the erasure request is flagged", async () => {
    await nav("Tasks & follow-ups");
    await see("Everything that needs doing");
    const call = page.locator(".task-list li", { hasText: "Call them" }).filter({ hasText: "Sahyadri BPO Services" }).first();
    await call.waitFor();
    if (!(await call.innerText()).includes("Priya")) throw new Error("call task is not Priya's");
    await page.locator(".task-list li", { hasText: "Erasure request" }).filter({ hasText: "lead unsubscribed" }).first().waitFor();
  });
  await step("Lead panel: log the call with a follow-up; the call task closes", async () => {
    await openLead("Sahyadri BPO Services");
    await page.getByRole("button", { name: "Log a call / meeting / note" }).click();
    await page.getByLabel("Outcome").selectOption("connected");
    await page.getByLabel("Minutes").fill("6");
    await page.getByLabel("Notes (what was said, next steps)").fill("Spoke to Priyanka: 40 telecaller openings in Pune; wants a demo this week.");
    await page.getByLabel("Follow up on (IST, optional)").fill(`${istDate(1)}T16:00`);
    await page.getByLabel("Follow-up task").fill("Send Priyanka the sample screen and confirm the demo");
    await page.locator("form.inline-form", { hasText: "What happened?" }).getByRole("button", { name: "Save", exact: true }).click();
    await see("Saved, and a follow-up task was created.");
    await page.locator(".trail-item", { hasText: "40 telecaller openings" }).first().waitFor();
    const open = page.locator(".task-list li");
    await open.filter({ hasText: "Send Priyanka the sample screen" }).first().waitFor();
    if (await open.filter({ hasText: "Call them" }).count()) throw new Error("call task still open after the call was logged");
    await closeDrawer();
  });

  // ======================= Meetings =======================
  await step("Integrations: connect a booking link (calendar) and make it active", async () => {
    await nav("Integrations");
    await page.getByTestId("tab-calendar").click();
    await page.getByTestId("prov-calendar-booking_link").getByRole("button", { name: "Set up" }).click();
    await page.getByRole("button", { name: "Next" }).click();
    await page.getByLabel("Booking link *").fill("https://bookings.example.in/team-priya?name={name}&email={email}");
    await page.getByRole("button", { name: "Save and continue" }).click();
    await page.getByTestId("run-test").click();
    await page.getByTestId("test-result").getByText("Works").waitFor();
    await page.keyboard.press("Escape");
    await page.getByTestId("prov-calendar-booking_link").getByText("Active").waitFor();
  });
  await step("Meetings: prefilled booking link for the lead, then a fixed-time booking with a practice invite", async () => {
    await openLead("Sahyadri BPO Services");
    const box = page.getByTestId("lead-meetings");
    await box.getByRole("button", { name: /Book a meeting/ }).click();
    await box.getByRole("button", { name: "Get booking link" }).click();
    const link = await box.locator("code").innerText();
    if (!link.startsWith("https://bookings.example.in/team-priya?name=Priyanka") || !link.includes("priyanka.deshmukh%40sahyadri-bpo.invalid"))
      throw new Error(`booking link ${link}`);
    await box.getByRole("button", { name: "Copy link" }).click();
    if ((await page.evaluate(() => navigator.clipboard.readText())) !== link) throw new Error("link not copied");
    await box.getByRole("checkbox", { name: /fixed time/ }).check();
    await box.getByLabel("Time (IST)").fill("15:00");
    await box.getByRole("button", { name: "Book meeting" }).click();
    await box.getByText("invite kept as practice").waitFor();
    await closeDrawer();
    await nav("Meetings");
    await see("Upcoming demos and calls");
    await page.getByText("Sahyadri BPO Services").first().waitFor();
    const rows = (await api("/api/meetings")).body;
    if (!rows.some((m) => m.provider === "booking_link" && m.invite === "dry_run" && m.start.startsWith(istDate(1)))) throw new Error("meeting not stored");
    return link;
  });

  // ======================= LinkedIn =======================
  await step("LinkedIn queue: start, copy, mark sent, accepted (sent by hand)", async () => {
    await nav("LinkedIn outreach");
    await see("LinkedIn doesn't allow automation");
    const suggestion = page.locator(".task-list li", { hasText: "Arjun Mehra" });
    await suggestion.getByRole("button", { name: "Start LinkedIn sequence" }).click();
    await see("Added. Step 1 is in today's list.");
    const card = page.locator(".card", { hasText: "Arjun Mehra · Chambal Facility Services" });
    await card.getByText("Step 1").waitFor();
    await card.getByRole("button", { name: "Copy message" }).click();
    await see("Message copied. Paste it on LinkedIn.");
    const copied = await page.evaluate(() => navigator.clipboard.readText());
    if (!copied.startsWith("Hi Arjun") || copied.length > 300) throw new Error(`copied note: ${copied.slice(0, 60)} (${copied.length})`);
    await card.getByRole("button", { name: "Mark sent" }).click();
    await see("Step 1 marked sent.");
    const waiting = page.locator(".task-list li", { hasText: "Arjun Mehra" }).filter({ has: page.getByRole("button", { name: "They accepted" }) });
    await waiting.getByRole("button", { name: "They accepted" }).click();
    await see("Accepted. The first message is scheduled.");
    const tasks = (await api("/api/tasks/board")).body;
    const all = ["overdue", "today", "upcoming", "no_date"].flatMap((k) => tasks[k] || []);
    if (!all.some((t) => t.title?.startsWith("LinkedIn step 2") && t.company === "Chambal Facility Services")) throw new Error("step 2 not scheduled");
  });

  // ======================= A caller joins =======================
  await step("Setup: the owner adds a caller login", async () => {
    await nav("Setup");
    await page.getByRole("tab", { name: "Connections" }).click();
    const card = page.getByTestId("users-card");
    await card.getByLabel("Email").fill(CALLER.email);
    await card.getByLabel("Name").fill(CALLER.name);
    await card.getByLabel("Role").selectOption("caller");
    await card.getByLabel("Password (10+ characters)").fill(CALLER.password);
    await card.getByRole("button", { name: "Add login" }).click();
    await card.getByText(`Login added for ${CALLER.email}`).waitFor();
  });
  let callerToken = "";
  await step("Caller signs in: works the call desk; owner-only screens and actions are refused", async () => {
    await signOut();
    await signIn(CALLER.password, CALLER.email);
    callerToken = await token();
    await nav("Setup");
    await page.getByRole("tab", { name: "Connections" }).click();
    await page.getByText("Integrations page").first().waitFor();
    if (await page.getByTestId("users-card").count()) throw new Error("caller sees Team logins");
    if (await page.getByRole("button", { name: "Save basics" }).count()) throw new Error("caller sees the basics form");
    await nav("Priya's call desk");
    await page.getByText("Sahyadri BPO Services").first().waitFor();
    await openLead("Malabar Security Services");
    if (await page.getByTestId("privacy-actions").count()) throw new Error("caller sees DPDP export / erase");
    await page.getByRole("button", { name: "Log a call / meeting / note" }).click();
    await page.getByLabel("Outcome").selectOption("call_back");
    await page.getByLabel("Notes (what was said, next steps)").fill("Farhan asked for a call back after 5 pm.");
    await page.locator("form.inline-form", { hasText: "What happened?" }).getByRole("button", { name: "Save", exact: true }).click();
    await see("Saved.");
    await closeDrawer();
    if (await page.getByRole("button", { name: "Download list for Apollo" }).count()) throw new Error("caller sees the Apollo CSV export");
    await nav("Outbox & sending");
    await page.getByRole("button", { name: "Run sequences now" }).click();
    await page.getByRole("status").filter({ hasText: "Your role (caller) cannot do this" }).waitFor();
    await nav("Simple rules");
    await page.getByRole("button", { name: "Add suggested rules" }).click();
    await see("Your role (caller) cannot do this");
    const lead = await leadByCompany("Malabar Security Services");
    for (const [p, opts] of [[`/api/leads/${lead.id}/export`, {}], [`/api/leads/${lead.id}/erase`, { method: "POST", body: '{"confirm":true}' }],
      ["/api/settings", { method: "PUT", body: '{"daily_cap":100}' }], ["/api/users", {}]]) {
      const r = await api(p, opts, callerToken);
      if (r.status !== 403) throw new Error(`caller got ${r.status} from ${opts.method || "GET"} ${p}`);
    }
  }, [{ status: 403, part: "/api/sequences/run" }, { status: 403, part: "/api/workflows" }]);
  await step("Caller signs out; the owner signs back in", async () => {
    await signOut();
    if ((await api("/api/leads", {}, callerToken)).status !== 401) throw new Error("caller token still works after sign-out");
    await signIn(PASSWORD);
  });

  // ======================= DPDP =======================
  await step("DPDP: the owner downloads Indus Fintech's data, erases it, and a later re-import is skipped", async () => {
    await openLead("Indus Fintech");
    const privacy = page.getByTestId("privacy-actions");
    const [dl] = await Promise.all([page.waitForEvent("download"), privacy.getByRole("button", { name: "Download their data" }).click()]);
    const data = JSON.parse(fs.readFileSync(await dl.path(), "utf8"));
    if (!JSON.stringify(data).includes("kavya.iyer@indus-fintech.invalid") || !JSON.stringify(data).includes("delete my data"))
      throw new Error("export misses the email or the reply");
    await privacy.getByRole("button", { name: "Erase their data…" }).click();
    await privacy.getByRole("button", { name: "Yes, erase everything" }).click();
    await drawer().waitFor({ state: "detached" });
    await page.getByLabel("Search leads").fill("Indus Fintech");
    await see("No leads match.");
    await wizardUpload("apollo-reimport-erased.csv");
    await page.getByRole("button", { name: "Next: dry run" }).click();
    await see("Left out (1)");
    if (await page.getByRole("button", { name: /^Import: 0 new, 0 updated/ }).isEnabled()) throw new Error("import of an erased person allowed");
    await page.getByRole("button", { name: "Close", exact: true }).last().click();
    const board = (await api("/api/tasks/board")).body;
    if (["overdue", "today", "upcoming", "no_date"].some((k) => (board[k] || []).some((t) => t.title === "Erasure request"))) throw new Error("erasure task left open");
  });

  await step("Sign out: back to the login screen and the session is revoked", async () => {
    const owner = await token();
    await signOut();
    if ((await api("/api/leads", {}, owner)).status !== 401) throw new Error("owner token still works");
    if ((await page.getByText("Dashboard").count()) > 0) throw new Error("app content visible after sign-out");
  });

  await ctx.close();
  return { size, results, problems: [...new Set(problems)] };
}

// ---------- run ----------
const ui = await startUi();
const browser = await chromium.launch(process.env.HEADED ? { headless: false, slowMo: 150 } : {});
let failed = 0;
try {
  for (const width of WIDTHS) {
    const apiServer = await startApi();
    try {
      const { size, results, problems } = await journey(browser, width);
      console.log(`\n=== ${size} (${width[1]}x${width[2]}) ===`);
      for (const [ok, name, note] of results) console.log(`${ok ? "PASS" : "FAIL"}  ${name}${note ? ` — ${note}` : ""}`);
      const bad = results.filter(([ok]) => !ok).length;
      console.log(`${results.length - bad}/${results.length} steps passed`);
      console.log(problems.length ? `Console errors / unexpected HTTP errors:\n  ${problems.join("\n  ")}` : "No console errors and no unexpected HTTP errors.");
      failed += bad + problems.length;
    } finally {
      await stopApi(apiServer);
    }
  }
} finally {
  await browser.close();
  await ui.close();
}
process.exit(failed ? 1 : 0);
