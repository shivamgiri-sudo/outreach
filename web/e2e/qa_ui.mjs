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
  await goPage(page, "leads", page.viewportSize().width <= 760);
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

if (want("keyboard")) await step("Keyboard: Ctrl+K search, J/K in drawer, Esc closes, focus visible", async () => {
  const notes = [];
  await goPage(page, "dashboard");
  await page.keyboard.press("Control+k");
  const box = page.getByRole("dialog").locator("input").first();
  await box.waitFor();
  await box.fill("Arogya");
  await settle(page, 500);
  const results = await page.getByRole("dialog").locator("button").allInnerTexts();
  notes.push(`ctrl+k "Arogya": ${results.length} results, first="${(results[0] || "").replace(/\n/g, " ")}"`);
  await page.keyboard.press("Enter");
  await settle(page, 600);
  const title = await page.locator("#drawer-title").innerText().catch(() => "(no drawer)");
  notes.push(`enter opened: ${title} at ${await page.evaluate(() => location.hash)}`);
  await page.keyboard.press("Escape");
  await settle(page, 300);
  notes.push(`esc closed drawer: ${(await page.locator(".drawer").count()) === 0}`);
  // empty search, junk
  await page.keyboard.press("Control+k");
  await page.getByRole("dialog").locator("input").first().fill("zzzz-no-such-thing-<script>");
  await settle(page, 300);
  notes.push(`junk: "${(await page.getByRole("dialog").innerText()).replace(/\n/g, " ").slice(0, 80)}"`);
  await page.keyboard.press("Escape");
  notes.push(`esc closed search: ${(await page.getByRole("dialog").count()) === 0}`);
  // J / K in the lead drawer from the Leads table
  await goPage(page, "leads");
  await page.locator("td[data-label=Company] button").first().click();
  const t0 = await page.locator("#drawer-title").innerText();
  await page.locator(".drawer h2, .drawer #drawer-title").first().click();
  await page.keyboard.press("j"); await settle(page, 400);
  const t1 = await page.locator("#drawer-title").innerText();
  await page.keyboard.press("j"); await settle(page, 400);
  const t2 = await page.locator("#drawer-title").innerText();
  await page.keyboard.press("k"); await settle(page, 400);
  const t3 = await page.locator("#drawer-title").innerText();
  const rows = (await page.locator("td[data-label=Company] button").allInnerTexts()).slice(0, 3);
  notes.push(`J/K: ${t0} -> ${t1} -> ${t2} -> k ${t3}; table first 3: ${rows.join(" | ")}`);
  if (t1 !== rows[1] || t2 !== rows[2] || t3 !== rows[1]) throw new Error(`J/K order wrong: ${notes.at(-1)}`);
  // typing j in the reply box must not jump
  await page.locator(".drawer").getByRole("tab", { name: "Outreach" }).click();
  await page.locator("#reply-box").fill("");
  await page.locator("#reply-box").type("jk");
  notes.push(`typing jk in a box keeps the lead: ${(await page.locator("#drawer-title").innerText()) === t3}`);
  await page.locator("#reply-box").fill("");
  await page.keyboard.press("Escape"); await settle(page, 300);
  notes.push(`esc from input closes drawer: ${(await page.locator(".drawer").count()) === 0}`);
  // focus visible: tab to the first nav button and read its outline
  await page.locator("body").click({ position: { x: 700, y: 5 } }).catch(() => {});
  let outlined = 0;
  for (let i = 0; i < 6; i++) {
    await page.keyboard.press("Tab");
    const o = await page.evaluate(() => { const el = document.activeElement; const cs = getComputedStyle(el); return { tag: el.tagName, o: cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0, sh: cs.boxShadow !== "none" }; });
    if (o.o || o.sh) outlined++;
  }
  notes.push(`focus ring on ${outlined}/6 tabbed controls`);
  if (outlined < 5) throw new Error(notes.join(" ;; "));
  return notes.join(" ;; ");
});

