// QA (area "lead ownership and assignment"), through the web UI; logins and the team are created through the API.
//   cd web && E2E_UI=http://localhost:5284 node e2e/qa_ownership.mjs      (same rig and admin password as qa_linkedin_plan.mjs)
// Covers: admin makes a team and a rule; a member uploads a file and gets their own list; a second member cannot see it
// and cannot start email on it; the admin sees every list, assigns and splits one; the ownership report and switch work;
// the waiting-for-first-touch chip and the lead drawer's "Assigned by" line show. Screenshots go to SHOT_DIR.
import { chromium } from "playwright";

const UI = process.env.E2E_UI || "http://localhost:5284";
const ADMIN = process.env.E2E_PASSWORD || "qa-admin-pass-1";
const SHOTS = process.env.SHOT_DIR || "/tmp";
const PW = "qa-member-pass-12345";
const stamp = Date.now().toString(36);
const A = { name: `Asha QA ${stamp}`, email: `asha.${stamp}@example.com` };
const B = { name: `Ravi QA ${stamp}`, email: `ravi.${stamp}@example.com` };
const CSV = ["company,contact_name,title,email,phone,website",
  `Own Logistics ${stamp} Pvt Ltd,Rohit Sharma,Head of HR,rohit.${stamp}@ownlogistics.example,+91 98110 23456,https://ownlogistics.example`].join("\n");
const results = [];
const errors = [];
const browser = await chromium.launch();

