// Guide screenshots: signs in to a SAMPLE-DATA rig and saves the key screens as PNG into SHOT_DIR (convert to WebP with
// tools/guide_webp.py). Never point it at live data.
//   rig: fresh OUTREACH_DATA_FILE, OUTREACH_ADMIN_PASSWORD=qa-admin-pass-1 OUTREACH_ADMIN_LOGIN=1 OUTREACH_EMAIL_DOMAIN_CHECK=0 OUTREACH_IGNORE_BUSINESS_HOURS=1
//   cd web && E2E_UI=http://localhost:5291 SHOT_DIR=/tmp/shots node e2e/guide_shots.mjs
import { chromium } from "playwright";

const UI = process.env.E2E_UI || "http://localhost:5291";
const ADMIN = process.env.E2E_PASSWORD || "qa-admin-pass-1";
const OUT = process.env.SHOT_DIR || "/tmp/guide-shots";
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
const page = await ctx.newPage();
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
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

const SHOTS = [
  // [file, hash, selector to wait for, optional action]
  ["myday", "#myday", "main h2"], ["dashboard", "#dashboard", "main h2"], ["prospects", "#prospects", "main h2"],
  ["leads-list", "#leads", "table"], ["campaigns", "#campaigns", "main h2"], ["replies", "#replies", "main h2"],
  ["calls", "#calls", "main h2"], ["linkedin", "#linkedin", "main h2"], ["outbox", "#outbox", "main h2"],
  ["templates", "#templates", "main h2"], ["flows", "#flows", "main h2"], ["analytics", "#analytics", "main h2"],
  ["funnel-top", "#funnel", "[data-testid=sf-funnel]"], ["reports", "#reports", "main h2"],
  ["setup-checklist", "#setup/checklist", "[data-testid=setup-section-title]"], ["setup-members", "#setup/members", "[data-testid=setup-section-title]"],
  ["security", "#setup/security", "[data-testid=setup-section-title]"], ["audit", "#setup/audit", "[data-testid=setup-section-title]"],
  ["integrations", "#integrations", "main h2"], ["glossary", "#help/glossary", "[data-testid=gloss-MQL]"],
  ["accounts", "#setup/accounts", "[data-testid=setup-section-title]"],
];

// sign-in page first (signed out)
await page.goto(UI);
await page.getByLabel("Password").waitFor();
await wait(800);
await page.screenshot({ path: `${OUT}/login.png` });
await page.getByLabel("Password").fill(ADMIN);
await page.getByRole("button", { name: "Sign in" }).click();
await page.locator("main").waitFor();
const skip = page.getByRole("button", { name: "Skip setup" });
await skip.waitFor({ timeout: 6000 }).then(async () => { await skip.click(); await skip.waitFor({ state: "detached" }); }, () => {});
const r = await api("POST", "/api/import/commit", { csv: CSV, source: "sheet" });
console.log("import", r.status, r.body.summary?.create);
for (const [file, hash, sel] of SHOTS) {
  await page.goto(`${UI}/${hash}`);
  await page.reload();
  await page.locator(sel).first().waitFor({ timeout: 20000 }).catch(() => console.log("no", sel));
  await wait(1200);
  await page.screenshot({ path: `${OUT}/${file}.png` });
  console.log("shot", file);
}
// the funnel further down
await page.goto(`${UI}/#funnel`); await page.reload();
await page.getByTestId("sf-pipeline").waitFor(); await page.getByTestId("sf-pipeline").scrollIntoViewIfNeeded(); await wait(800);
await page.screenshot({ path: `${OUT}/funnel-pipeline.png` });
// a lead's Sales stage panel
const leads = await api("GET", "/api/leads");
const lead = (leads.body.leads || leads.body).find((l) => /Charlie/.test(l.company));
await page.goto(`${UI}/#leads?lead=${encodeURIComponent(lead.id)}`); await page.reload();
await page.getByTestId("lead-sales").waitFor(); await page.getByTestId("lead-sales").scrollIntoViewIfNeeded(); await wait(800);
await page.screenshot({ path: `${OUT}/lead-sales.png` });
// the import wizard
await page.goto(`${UI}/#leads`); await page.reload();
await page.getByRole("button", { name: "Import leads" }).click(); await wait(1000);
await page.screenshot({ path: `${OUT}/import.png` });
// two-step set-up with its QR code: a sample team login (the server admin password cannot use two-step)
await api("POST", "/api/users", { email: "sample.caller@example.com", name: "Sample Caller", role: "caller", password: "sample-long-password-1" });
const p2 = await (await browser.newContext({ viewport: { width: 1280, height: 800 } })).newPage();
await p2.goto(UI);
await p2.getByLabel("Password").waitFor();
await p2.getByLabel("Work email").fill("sample.caller@example.com");
await p2.getByLabel("Password").fill("sample-long-password-1");
await p2.getByRole("button", { name: "Sign in" }).click();
await p2.locator("nav.side").waitFor();
await p2.goto(`${UI}/#setup/account`); await p2.reload();
await p2.getByRole("button", { name: "Set up two-step login" }).click();
await p2.getByTestId("two-step-qr").waitFor(); await wait(800);
await p2.screenshot({ path: `${OUT}/two-step.png` });
await browser.close();
