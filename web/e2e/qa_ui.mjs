// QA sweep "ui": seed realistic data ONLY through the browser, then check numbers on every analytics surface.
//   node e2e/qa_ui.mjs <ui url> <password> <csv file> [steps=all|a,b]
// Read-backs (GET only) go through the same proxy, with the session token the UI holds, to double-check what is shown.
import { chromium } from "playwright";
import fs from "node:fs";
import { watch, signIn, goPage, settle, wait, textProblems, overflow } from "./qa_ui_lib.mjs";

const [UI, PASS, CSV, ONLY = "all"] = process.argv.slice(2);
const want = (s) => ONLY === "all" || ONLY.split(",").includes(s);
const browser = await chromium.launch({ headless: !process.env.HEADED });
const ctxB = await browser.newContext({ viewport: { width: 1400, height: 900 } });
const page = await ctxB.newPage();
page.setDefaultTimeout(15000);
const problems = [];
const ctx = watch(page, problems);
const results = [];
const see = (t, timeout = 10000) => page.getByText(t, { exact: false }).first().waitFor({ timeout });
const get = async (p) => {
  const tok = await page.evaluate(() => sessionStorage.getItem("outreach-token"));
  const r = await fetch(`${UI}${p}`, { headers: tok ? { Authorization: `Bearer ${tok}` } : {} });
  return r.json();
};
const step = async (name, fn) => {
  ctx.where = name;
  const before = problems.length;
  try { const note = await fn(); results.push(["PASS", name, note || ""]); }
  catch (e) {
    results.push(["FAIL", name, e.message.split("\n")[0]]);
    await page.screenshot({ path: `${process.env.SHOTS || "/tmp"}/fail-${name.replace(/\W+/g, "-")}.png`, fullPage: true }).catch(() => {});
  }
  for (const t of await textProblems(page)) problems.push(`[${name}] ${t}`);
  const extra = problems.slice(before);
  console.log(`${results.at(-1)[0]} ${name} ${results.at(-1)[2]}${extra.length ? `\n   ! ${extra.join("\n   ! ")}` : ""}`);
};
const closeDrawer = async () => { if (await page.locator(".drawer").count()) { await page.keyboard.press("Escape"); await page.locator(".drawer").waitFor({ state: "detached" }); } };
const openLead = async (company) => {
  await closeDrawer();
  await goPage(page, "leads");
  await page.getByLabel("Search leads").fill(company);
  await settle(page, 300);
  await page.locator("td[data-label=Company] button").getByText(company, { exact: true }).first().click();
  await page.locator("#drawer-title").getByText(company, { exact: true }).waitFor();
};

await page.goto(UI);
await page.getByLabel("Password").waitFor();
await page.getByLabel("Password").fill(PASS);
await page.getByRole("button", { name: "Sign in" }).click();
await page.locator("main").waitFor();
await settle(page, 800);

if (want("onboarding")) await step("Onboarding: business details typed and saved", async () => {
  const dlg = page.locator(".onb");
  if (!(await dlg.waitFor({ timeout: 6000 }).then(() => true, () => false))) return "wizard not shown (already done)";
  await dlg.getByLabel("Company name").fill("Mas Callnet Staffing");
  await dlg.getByLabel("Product or service").fill("Bulk hiring for frontline roles");
  await dlg.getByLabel("Website (optional)").fill("https://www.mascallnet.com");
  await dlg.getByLabel("Who signs the emails").fill("Shuvam Giri");
  await dlg.getByRole("button", { name: "Save and continue" }).click();
  await see("Step 2 of 7");
  for (let i = 0; i < 6; i++) {
    const fin = dlg.getByRole("button", { name: "Finish" });
    if (await fin.isVisible().catch(() => false)) { await fin.click(); break; }
    const skip = dlg.getByRole("button", { name: "Skip this step" });
    if (await skip.isVisible().catch(() => false)) await skip.click(); else await dlg.getByRole("button", { name: "Continue" }).click();
    await wait(300);
  }
  await dlg.waitFor({ state: "detached" });
  const brand = await get("/api/brand");
  if (!JSON.stringify(brand).includes("Mas Callnet Staffing")) throw new Error(`brand not saved: ${JSON.stringify(brand).slice(0, 200)}`);
});

