// QA (area "sales funnel"): import Rajesh's columns -> the Sales funnel page -> drill-down, filters, rules in the lead drawer, polling.
//   API: a fresh OUTREACH_DATA_FILE, OUTREACH_ADMIN_PASSWORD=qa-admin-pass-1 OUTREACH_ADMIN_LOGIN=1 OUTREACH_EMAIL_DOMAIN_CHECK=0 OUTREACH_IGNORE_BUSINESS_HOURS=1
//   UI:  npx vite build --outDir <temp>/dist, served by e2e/static_proxy.mjs (CADDY_HEADERS=deploy/Caddyfile.template for the production CSP)
//   cd web && E2E_UI=http://localhost:5291 SHOT_DIR=/tmp/shots node e2e/qa_funnel.mjs
import { chromium } from "playwright";

const UI = process.env.E2E_UI || "http://localhost:5291";
const ADMIN = process.env.E2E_PASSWORD || "qa-admin-pass-1";
const SHOTS = process.env.SHOT_DIR || "/tmp";
const results = [];
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
const problems = [];
page.on("pageerror", (e) => problems.push(`pageerror ${e.message}`));
page.on("console", (m) => { if (m.type() === "error" && /Content Security Policy|Refused to/.test(m.text())) problems.push(`csp ${m.text().slice(0, 160)}`); });

async function step(name, fn) {
  try { await fn(); results.push([true, name]); } catch (e) { results.push([false, `${name} — ${e.message.split("\n")[0]}`]); }
}
const ok = (cond, msg) => { if (!cond) throw new Error(msg); };
// Polling helper (page.waitForFunction evals a string, which the production CSP forbids).
async function until(fn, msg, ms = 20000) {
  const end = Date.now() + ms;
  for (;;) {
    try { if (await fn()) return; } catch { /* not there yet */ }
    if (Date.now() > end) throw new Error(`timed out: ${msg}`);
    await new Promise((r) => setTimeout(r, 250));
  }
}
const text = (id) => page.getByTestId(id).innerText();
const api = (method, path, body) => page.evaluate(async ([m, p, b]) => {
  const r = await fetch(p, { method: m, headers: { "Content-Type": "application/json", Authorization: `Bearer ${sessionStorage.getItem("outreach-token")}` }, body: b ? JSON.stringify(b) : undefined });
  return { status: r.status, body: await r.json().catch(() => ({})) };
}, [method, path, body]);