if (want("mobile")) await step("Phone 375px: menu opens/closes, bottom bar, drawer fits", async () => {
  const notes = [];
  await page.setViewportSize({ width: 375, height: 812 });
  await settle(page, 400);
  const bar = page.getByRole("navigation", { name: "Quick links" });
  const labels = await bar.locator("button").allInnerTexts();
  notes.push(`bottom bar: ${labels.map((x) => x.trim()).join(", ")}`);
  for (const lab of ["Leads", "Replies", "Tasks"]) {
    await bar.getByRole("button", { name: lab }).click(); await settle(page, 400);
    const o = await overflow(page);
    notes.push(`${lab}: ${await page.evaluate(() => location.hash)}${o ? " OVERFLOW " + o : ""}`);
  }
  await page.getByRole("button", { name: "Open menu" }).click();
  await page.locator("#main-nav").getByRole("button", { name: "Analytics", exact: true }).waitFor();
  await page.keyboard.press("Escape"); await settle(page, 300);
  notes.push(`esc closes menu: ${!(await page.locator(".layout.menu-open").count())}`);
  await bar.getByRole("button", { name: "More pages" }).click();
  await page.locator("#main-nav").getByRole("button", { name: "Companies", exact: true }).click(); await settle(page, 500);
  notes.push(`More -> Companies: ${await page.evaluate(() => location.hash)}, menu closed: ${!(await page.locator(".layout.menu-open").count())}`);
  await goPage(page, "leads", true);
  await page.locator("td[data-label=Company] button").first().click();
  await page.locator(".drawer").waitFor();
  const dw = await page.locator(".drawer").boundingBox();
  const o = await overflow(page);
  notes.push(`drawer width ${Math.round(dw.width)} at 375${o ? " OVERFLOW " + o : ""}`);
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 1400, height: 900 });
  if (notes.some((n) => n.includes("OVERFLOW"))) throw new Error(notes.join(" ;; "));
  return notes.join(" ;; ");
});

if (want("lists")) await step("Lists: save 4 replied leads as a list from Leads, check list numbers", async () => {
  await goPage(page, "leads");
  for (const c of ["Bharat Freight Movers", "Apollo Care Clinics", "Reliance Fresh Outlet", "Teleperformance Hub"]) {
    await page.getByLabel("Search leads").fill(c); await settle(page, 300);
    await page.getByLabel(`Select ${c}`, { exact: true }).check();
  }
  await page.getByLabel("Search leads").fill("");
  await page.getByTestId("bulkbar").getByRole("button", { name: "Save as list" }).click();
  const dlg = page.getByRole("dialog").last();
  await dlg.locator("input").fill("Replied in Nov — QA");
  await dlg.getByRole("button").filter({ hasText: /OK|Save|Continue|Yes/ }).first().click();
  const msg = await page.getByRole("status").first().innerText();
  await goPage(page, "lists");
  const row = page.locator("tr", { hasText: "Replied in Nov — QA" });
  const txt = (await row.innerText()).replace(/\s+/g, " ");
  // 4 leads, none really emailed (practice only) -> 0% reply; replies made outside email do not inflate the rate
  if (!/\b4\b/.test(txt)) throw new Error(`list row: ${txt}`);
  return `${msg} || ${txt}`;
});

if (want("campaignlist")) await step("Campaigns list: metrics of the two campaigns", async () => {
  await goPage(page, "campaigns");
  const txt = (await page.locator("main").innerText()).replace(/\n+/g, " / ");
  return txt.slice(0, 700);
});