if (want("import")) await step("Import wizard: upload 300-row CSV, dry run, import", async () => {
  await goPage(page, "leads");
  await page.getByRole("button", { name: "Import leads" }).click();
  await page.locator("#import-file").setInputFiles(CSV);
  await see("Loaded ");
  await page.getByRole("button", { name: "Next: check the columns" }).click();
  await page.getByRole("button", { name: "Next: dry run" }).waitFor();
  const map = {};
  for (const f of ["Company Name", "Email", "Mobile Phone", "City", "Industry", "Open Positions", "Number of openings", "Employees"]) {
    map[f] = await page.getByLabel(`Field for ${f}`, { exact: true }).inputValue().catch(() => "?");
  }
  await page.getByRole("button", { name: "Next: dry run" }).click();
  const btn = page.getByRole("button", { name: /^Import: \d+ new/ });
  await btn.waitFor();
  const label = await btn.innerText();
  const summary = (await page.locator("main").innerText()).match(/[^\n]*(left out|skipped|problem)[^\n]*/gi) || [];
  await btn.click();
  await page.getByRole("button", { name: "Undo this import" }).waitFor({ timeout: 60000 });
  const leads = await get("/api/leads");
  return `mapping=${JSON.stringify(map)} | ${label} | ${summary.slice(0, 3).join(" / ")} | stored=${leads.length}`;
});

const REPLIES = [
  ["Bharat Freight Movers", "Yes, interested. We need 40 delivery executives in Pune by December, please call me tomorrow."],
  ["Apollo Care Clinics", "Not interested at the moment, we have an in-house team."],
  ["Reliance Fresh Outlet", "What is your fee per hire and do you cover Tier 2 cities?"],
  ["Teleperformance Hub", "I am out of office until 15 November with limited access to email."],
  ["Sundaram Fasteners Unit", "Please remove me from your list and do not email again."],
  ["Express Cargo Lines", "Sounds good, can we do a call on Monday at 11?"],
  ["Arogya Diagnostics", "Please send the case study and pricing."],
];

if (want("enroll")) await step("Leads: select 100 on a page, Start email sequence", async () => {
  await goPage(page, "leads");
  await page.getByLabel("Leads per page").selectOption("100");
  await settle(page);
  await page.getByLabel("Select all shown").check();
  const bar = page.getByTestId("bulkbar");
  const n = await bar.locator("b").first().innerText();
  await bar.getByRole("button", { name: "Start email sequence" }).click();
  await page.getByRole("button", { name: /^Yes, start for/ }).click();
  const msg = await page.getByRole("status").first().innerText({ timeout: 20000 });
  // the reply companies too, one by one through search
  for (const [company] of REPLIES) {
    await page.getByLabel("Search leads").fill(company);
    await settle(page, 300);
    const box = page.getByLabel(`Select ${company}`, { exact: true });
    if (!(await box.count())) continue;
    await box.check();
  }
  await page.getByLabel("Search leads").fill("");
  let msg2 = "";
  if (await bar.isVisible().catch(() => false)) {
    await bar.getByRole("button", { name: "Start email sequence" }).click();
    await page.getByRole("button", { name: /^Yes, start for/ }).click();
    await settle(page, 800);
    msg2 = await page.getByRole("status").first().innerText();
  }
  const leads = await get("/api/leads");
  const onSeq = leads.filter((l) => l.sequence).length;
  return `${n}: "${msg}" | then "${msg2}" | on sequence now=${onSeq}`;
});