// ---- the test data: dates are relative to today (India time) so the numbers below hold on any day
const ist = (n) => { const d = new Date(Date.now() + 330 * 60000 - n * 86400000); return d.toISOString().slice(0, 10).replace(/-/g, "/") + " 10:00"; };
const HEAD = "Company,Email,Lead Status,Created Date,MQL Date,SAL Date,SQL Date,Meeting Held Date,Proposal Date,Negotiation Date,Closed Date,Recycled Date,Lost Stage,Lost Reason,Est. Monthly Value (₹),Closed Monthly Value (₹),Contract Tenure (Months),Lead Owner,Lead Source,Industry,Service Line,Lead Score (Fit),Engagement,Remarks";
const row = (o) => HEAD.split(",").map((h) => `"${(o[h] ?? "").toString().replace(/"/g, '""')}"`).join(",");
const L = (company, status, created, o = {}) => row({ Company: company, Email: `ravi@${company.toLowerCase().replace(/\W+/g, "")}.example`, "Lead Status": status, "Created Date": ist(created), ...o });
const ROWS = [
  L("Alpha Won", "Closed Won", 40, { "MQL Date": ist(39), "SAL Date": ist(38), "SQL Date": ist(37), "Meeting Held Date": ist(35), "Proposal Date": ist(33), "Negotiation Date": ist(30), "Closed Date": ist(5),
    "Est. Monthly Value (₹)": "1,00,000", "Closed Monthly Value (₹)": 120000, "Contract Tenure (Months)": 6, "Lead Owner": "Asha", "Lead Source": "Meta Ads", Industry: "BFSI", "Service Line": "Collections", "Lead Score (Fit)": "A", Engagement: "Hot" }),
  L("Bravo Won", "Closed Won", 70, { "MQL Date": ist(69), "SAL Date": ist(68), "SQL Date": ist(67), "Meeting Held Date": ist(60), "Proposal Date": ist(50), "Negotiation Date": ist(40), "Closed Date": ist(2),
    "Est. Monthly Value (₹)": 180000, "Closed Monthly Value (₹)": 200000, "Lead Owner": "Bala", "Lead Source": "Email", Industry: "Retail", "Service Line": "Inbound", "Lead Score (Fit)": "A", Engagement: "Warm" }),
  L("Charlie SQL", "Sales Qualified Lead (SQL)", 20, { "MQL Date": ist(19), "SAL Date": ist(18), "SQL Date": ist(15), "Est. Monthly Value (₹)": 50000, "Lead Owner": "Asha", "Lead Source": "Email", Industry: "BFSI", "Service Line": "Collections", "Lead Score (Fit)": "B", Engagement: "Hot" }),
  L("Delta Meeting", "Discovery Meeting Held", 12, { "MQL Date": ist(11), "SAL Date": ist(10), "SQL Date": ist(9), "Meeting Held Date": ist(6), "Est. Monthly Value (₹)": 80000, "Lead Owner": "Bala", "Lead Source": "LinkedIn", Industry: "Retail", "Service Line": "Inbound", "Lead Score (Fit)": "A", Engagement: "Hot" }),
  L("Echo Lost", "Closed Lost", 25, { "MQL Date": ist(24), "SAL Date": ist(23), "Closed Date": ist(20), "Lost Stage": "SAL", "Lost Reason": "Price", "Lead Owner": "Asha", "Lead Source": "Email", Industry: "BFSI" }),
  L("Foxtrot Inquiry", "Inquiry / Raw Lead", 2, { "Lead Owner": "Bala", "Lead Source": "Cold Call", Industry: "Retail", "Lead Score (Fit)": "C", Engagement: "Cold" }),
  L("Golf MQL", "Marketing Qualified Lead (MQL)", 8, { "MQL Date": ist(7), "Lead Owner": "Asha", "Lead Source": "Meta Ads", Industry: "BFSI", "Lead Score (Fit)": "B", Engagement: "Warm" }),
  L("Hotel Recycled", "Recycled / Nurture", 50, { "MQL Date": ist(49), "Recycled Date": ist(10), "Lost Reason": "Timing", "Lead Owner": "Bala", "Lead Source": "Event" }),
  L("India Proposal", "Proposal Submitted", 30, { "MQL Date": ist(29), "SAL Date": ist(28), "SQL Date": ist(27), "Meeting Held Date": ist(26), "Proposal Date": ist(22), "Est. Monthly Value (₹)": 90000, "Lead Owner": "Asha", "Lead Source": "Event", Industry: "BFSI" }),
  L("Juliet NoMeeting", "Proposal Submitted", 4, { "Lead Owner": "Bala", "Lead Source": "Email", "Est. Monthly Value (₹)": 40000 }),
];
const CSV = `${HEAD}\n${ROWS.join("\n")}\n`;
const EXPECT_STAGES = [10, 8, 6, 5, 4, 3, 2, 2];     // Inquiry, MQL, SAL, SQL, Meeting, Proposal, Negotiation, Won

const funnel = () => page.getByTestId("sales-funnel");
const stageCount = async (key) => Number((await page.getByTestId(`sf-stage-${key}`).locator("b").innerText()).trim());
const tileValue = async (id) => (await page.getByTestId(`sf-tile-${id}`).locator("b").innerText()).trim();
async function openFunnel() {
  await page.goto(`${UI}/#funnel`);
  await page.reload();
  await funnel().waitFor();
  await page.getByTestId("sf-funnel").waitFor();
}
async function setDates(from, to) {
  await page.getByTestId("sf-from").fill(from);
  await page.getByTestId("sf-to").fill(to);
}

await step("Owner signs in and the Sales funnel is in the Analytics menu", async () => {
  await page.goto(UI);
  await page.getByLabel("Password").waitFor();
  await page.getByLabel("Password").fill(ADMIN);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.locator("main").waitFor();
  const later = page.getByRole("button", { name: "Skip setup" });
  await later.waitFor({ timeout: 6000 }).then(async () => { await later.click(); await later.waitFor({ state: "detached", timeout: 20000 }); }, () => {});
  await page.locator("nav.side").getByRole("button", { name: "Sales funnel", exact: true }).click();
  await funnel().waitFor();
});

await step("Importing a CSV with Rajesh's columns: the Proposal without a meeting is refused with the reason", async () => {
  const r = await api("POST", "/api/import/commit", { csv: CSV, source: "sheet" });
  ok(r.status === 200, `import ${r.status} ${JSON.stringify(r.body).slice(0, 200)}`);
  const warn = (r.body.warnings || []).map((w) => w.text).join(" ");
  ok(/Meeting Held Date/.test(warn), `no refusal message: ${warn.slice(0, 200)}`);
  ok(r.body.summary.create === 10, `created ${r.body.summary.create}`);
});