if (want("drawers")) await step("Drawers at 1400/768/375: lead drawer tabs and company detail, no bad text or overflow", async () => {
  const bad = [];
  for (const [w, h] of [[1400, 900], [768, 1000], [375, 812]]) {
    await page.setViewportSize({ width: w, height: h });
    for (const company of ["Bharat Freight Movers", "Teleperformance Hub", "Shree Ganesh Logistics"]) {
      await openLead(company);
      for (const tab of ["Overview", "Activity", "Outreach", "Product / Free trial", "Data"]) {
        await page.locator(".drawer").getByRole("tab", { name: tab }).click(); await settle(page, 300);
        const txt = await page.locator(".drawer").innerText();
        for (const [re, label] of [[/\{\{\s*[\w.]+\s*\}\}/, "{{}}"], [/\bundefined\b|\bNaN\b|\[object Object\]|Invalid Date/, "junk"], [/\b(UTC|GMT)\b/, "UTC"]]) {
          const m = txt.match(re); if (m) bad.push(`${w} ${company}/${tab}: ${label} "…${txt.slice(Math.max(0, m.index - 60), m.index + 40).replace(/\s+/g, " ")}…"`);
        }
        const o = await overflow(page); if (o) bad.push(`${w} ${company}/${tab}: ${o}`);
      }
      await closeDrawer();
    }
    await goPage(page, "companies", w <= 760);
    await page.getByText("Bharat Freight Movers", { exact: true }).first().click(); await settle(page, 500);
    const ct = await page.locator("main, .drawer").last().innerText();
    if (/undefined|NaN|\{\{/.test(ct)) bad.push(`${w} company detail: bad text`);
    const o = await overflow(page); if (o) bad.push(`${w} company detail: ${o}`);
    await page.keyboard.press("Escape"); await settle(page, 300);
  }
  await page.setViewportSize({ width: 1400, height: 900 });
  if (bad.length) throw new Error(bad.join(" ;; "));
});

if (want("sorting")) await step("Leads: sort headers, filters and pages give correct rows", async () => {
  const notes = [];
  await goPage(page, "leads");
  await page.getByLabel("Leads per page").selectOption("50").catch(() => {});
  const col = async (label) => page.locator(`td[data-label="${label}"]`).allInnerTexts();
  await page.locator("th button.th-sort", { hasText: "Score" }).click(); await settle(page, 300);
  const scores = (await col("Score")).map((t) => parseInt(t, 10));
  notes.push(`score sorted desc: ${scores.every((v, i) => i === 0 || v <= scores[i - 1])} (${scores.slice(0, 5).join(",")})`);
  await page.locator("th button.th-sort", { hasText: "Company" }).click(); await settle(page, 300);
  const names = await col("Company");
  const sorted = [...names].sort((a, b) => a.localeCompare(b, "en", { sensitivity: "base" }));
  notes.push(`company A-Z: ${JSON.stringify(names.slice(0, 3)) === JSON.stringify(sorted.slice(0, 3))} (${names.slice(0, 3).join(" | ")})`);
  const pager = page.getByRole("navigation", { name: "Pages of leads" });
  const p1 = await pager.locator("span").first().innerText();
  await pager.getByRole("button", { name: "Next" }).click(); await settle(page, 300);
  const p2 = await pager.locator("span").first().innerText();
  const page2first = (await col("Company"))[0];
  notes.push(`pager: "${p1}" -> "${p2}", page 2 starts ${page2first} (expected ${sorted[50] || "?"})`);
  await page.getByLabel("Filter by stage").selectOption({ label: "Interested (said yes)" }).catch(async () => page.getByLabel("Filter by stage").selectOption("interested"));
  await settle(page, 300);
  const interested = await col("Company");
  notes.push(`stage=interested: ${interested.join(" | ")}`);
  await page.getByLabel("Filter by stage").selectOption("");
  await page.getByLabel("Search leads").fill("+91 98907 79946"); await settle(page, 300);
  notes.push(`search by phone with spaces: ${(await col("Company")).join(" | ") || "(none)"}`);
  const dl = await page.getByRole("button", { name: /^Download these/ }).first().innerText().catch(() => "");
  notes.push(`download button: ${dl}`);
  await page.getByLabel("Search leads").fill("KAVITA.NAIR@bharatfreightmovers.co.in"); await settle(page, 300);
  notes.push(`search by upper-case email: ${(await col("Company")).join(" | ") || "(none)"}`);
  await page.getByLabel("Search leads").fill("");
  if (!notes[0].includes("true") || !notes[1].includes("true") || interested.length !== 2 || notes[4].includes("(none)")) throw new Error(notes.join(" ;; "));
  return notes.join(" ;; ");
});

const out = results.map((r) => r.join(" | ")).join("\n");
console.log("\n==== RESULTS\n" + out + "\n==== PROBLEMS\n" + [...new Set(problems)].join("\n"));
await browser.close();