if (want("runseq")) await step("Outbox: Run sequences now (practice mode)", async () => {
  await goPage(page, "outbox");
  const outs = [];
  for (let i = 0; i < 3; i++) {
    await page.getByRole("button", { name: "Run sequences now" }).click();
    await settle(page, 800);
    outs.push(await page.getByRole("status").first().innerText());
  }
  const leads = await get("/api/leads");
  const sent = leads.filter((l) => l.sequence && (l.sequence.step > 0 || (l.sequence.sent || []).length)).length;
  return `${outs.join(" || ")} | leads with a step done=${sent}`;
});

if (want("replies")) await step("Lead drawer: paste 7 real-looking replies", async () => {
  const got = [];
  for (const [company, text] of REPLIES) {
    await openLead(company);
    await page.locator(".drawer").getByRole("tab", { name: "Outreach" }).click();
    await page.locator("#reply-box").fill(text);
    await page.getByRole("button", { name: "Log reply" }).click();
    const st = await page.getByRole("status").filter({ hasText: "Detected:" }).first().innerText();
    const stage = await page.locator("h3", { hasText: "Stage:" }).first().innerText().catch(() => "?");
    got.push(`${company}: ${st.slice(0, 60)} [${stage}]`);
  }
  await closeDrawer();
  return got.join(" ; ");
});

if (want("tasks")) await step("Tasks: add three follow-ups by typing", async () => {
  await goPage(page, "tasks");
  const T = [["Call Bharat Freight Movers about 40 delivery executives", 1], ["Send fee card to Reliance Fresh Outlet", 0], ["Prepare Monday call notes for Express Cargo Lines", 3]];
  for (const [title, days] of T) {
    await page.getByLabel("New task").fill(title);
    const d = new Date(Date.now() + (330 + days * 1440) * 60000).toISOString().slice(0, 10);
    await page.getByLabel("Due (IST)").fill(`${d}T11:30`);
    await page.getByRole("button", { name: "Add", exact: true }).click();
    await see(title);
  }
  const txt = await page.locator("main").innerText();
  return (txt.match(/[^\n]*11:30[^\n]*/g) || []).slice(0, 3).join(" / ");
});

if (want("campaign")) await step("Campaigns: create a campaign with the wizard, preview, run for real", async () => {
  await goPage(page, "campaigns");
  await page.getByRole("button", { name: "New campaign" }).click();
  await page.locator("#camp-name").fill("Pune logistics hiring delivery staff");
  await page.getByRole("button", { name: "+ Logistics" }).click();
  await page.getByRole("button", { name: "+ Pune" }).click();
  await page.locator("#chip-roles_hiring").fill("delivery executive");
  await page.locator("#chip-roles_hiring").press("Enter");
  await page.getByRole("button", { name: "51-200", exact: true }).click();
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await page.locator("#camp-pages").fill("https://www.delhivery.com/careers");
  const search = page.getByLabel("Search your leads");
  let picked = 0;
  if (await search.isVisible().catch(() => false)) {
    await search.fill("Logistics");
    const boxes = page.locator("main input[type=checkbox]").filter({ has: page.locator("xpath=..") });
    const labels = page.locator("main label").filter({ hasText: /Logistics/ });
    for (let i = 0; i < Math.min(3, await labels.count()); i++) { await labels.nth(i).locator("input[type=checkbox]").check(); picked++; }
  }
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await page.getByRole("button", { name: "Next", exact: true }).click();
  const review = await page.locator("main ul.steps").innerText();
  await page.getByRole("button", { name: "Run now (preview)" }).click();
  await page.getByRole("button", { name: "Run now (preview)" }).waitFor({ timeout: 120000 });
  await settle(page, 500);
  const pv = (await page.locator("main").innerText()).split("Review and run").pop().slice(0, 600).replace(/\n+/g, " / ");
  await page.getByRole("button", { name: "Run for real" }).click();
  await page.getByRole("button", { name: "Run for real" }).waitFor({ timeout: 180000 });
  await settle(page, 500);
  const real = (await page.locator("main").innerText()).split("The preview searches").pop().slice(0, 600).replace(/\n+/g, " / ");
  return `picked=${picked} | review: ${review.replace(/\n/g, " / ")} || PREVIEW: ${pv} || REAL: ${real}`;
});