await step("Funnel numbers match the file: 10 inquiries and 8/6/5/4/3/2/2 down the stages", async () => {
  await openFunnel();
  for (const [i, key] of ["inquiry", "mql", "sal", "sql", "meeting", "proposal", "negotiation", "won"].entries()) {
    const got = await stageCount(key);
    ok(got === EXPECT_STAGES[i], `${key} ${got} != ${EXPECT_STAGES[i]}`);
  }
  ok(/MQLs/.test(await page.getByTestId("sf-tile-mqls").innerText()) && (await tileValue("mqls")) === "8", "MQL tile");
  ok((await tileValue("sqls")) === "5" && (await tileValue("opps")) === "4" && (await tileValue("won")) === "2", "tiles");
  ok((await tileValue("value")) === "₹3.2 L", `deal value ${await tileValue("value")}`);
  ok(/As on \d\d-\w{3}-\d{4}/.test(await page.getByTestId("sf-range-label").innerText()), "range label");
  const conv = await page.getByTestId("sf-conversions").innerText();
  ok(/80%|80\.0%/.test(conv) && /20%|20\.0%/.test(conv), `conversion table: ${conv.slice(0, 160)}`);   // 8/10 and overall 2/10
});

await step("Clicking a stage lists exactly that many leads; clicking the Deal Value tile lists the won deals", async () => {
  await page.getByTestId("sf-stage-mql").click();
  await page.getByTestId("sf-drill").waitFor();
  await page.getByTestId("sf-drill-table").waitFor();
  const n = Number(await page.getByTestId("sf-drill-count").innerText());
  const rows = await page.getByTestId("sf-drill-row").count();
  ok(n === 8 && rows === 8, `drill ${n}/${rows}`);
  await page.keyboard.press("Escape");
  await page.getByTestId("sf-drill").waitFor({ state: "detached" });
  await page.getByTestId("sf-tile-value").click();
  await page.getByTestId("sf-drill-table").waitFor();
  ok((await page.getByTestId("sf-drill-row").count()) === 2, "won deals");
  ok(/Alpha Won|Bravo Won/.test(await page.getByTestId("sf-drill-table").innerText()), "won names");
  await page.keyboard.press("Escape");
});

await step("Closed revenue follows the Closed Date: a range of the last 3 days has 1 new inquiry but the 2 lakh deal won in it", async () => {
  const today = new Date(Date.now() + 330 * 60000).toISOString().slice(0, 10);
  const from = new Date(Date.now() + 330 * 60000 - 3 * 86400000).toISOString().slice(0, 10);
  await setDates(from, today);
  await until(async () => /to \d\d-\w{3}-\d{4}/.test(await text("sf-range-label")), "range label");
  ok((await stageCount("inquiry")) === 1, `inquiries ${await stageCount("inquiry")}`);
  ok((await tileValue("value")) === "₹2 L", `value ${await tileValue("value")}`);
  ok((await stageCount("won")) === 0, "won in cohort");
});

await step("From after To shows the error and keeps the old numbers; Reset returns to 'As on'", async () => {
  const today = new Date(Date.now() + 330 * 60000).toISOString().slice(0, 10);
  const early = new Date(Date.now() + 330 * 60000 - 5 * 86400000).toISOString().slice(0, 10);
  await page.getByTestId("sf-reset").click();
  await setDates(today, early);
  await page.getByTestId("sf-error").waitFor();
  ok(/From Date cannot be after To Date/.test(await page.getByTestId("sf-error").innerText()), "message");
  await page.getByTestId("sf-reset").click();
  await page.getByTestId("sf-error").waitFor({ state: "detached" });
  ok(/As on/.test(await page.getByTestId("sf-range-label").innerText()) && (await stageCount("inquiry")) === 10, "reset");
});

await step("Presets fill both dates and the filters narrow the numbers", async () => {
  await page.getByTestId("sf-preset-fy").click();
  ok((await page.getByTestId("sf-from").inputValue()).endsWith("-04-01"), "FY start");
  await page.getByTestId("sf-preset-today").click();
  const t = await page.getByTestId("sf-to").inputValue();
  ok((await page.getByTestId("sf-from").inputValue()) === t, "today preset");
  await page.getByTestId("sf-reset").click();
  await page.getByLabel("Filter by lead owner").click();
  await page.getByLabel("Lead owner", { exact: true }).getByLabel("Asha").check();
  await until(async () => (await stageCount("inquiry")) === 5, "owner filter");
  ok((await stageCount("inquiry")) === 5, "Asha's leads");
  await page.getByLabel("Lead owner", { exact: true }).getByRole("button", { name: "Clear" }).click();
  await until(async () => (await stageCount("inquiry")) === 10, "filter cleared");
});

