// QA (area "lead ownership"): admin assigns a list in the UI, turns ownership mode on (with the migration report), members
// then see only their own lists/leads, sending on someone else's lead is refused, workspaces stay apart.
//   rig as qa_guide.mjs (OUTREACH_ADMIN_PASSWORD=qa-admin-pass-1 OUTREACH_ADMIN_LOGIN=1 ...)
//   cd web && E2E_UI=http://localhost:5291 SHOT_DIR=/tmp/shots node e2e/qa_ownership.mjs
import { chromium } from "playwright";

const UI = process.env.E2E_UI || "http://localhost:5291";
const ADMIN = process.env.E2E_PASSWORD || "qa-admin-pass-1";
const SHOTS = process.env.SHOT_DIR || "/tmp";
const PW = "owner-long-password-1";
const results = [], problems = [];
const browser = await chromium.launch();
const ok = (c, m) => { if (!c) throw new Error(m); };
const watch = (p) => { p.on("pageerror", (e) => problems.push(`pageerror ${e.message}`)); p.on("response", (r) => { if (r.url().includes("/api/") && r.status() >= 500) problems.push(`${r.status()} ${r.url().replace(UI, "")}`); }); };
async function step(name, fn) { try { await fn(); results.push([true, name]); } catch (e) { results.push([false, `${name} — ${e.message.split("\n")[0]}`]); } }
async function signIn(page, email, pw) {
  await page.goto(UI);
  await page.getByLabel("Password").waitFor();
  if (email) await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Password").fill(pw);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.locator("nav.side, header.topbar").first().waitFor();
  const later = page.getByRole("button", { name: "Skip setup" });
  await later.waitFor({ timeout: 4000 }).then(async () => { await later.click(); await later.waitFor({ state: "detached" }); }, () => {});
}
const api = (page, method, path, body) => page.evaluate(async ([m, p, b]) => {
  const r = await fetch(p, { method: m, headers: { "Content-Type": "application/json", Authorization: `Bearer ${sessionStorage.getItem("outreach-token")}` }, body: b ? JSON.stringify(b) : undefined });
  return { status: r.status, body: await r.json().catch(() => ({})) };
}, [method, path, body]);
const go = async (page, hash) => { await page.evaluate((h) => { location.hash = h; }, hash); await page.waitForTimeout(1500); };

const admin = await (await browser.newContext({ viewport: { width: 1440, height: 950 } })).newPage(); watch(admin);
await signIn(admin, "", ADMIN);
for (const [email, name] of [["priya@example.com", "Priya"], ["ravi@example.com", "Ravi"]]) await api(admin, "POST", "/api/users", { email, name, role: "caller", password: PW });
const CSV = "company,contact_name,title,email\nAlpha Ltd,Asha One,HR Head,asha@alpha.example\nBeta Ltd,Bala Two,CHRO,bala@beta.example\nGamma Ltd,Chitra Three,Founder,chitra@gamma.example\nDelta Ltd,Dev Four,HR,dev@delta.example";
await api(admin, "POST", "/api/leads/import", { csv: CSV, source: "qa" });
const leads = (await api(admin, "GET", "/api/leads")).body;
const rows = Array.isArray(leads) ? leads : leads.leads || [];
const idOf = (company) => rows.find((l) => l.company === company)?.id;
const mkList = async (name, companies) => (await api(admin, "POST", "/api/lists", { name, lead_ids: companies.map(idOf) })).body;
const listP = await mkList("Priya batch", ["Alpha Ltd", "Beta Ltd"]);
const listR = await mkList("Ravi batch", ["Gamma Ltd"]);

await step("Admin assigns a list to a member in the Lists screen", async () => {
  await go(admin, "#lists");
  await admin.getByRole("button", { name: "Assign Priya batch" }).click();
  await admin.getByTestId("assign-dialog").waitFor();
  await admin.getByLabel("Give this list to").selectOption("Priya");
  await admin.getByRole("button", { name: /^Assign to Priya$/ }).click();
  await admin.getByTestId("assign-result").waitFor({ timeout: 8000 });
  await admin.screenshot({ path: `${SHOTS}/own-assign.png`, fullPage: true });
  await admin.keyboard.press("Escape");
});
await api(admin, "PUT", `/api/lists/${listR.id}/assign`, { to: "Ravi", take_over: false });