if (want("campaign_wipro")) await step("Campaigns: careers page https://www.wipro.com/ finds jobs (preview)", async () => {
  await goPage(page, "campaigns");
  await page.getByRole("button", { name: "New campaign" }).click();
  await page.locator("#camp-name").fill("Wipro hiring watch");
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await page.locator("#camp-pages").fill("https://www.wipro.com/");
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await page.getByRole("button", { name: "Run now (preview)" }).click();
  await page.getByText("Preview: nothing was saved").waitFor({ timeout: 240000 });
  await settle(page, 500);
  const txt = (await page.locator("main").innerText()).split("Preview: nothing was saved").pop();
  const found = Number((txt.match(/Found\s*\n\s*(\d+)/) || [])[1]);
  if (!(found > 0)) throw new Error(`Found ${found}: ${txt.slice(0, 400).replace(/\n+/g, " / ")}`);
  return txt.slice(0, 400).replace(/\n+/g, " / ");
});

if (want("callscript")) await step("Call desk: script never shows raw {{brand}} placeholders", async () => {
  await goPage(page, "calls");
  const box = page.getByTestId("call-script");
  await box.waitFor();
  const txt = await box.innerText();
  if (/\{\{/.test(txt)) throw new Error(`raw placeholder in call script: ${txt.slice(0, 300)}`);
  const gap = await box.getByTestId("script-gap").count();
  return `gap notes=${gap}; ${txt.split("\n").find((l) => l.startsWith("Why us")) || ""}`;
});

const tile = async (label) => {
  const t = page.getByTestId(`kpi-${label}`).first();
  return (await t.innerText()).replace(/\n+/g, " | ");
};
if (want("periods")) await step("Dashboard + Analytics periods: 7/30/90/custom, long and reversed ranges", async () => {
  const notes = [];
  for (const id of ["dashboard", "analytics"]) {
    await goPage(page, id);
    for (const d of [7, 90, 30]) {
      await page.getByRole("button", { name: `Last ${d} days` }).click();
      await settle(page, 500);
      notes.push(`${id} ${d}d: ${await tile("New leads")} / ${await tile("Replied")}`);
    }
    await page.getByRole("button", { name: "Custom", exact: true }).click();
    await page.getByLabel("From (IST day)").fill("2023-01-01");
    await settle(page, 300);
    const body = await page.locator("main").innerText();
    if (/could not load/i.test(body)) throw new Error(`${id}: a 2023-to-today range breaks the page`);
    if (!(await page.getByRole("button", { name: "Apply" }).isDisabled())) throw new Error(`${id}: Apply allowed for a range over 731 days`);
    notes.push(`${id} custom 2023-01-01..today: ${(body.match(/Pick at most[^\n]*/) || ["(no hint)"])[0]}`);
    await page.getByLabel("From (IST day)").fill("2026-11-01");
    await page.getByLabel("To (IST day)").fill("2026-11-06");
    await page.getByRole("button", { name: "Apply" }).click();
    await settle(page, 600);
    notes.push(`${id} custom 1-6 Nov: ${await tile("New leads")} :: ${(await page.getByTestId("period-picker").innerText()).split("\n").pop()}`);
    await page.getByLabel("To (IST day)").fill("2026-10-01");
    notes.push(`${id} reversed: Apply disabled=${await page.getByRole("button", { name: "Apply" }).isDisabled()}`);
    await page.getByRole("button", { name: "Last 30 days" }).click();
    await settle(page, 400);
  }
  return notes.join(" ;; ");
});

const out = results.map((r) => r.join(" | ")).join("\n");
console.log("\n==== RESULTS\n" + out + "\n==== PROBLEMS\n" + [...new Set(problems)].join("\n"));
await browser.close();