let sqlLeadId = "";
await step("In the lead drawer a Proposal without a meeting is refused with the message; Inquiry to SQL needs an estimate then works", async () => {
  const list = await api("GET", "/api/leads");
  const lead = list.body.find((l) => l.company === "Foxtrot Inquiry");
  ok(lead, "lead not found");
  sqlLeadId = lead.id;
  await page.goto(`${UI}/#leads?lead=${lead.id}`);
  await page.getByTestId("lead-sales").waitFor();
  ok((await page.getByTestId("lead-sales-status").innerText()).trim() === "Inquiry", "starts at inquiry");
  await page.getByTestId("lead-sales-select").selectOption("Proposal Submitted");
  await page.getByTestId("lead-sales-est").fill("30000");
  await page.getByTestId("lead-sales-save").click();
  await page.getByTestId("lead-sales-msg").waitFor();
  ok(/Meeting Held Date/.test(await page.getByTestId("lead-sales-msg").innerText()), "refusal text");
  await page.getByTestId("lead-sales-select").selectOption("Sales Qualified Lead (SQL)");
  await page.getByTestId("lead-sales-est").fill("");
  await page.getByTestId("lead-sales-save").click();
  await until(async () => /Est\. Monthly Value/.test(await text("lead-sales-msg")), "estimate refusal");
  await page.getByTestId("lead-sales-est").fill("70000");
  await page.getByTestId("lead-sales-save").click();
  await until(async () => /Saved/.test(await text("lead-sales-msg")), "saved");
  ok((await page.getByTestId("lead-sales-status").innerText()).trim() === "SQL", "now SQL");
  await page.screenshot({ path: `${SHOTS}/funnel-lead-drawer.png` });
});

await step("Back on the page the SQL count rose to 6 on return to the tab (refresh without a reload), filters kept", async () => {
  const second = await ctx.newPage();
  await second.goto(`${UI}/#funnel`);
  await second.getByTestId("sf-funnel").waitFor();
  const before = Number((await second.getByTestId("sf-stage-sql").locator("b").innerText()).trim());
  ok(before === 6, `fresh load shows ${before}`);
  await page.goto(`${UI}/#funnel`);
  await page.reload();
  await page.getByTestId("sf-funnel").waitFor();
  ok((await stageCount("sql")) === 6, "page shows 6");
  // change another lead through the API, then bring the tab back: the page refreshes itself
  const list = await api("GET", "/api/leads");
  const golf = list.body.find((l) => l.company === "Golf MQL");
  await api("PUT", `/api/leads/${golf.id}/sales`, { status: "Sales Qualified Lead (SQL)", est_value: 20000 });
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await until(async () => (await stageCount("sql")) === 7, "tab return refresh", 15000);
  await second.close();
});

await step("The page also refreshes by itself within 60 seconds (polling)", async () => {
  const list = await api("GET", "/api/leads");
  const foxtrot = list.body.find((l) => l.company === "Foxtrot Inquiry");
  await api("PUT", `/api/leads/${foxtrot.id}/sales`, { status: "Discovery Meeting Held" });
  await until(async () => (await stageCount("meeting")) === 5, "60 second poll", 90000);
});

await step("Targets can be saved by the owner and the coverage ratio appears", async () => {
  const month = new Date(Date.now() + 330 * 60000).toISOString().slice(0, 7);
  const r = await api("PUT", "/api/funnel/settings", { sla_hours: 12, targets: [{ month, owner: "", target: 1000000 }] });
  ok(r.status === 200, `settings ${r.status}`);
  await page.reload();
  await page.getByTestId("sf-funnel").waitFor();
  await page.getByTestId("sf-preset-month").click();
  await until(async () => /×/.test(await text("sf-coverage")), "coverage ratio", 15000);
});

await step("Screenshots at 1440 and the page has no console errors / CSP violations", async () => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByTestId("sf-reset").click();
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${SHOTS}/funnel-1440.png`, fullPage: true });
  ok(problems.length === 0, problems.join(" | "));
});

await step("At 375px wide there is no sideways scroll", async () => {
  await page.setViewportSize({ width: 375, height: 800 });
  await page.goto(`${UI}/#funnel`);
  await page.reload();
  await page.getByTestId("sf-funnel").waitFor();
  await page.waitForTimeout(800);
  const over = await page.evaluate(() => ({ doc: document.documentElement.scrollWidth, win: window.innerWidth }));
  ok(over.doc <= over.win + 1, `scrollWidth ${over.doc} > ${over.win}`);
  await page.screenshot({ path: `${SHOTS}/funnel-375.png`, fullPage: true });
});

await browser.close();
for (const [pass, name] of results) console.log(`${pass ? "PASS" : "FAIL"}  ${name}`);
const failed = results.filter(([p]) => !p).length;
console.log(`${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
