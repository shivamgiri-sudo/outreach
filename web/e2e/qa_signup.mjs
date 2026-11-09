// QA (area "signup"): self sign-up through the web UI, the email link, the waiting page, super-admin approval, then work.
//   API: a fresh OUTREACH_DATA_FILE, OUTREACH_ADMIN_PASSWORD=qa-admin-pass-1, OUTREACH_ADMIN_LOGIN=1,
//        OUTREACH_DEV_OUTBOX=<file> (the verification email is read from there), OUTREACH_SUPER_ADMIN=<any test address>
//   UI:  vite build, served by e2e/static_proxy.mjs.   cd web && E2E_UI=http://localhost:5291 DEV_OUTBOX=<file> node e2e/qa_signup.mjs
import fs from "node:fs";
import { chromium } from "playwright";

const UI = process.env.E2E_UI || "http://localhost:5291";
const ADMIN = process.env.E2E_PASSWORD || "qa-admin-pass-1";
const OUTBOX = process.env.DEV_OUTBOX;
const SHOTS = process.env.SHOT_DIR || "/tmp";
const EMAIL = "dev@deltafreight.example";
const results = [];
const browser = await chromium.launch();

async function session() {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on("response", (r) => { if (r.url().includes("/api/") && r.status() >= 400) errors.push(`${r.status()} ${r.url().replace(UI, "")}`); });
  page.on("pageerror", (e) => errors.push(`pageerror ${e.message}`));
  return { page, errors };
}
async function step(name, fn) {
  try { await fn(); results.push([true, name]); } catch (e) { results.push([false, `${name} — ${e.message.split("\n")[0]}`]); }
}
const nav = async (p, label) => { await p.locator("nav.side").getByRole("button", { name: label, exact: true }).click(); await p.waitForTimeout(900); };
function verifyLink() {
  const rows = fs.readFileSync(OUTBOX, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l)).filter((r) => r.to === EMAIL);
  const m = /#verify\?token=([A-Za-z0-9_.-]+)/.exec(rows.at(-1)?.body || "");
  if (!m) throw new Error("no verification email in the dev outbox");
  return `${UI}/#verify?token=${m[1]}`;
}

const client = await session();
await step("Sign-up form from the sign-in page: 'Create an account' -> check your inbox", async () => {
  const p = client.page;
  await p.goto(UI);
  await p.getByTestId("signup-link").click();
  const f = p.getByTestId("signup-form");
  await f.getByLabel("Company name").fill("Delta Freight");
  await f.getByLabel("Your name").fill("Dev Patel");
  await f.getByLabel("Work email").fill(EMAIL);
  await f.getByLabel("Password").fill("delta-long-password-1");
  await f.getByLabel("Phone").fill("+91 98100 11111");
  await f.getByTestId("signup-accept").check();
  await p.waitForTimeout(4500);                                    // a person takes a few seconds (minimum fill time)
  await f.getByRole("button", { name: "Create account" }).click();
  await p.getByTestId("signup-sent").waitFor();
});
await step("The emailed link confirms the address and opens the waiting page", async () => {
  const p = client.page;
  await p.goto("about:blank");                                    // the link opens like a click in the mail app (a fresh page load)
  await p.goto(verifyLink());
  await p.getByTestId("pending-page").waitFor({ timeout: 15000 });
  const text = await p.locator("body").innerText();
  if (!/Waiting for approval/.test(text) || !/Delta Freight/.test(text)) throw new Error(text.slice(0, 200));
  const refused = await p.evaluate(async () => (await fetch("/api/leads", { headers: { Authorization: `Bearer ${sessionStorage.getItem("outreach-token")}` } })).json());
  if (refused.detail !== "account pending approval") throw new Error(JSON.stringify(refused));
  await p.screenshot({ path: `${SHOTS}/signup-pending.png` });
});

const sup = await session();
await step("Super admin sees the sign-up in the bell and approves it in Setup > Accounts", async () => {
  const p = sup.page;
  await p.goto(UI);
  await p.getByLabel("Password").fill(ADMIN);
  await p.getByRole("button", { name: "Sign in" }).click();
  await p.locator("main").waitFor();
  const later = p.getByRole("button", { name: "Skip setup" });
  await later.waitFor({ timeout: 6000 }).then(async () => { await later.click(); }, () => {});
  await nav(p, "Setup");
  await p.getByTestId("setup-nav-accounts").click();
  const q = p.getByTestId("pending-queue");
  await q.getByTestId("pending-Delta Freight").waitFor();
  await q.getByRole("button", { name: "Approve Delta Freight" }).click();
  await p.getByText("“Delta Freight” approved.").waitFor();
  const notes = await p.evaluate(async () => (await fetch("/api/notifications", { headers: { Authorization: `Bearer ${sessionStorage.getItem("outreach-token")}` } })).json());
  if (!JSON.stringify(notes).includes("New sign-up: Delta Freight")) throw new Error("no bell entry for the sign-up");
});
await step("The approved client admin works: imports a lead into its own workspace", async () => {
  const p = client.page;
  await p.reload();
  await p.locator("nav.side").waitFor({ timeout: 15000 });
  await nav(p, "Leads");
  await p.getByRole("button", { name: "Quick paste" }).click();
  await p.locator("textarea").first().fill("company,contact_name,email\nZed Co,Zed Kumar,zed@zedco.example");
  await p.getByRole("button", { name: "Import", exact: true }).click();
  await p.getByText("Added 1").waitFor();
  await p.screenshot({ path: `${SHOTS}/signup-approved.png` });
});

await browser.close();
for (const [ok, name] of results) console.log(`${ok ? "PASS" : "FAIL"} ${name}`);
console.log(`client errors: ${JSON.stringify(client.errors.filter((e) => !e.includes("/api/leads")))}\nsuper errors: ${JSON.stringify(sup.errors)}`);
process.exit(results.every(([ok]) => ok) ? 0 : 1);