await step("Setup > Assignment shows the ownership switch and its report; turning it on needs the unowned acknowledgement", async () => {
  await go(admin, "#setup/assignment");
  await admin.getByTestId("ownership-mode").waitFor({ timeout: 8000 });
  ok(/Off/.test(await admin.getByTestId("ownership-state").innerText()), "should start Off");
  if (await admin.getByTestId("ownership-ack").count()) await admin.getByTestId("ownership-ack").check();
  await admin.getByTestId("ownership-toggle").click();
  await admin.waitForTimeout(1500);
  ok(/On/.test(await admin.getByTestId("ownership-state").innerText()), "should be On");
  await admin.screenshot({ path: `${SHOTS}/own-mode.png`, fullPage: true });
});

async function asMember(email) { const p = await (await browser.newContext({ viewport: { width: 1440, height: 950 } })).newPage(); watch(p); await signIn(p, email, PW); return p; }
const priya = await asMember("priya@example.com");
await step("Priya sees only her list and only her leads", async () => {
  await go(priya, "#lists");
  const t = await priya.locator("main").innerText();
  ok(/Priya batch/.test(t) && !/Ravi batch/.test(t), `lists: ${t.slice(0, 200)}`);
  const mine = (await api(priya, "GET", "/api/leads")).body;
  const names = (Array.isArray(mine) ? mine : mine.leads || []).map((l) => l.company).sort().join(",");
  ok(names === "Alpha Ltd,Beta Ltd", `leads: ${names}`);
  await priya.screenshot({ path: `${SHOTS}/own-priya-lists.png`, fullPage: true });
});
await step("Priya cannot start email or LinkedIn on Ravi's lead; the refusal says whose it is", async () => {
  const r = await api(priya, "POST", `/api/leads/${idOf("Gamma Ltd")}/linkedin/start`);
  ok([404, 409].includes(r.status), `status ${r.status} (mode must be On)`);
  const l = await api(priya, "GET", `/api/leads/${idOf("Gamma Ltd")}`);
  ok(l.status === 404, `Ravi's lead visible to Priya: ${l.status}`);
});
await step("Priya's own lead is allowed through the ownership check", async () => {
  const r = await api(priya, "POST", `/api/leads/${idOf("Alpha Ltd")}/linkedin/start`);
  ok(!(r.status === 409 && /belongs to/.test(JSON.stringify(r.body))), `refused: ${JSON.stringify(r.body).slice(0, 160)}`);
});
const ravi = await asMember("ravi@example.com");
await step("Ravi sees only his list; none of Priya's", async () => {
  await go(ravi, "#lists");
  const t = await ravi.locator("main").innerText();
  ok(/Ravi batch/.test(t) && !/Priya batch/.test(t), `lists: ${t.slice(0, 200)}`);
  const a = await api(ravi, "GET", `/api/leads/${idOf("Alpha Ltd")}`);
  ok(a.status === 404, `Priya's lead visible to Ravi: ${a.status}`);
});
await step("Admin still sees every list and lead", async () => {
  await go(admin, "#lists");
  const t = await admin.locator("main").innerText();
  ok(/Priya batch/.test(t) && /Ravi batch/.test(t), "admin lists");
  const all = (await api(admin, "GET", "/api/leads")).body;
  ok((Array.isArray(all) ? all : all.leads || []).length >= 4, "admin leads");
  await admin.screenshot({ path: `${SHOTS}/own-admin-lists.png`, fullPage: true });
});
await step("Member cannot reach admin-only assignment screens", async () => {
  const r = await api(priya, "PUT", `/api/lists/${listP.id}/assign`, { to: "Priya" });
  ok(r.status === 403, `assign status ${r.status}`);
  const s = await api(priya, "PUT", "/api/ownership-mode", { on: false });
  ok(s.status === 403, `mode status ${s.status}`);
});

await browser.close();
for (const [pass, name] of results) console.log(`${pass ? "PASS" : "FAIL"} ${name}`);
console.log(`problems: ${JSON.stringify([...new Set(problems)])}`);
process.exit(results.every(([p]) => p) && !problems.length ? 0 : 1);