async function step(name, fn) {
  try { await fn(); results.push([true, name]); } catch (e) { results.push([false, `${name} — ${e.message.split("\n")[0]}`]); }
}
async function api(token, method, path, body) {
  const r = await fetch(`${UI}${path}`, { method, headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` }, body: body ? JSON.stringify(body) : undefined });
  const text = await r.text();
  let json = {}; try { json = JSON.parse(text); } catch { /* not json */ }
  return { status: r.status, json };
}
async function login(creds) {
  const r = await fetch(`${UI}/api/login`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(creds) });
  const j = await r.json();
  if (!j.token) throw new Error(`login failed for ${creds.email || "admin"}`);
  return j.token;
}
async function open(token) {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
  await ctx.addInitScript((t) => sessionStorage.setItem("outreach-token", t), token);
  const p = await ctx.newPage();
  p.on("response", (r) => { if (r.url().includes("/api/") && r.status() >= 500) errors.push(`${r.status()} ${r.url().replace(UI, "")}`); });
  p.on("pageerror", (e) => errors.push(`pageerror ${e.message}`));
  await p.goto(UI); await p.locator("main").waitFor();
  const later = p.getByRole("button", { name: "Skip setup" });
  await later.waitFor({ timeout: 4000 }).then(async () => { await later.click(); }, () => {});
  return p;
}
const hash = async (p, h) => { await p.evaluate((x) => { location.hash = x; }, h); await p.waitForTimeout(1200); };
const shot = (p, n) => p.screenshot({ path: `${SHOTS}/own_${n}.png`, fullPage: true });

let adminToken, tokA, tokB, admin, pa, pb;

await step("Admin signs in and creates two callers", async () => {
  adminToken = await login({ password: ADMIN });
  for (const u of [A, B]) {
    const r = await api(adminToken, "POST", "/api/users", { name: u.name, email: u.email, role: "caller", password: PW });
    if (r.status !== 200) throw new Error(`create ${u.email}: ${r.status}`);
  }
  tokA = await login({ email: A.email, password: PW });
  tokB = await login({ email: B.email, password: PW });
});
await step("Admin builds a team and a rule in Setup > Assignment", async () => {
  admin = await open(adminToken);
  await hash(admin, "setup/assignment");
  await admin.getByTestId("setup-section-title").waitFor();
  await admin.getByRole("button", { name: "Add team" }).click();
  await admin.getByPlaceholder("Team name").fill(`QA team ${stamp}`);
  await admin.getByRole("checkbox", { name: A.name }).check();
  await admin.getByRole("checkbox", { name: B.name }).check();
  await admin.getByRole("button", { name: "Add rule" }).click();
  await admin.locator("[data-testid=rule-card] input").nth(1).fill("Noida");        // City
  await admin.getByTestId("assignment-on").check();
  await admin.getByTestId("assignment-save").click();
  await admin.getByText("Saved.").waitFor();
  await shot(admin, "assignment");
  const cfg = (await api(adminToken, "GET", "/api/assignment")).json;
  const t = (cfg.teams || []).find((x) => x.name === `QA team ${stamp}`);
  if (!t || t.members.length !== 2) throw new Error("team not saved with two members");
  if (!(cfg.rules || []).some((r) => r.team === t.id && r.when?.city === "Noida")) throw new Error("rule not saved");
});
await step("Rules can be moved up and down", async () => {
  await admin.getByRole("button", { name: "Add rule" }).click();
  await admin.getByRole("button", { name: "Move rule 2 up" }).click();
  await admin.getByRole("button", { name: "Move rule 1 down" }).waitFor();
});
await step("Member A uploads a file and the list is theirs", async () => {
  pa = await open(tokA);
  await hash(pa, "leads");
  await pa.getByRole("button", { name: "Quick paste" }).click();
  await pa.locator("textarea").first().fill(CSV);
  await pa.getByRole("button", { name: "Import", exact: true }).click();
  await pa.getByText("Added 1").waitFor();
  await hash(pa, "lists");
  await pa.getByRole("heading", { name: "My lists" }).waitFor();
  await shot(pa, "member_a_lists");
  const lists = (await api(tokA, "GET", "/api/lists")).json;
  if (!lists.length || !lists.every((l) => l.assigned_to === A.name)) throw new Error(`A's lists: ${JSON.stringify(lists.map((l) => l.assigned_to))}`);
});
await step("Member B cannot see A's list or lead", async () => {
  pb = await open(tokB);
  await hash(pb, "lists");
  const lists = (await api(tokB, "GET", "/api/lists")).json;
  if (lists.some((l) => l.assigned_to === A.name)) throw new Error("B can read A's list over the API");
  await hash(pb, "leads");
  if ((await pb.getByText(`Own Logistics ${stamp}`).count()) > 0) throw new Error("B sees A's lead");
});
await step("Starting email on A's list is refused for B", async () => {
  const aList = (await api(tokA, "GET", "/api/lists")).json[0];
  const r = await api(tokB, "POST", `/api/lists/${aList.id}/start-email`, { lead_ids: null });
  if (r.status < 400) throw new Error(`expected a refusal, got ${r.status}`);
});
await step("Admin sees every list with its assignee", async () => {
  await hash(admin, "lists");
  await admin.getByTestId("lists-all").click();
  await admin.getByTestId("list-assignee").filter({ hasText: A.name }).first().waitFor();
  await shot(admin, "admin_lists");
});
await step("Admin assigns A's list to B, then splits", async () => {
  await admin.getByRole("button", { name: /^Assign / }).first().click();
  await admin.getByRole("combobox", { name: "Give this list to" }).selectOption(B.name);
  await admin.getByRole("button", { name: `Assign to ${B.name}` }).click();
  await admin.getByTestId("assign-result").waitFor();
  await shot(admin, "assign_dialog");
  await admin.keyboard.press("Escape");
  const lists = (await api(tokB, "GET", "/api/lists")).json;
  if (!lists.some((l) => l.assigned_to === B.name)) throw new Error("B does not see the list after assignment");
  await admin.getByRole("button", { name: /^Split / }).first().click();
  await admin.getByTestId("split-dialog").getByRole("checkbox", { name: A.name }).check();
  await admin.getByTestId("split-dialog").getByRole("checkbox", { name: B.name }).check();
  await admin.getByRole("button", { name: /^Split between 2/ }).click();
  await admin.getByTestId("split-result").waitFor();
  await shot(admin, "split_dialog");
  await admin.keyboard.press("Escape");
});
await step("A list can be shared view-only", async () => {
  await admin.reload(); await admin.locator("main").waitFor(); await hash(admin, "lists");
  await admin.getByRole("button", { name: /^Share / }).first().click();
  await admin.getByTestId("share-dialog").waitFor();
  await shot(admin, "share_dialog");
  await admin.keyboard.press("Escape");
});
await step("Ownership report shows and the switch asks to acknowledge", async () => {
  await hash(admin, "setup/assignment");
  await admin.getByTestId("ownership-report").waitFor();
  await shot(admin, "ownership");
  const mode = (await api(adminToken, "GET", "/api/ownership-mode")).json;
  if (mode.unowned > 0 && !(await admin.getByTestId("ownership-toggle").isDisabled())) throw new Error("switch not blocked before acknowledging");
  if (mode.unowned > 0) await admin.getByTestId("ownership-ack").check();
  await admin.getByTestId("ownership-toggle").click();
  await admin.getByTestId("ownership-state").filter({ hasText: "On" }).waitFor();
  if (!(await api(adminToken, "GET", "/api/ownership-mode")).json.on) throw new Error("mode not on");
});
await step("Leads: Waiting for first touch chip and the drawer's Assigned by line", async () => {
  await hash(admin, "leads");
  const chip = admin.getByTestId("waiting-first-touch");
  await chip.waitFor();
  await chip.click();
  await shot(admin, "waiting");
  await chip.click();
  await admin.getByRole("button", { name: `Own Logistics ${stamp} Pvt Ltd` }).first().click();
  const dlg = admin.getByRole("dialog").first();
  await dlg.getByRole("tab", { name: "Outreach" }).click();
  await dlg.getByTestId("lead-assigned-by").waitFor();
  const text = await dlg.getByTestId("lead-assigned-by").innerText();
  if (!/Assigned by/.test(text)) throw new Error(`drawer line: ${text}`);
  await shot(admin, "drawer");
});
await step("Dashboard shows the first-touch card", async () => {
  await hash(admin, "dashboard");
  await admin.getByTestId("dash-assignment").waitFor({ timeout: 8000 });
  await shot(admin, "dashboard");
});
await step("Owner puts ownership mode back off", async () => {
  const r = await api(adminToken, "PUT", "/api/ownership-mode", { on: false, acknowledge: true });
  if (r.status !== 200) throw new Error(`status ${r.status}`);
});
await step("No server errors or page errors during the run", async () => {
  if (errors.length) throw new Error(errors.slice(0, 4).join("; "));
});

await browser.close();
for (const [ok, name] of results) console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
const failed = results.filter(([ok]) => !ok).length;
console.log(`${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
