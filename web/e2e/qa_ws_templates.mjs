// QA (area "workspaces"): templates per workspace, done through the web UI as a workspace admin would.
//   API: fresh OUTREACH_DATA_FILE, OUTREACH_ADMIN_PASSWORD=qa-admin-pass-1, OUTREACH_EMAIL_DOMAIN_CHECK=0 (made-up domains)
//   UI:  npx vite build --outDir <temp>/dist, served by e2e/static_proxy.mjs with /api proxied to the API
//   cd web && E2E_UI=http://localhost:5293 node e2e/qa_ws_templates.mjs
// Covers: the owner adds a workspace and a workspace admin; the admin sees main's sequence read-only with "Copy into my
// workspace", copies and saves its own; opens the LinkedIn page (its workspace's leads and steps, no owner-only parts)
// and copies main's LinkedIn steps in; makes a flow of its own and sees main's flows read-only; the owner sees each
// template's workspace; no refused (403) call on any page the admin opens.
import { chromium } from "playwright";

const UI = process.env.E2E_UI || "http://localhost:5293";
const ADMIN = process.env.E2E_PASSWORD || "qa-admin-pass-1";
const SHOTS = process.env.SHOT_DIR || "/tmp";
const MAIN_CSV = ["company,contact_name,title,email,phone,website,linkedin_url",
  "Sharma Logistics Pvt Ltd,Rohit Sharma,Head of HR,rohit.sharma@sharmalogistics.example,+91 98110 23456,https://sharmalogistics.example,https://www.linkedin.com/in/rohit-sharma-qa"].join("\n");
const CLIENT_CSV = ["company,contact_name,title,email,phone,linkedin_url",
  "Nandi Foods,Arun Gowda,HR Lead,arun@nandifoods.example,9900112233,https://www.linkedin.com/in/arun-gowda-qa"].join("\n");
const results = [];
const browser = await chromium.launch();

async function session() {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on("response", (r) => { if (r.url().includes("/api/") && r.status() >= 400) errors.push(`${r.status()} ${r.url().replace(UI, "")}`); });
  page.on("pageerror", (e) => errors.push(`pageerror ${e.message}`));
  page.on("dialog", (d) => d.accept());
  return { page, errors };
}
async function signIn(p, email, password) {
  await p.goto(UI);
  await p.getByLabel("Password").waitFor();
  if (email) await p.getByLabel("Work email").fill(email);
  await p.getByLabel("Password").fill(password);
  await p.getByRole("button", { name: "Sign in" }).click();
  await p.locator("main").waitFor();
  const later = p.getByRole("button", { name: "Skip setup" });
  await later.waitFor({ timeout: 6000 }).then(async () => { await later.click(); await later.waitFor({ state: "detached", timeout: 20000 }); }, () => {});
}
const nav = async (p, label) => { await p.locator("nav.side").getByRole("button", { name: label, exact: true }).click(); await p.waitForTimeout(900); };
async function step(name, fn) {
  try { await fn(); results.push([true, name]); } catch (e) { results.push([false, `${name} — ${e.message.split("\n")[0]}`]); }
}
// Seed through the API the page itself uses (same token), for data the UI makes elsewhere (covered by other QA files).
const callApi = (p, path, method = "GET", body, workspace = "") => p.evaluate(async ([path, method, body, workspace]) => {
  const token = sessionStorage.getItem("outreach-token");
  const r = await fetch(path, { method, headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`, ...(workspace ? { "X-Workspace": workspace } : {}) },
    body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, json: await r.json().catch(() => null) };
}, [path, method, body, workspace]);

let ws = "";
const owner = await session();
await step("Owner adds a workspace, leads in main and in it, a main sequence and a workspace admin", async () => {
  const p = owner.page;
  await signIn(p, "", ADMIN);
  await nav(p, "Setup");
  await p.evaluate(() => { window.location.hash = "#setup/workspaces"; }); await p.waitForTimeout(900);
  await p.getByTestId("create-workspace").click();
  await p.getByTestId("ws-name-dialog").getByLabel("Workspace name").fill("Client Foods");
  await p.getByTestId("ws-name-dialog").getByRole("button", { name: "Create workspace" }).click();
  await p.getByText("Workspace “Client Foods” added.").waitFor();
  ws = (await callApi(p, "/api/workspaces")).json.find((w) => w.name === "Client Foods").id;
  await callApi(p, "/api/leads/import", "POST", { csv: MAIN_CSV, source: "upload" });
  await callApi(p, "/api/leads/import", "POST", { csv: CLIENT_CSV, source: "upload" }, ws);
  const body = "{{greeting}}\n\nA short note for {{company}}.\n\n{{sender}}\n\nUnsubscribe: {{unsub_link}}";
  const made = await callApi(p, "/api/sequences", "POST", { name: "Main welcome", steps: [{ day: 0, name: "Email 1", subject: "Hello {{company}}", body }] });
  if (made.status !== 200) throw new Error(`main sequence: ${made.status}`);
  await p.reload(); await p.locator("main").waitFor(); await p.waitForTimeout(1200);
  await nav(p, "Setup");
  await p.evaluate(() => { window.location.hash = "#setup/members"; }); await p.waitForTimeout(900);
  await p.getByTestId("add-member").click();
  const d = p.getByTestId("add-member-dialog");
  await d.getByLabel("Work email").fill("boss@client.example");
  await d.getByLabel("Name").fill("Client Boss");
  await d.getByLabel("Starting password").fill("long-password-2");
  await d.getByLabel("Role").selectOption("admin");
  await d.getByTestId("new-user-workspaces").getByLabel("Client Foods").check();
  await d.getByTestId("dialog-submit").click();
  await p.getByText("Login added for boss@client.example.").waitFor();
});

const admin = await session();
await step("Workspace admin sees main's sequences read-only and copies one into its workspace", async () => {
  const p = admin.page;
  await signIn(p, "boss@client.example", "long-password-2");
  await nav(p, "Sequences & templates");
  const mainCard = p.locator(".card", { hasText: "Main welcome" }).first();
  await mainCard.waitFor();
  if (!(await mainCard.getByTestId("ws-tag").innerText()).includes("Main · read-only")) throw new Error("no read-only tag on main's sequence");
  if (await mainCard.getByRole("button", { name: "Edit" }).count()) throw new Error("admin may edit main's sequence");
  if (await mainCard.getByRole("button", { name: "Archive" }).count()) throw new Error("admin may archive main's sequence");
  await mainCard.getByRole("button", { name: "Copy into my workspace" }).click();
  await p.getByRole("button", { name: "Save sequence" }).waitFor();
  await p.locator("main").getByLabel("Name").first().fill("Client welcome");
  await p.getByRole("button", { name: "Save sequence" }).click();
  await p.getByText("Saved.").waitFor();
  await p.getByRole("button", { name: "Back to list" }).click();
  const mine = p.locator(".card", { hasText: "Client welcome" }).first();
  await mine.getByRole("button", { name: "Edit" }).waitFor();
  if (!(await mine.getByTestId("ws-tag").innerText()).includes("Client Foods")) throw new Error("copy is not tagged Client Foods");
  await mine.getByRole("button", { name: "Make default" }).click();
  await mine.getByText("Default", { exact: true }).waitFor();
  await p.screenshot({ path: `${SHOTS}/ws_templates_admin_sequences.png`, fullPage: true });
});
await step("Workspace admin adds and edits a snippet of its own; main's snippets stay read-only", async () => {
  const p = admin.page;
  await p.getByRole("tab", { name: "Snippet library" }).click();
  await p.getByRole("button", { name: "New snippet" }).click();
  await p.locator("form.inline-form").getByLabel("Name").fill("Client hello");
  await p.locator("form.inline-form").getByLabel("Text").fill("Hi {{first_name}}, a quick hello from us.");
  await p.getByRole("button", { name: "Save snippet" }).click();
  const card = p.locator(".card", { hasText: "Client hello" }).first();
  await card.getByRole("button", { name: "Edit" }).waitFor();
});
await step("Workspace admin opens LinkedIn: only its lead, no owner-only parts, copies main's steps in", async () => {
  const p = admin.page;
  await nav(p, "LinkedIn outreach");
  await p.getByText("Arun Gowda").first().waitFor();
  const text = await p.locator("main").innerText();
  if (/Rohit|Sharma/.test(text)) throw new Error("admin sees a main lead on the LinkedIn page");
  if (/LinkedIn agent|Cloud accounts|Office runner/i.test(text.split("To send today")[0]) && /Switch on the agent/i.test(text)) throw new Error("owner-only agent controls shown");
  await p.getByTestId("li-sequence-editor").locator("summary").click();
  await p.getByTestId("li-copy").click();
  await p.getByText("Copied into your workspace.").waitFor();
  await p.getByRole("button", { name: "Save LinkedIn steps" }).waitFor();
  await p.screenshot({ path: `${SHOTS}/ws_templates_admin_linkedin.png`, fullPage: true });
});
await step("Workspace admin makes its own flow; main's flows are view-only", async () => {
  const p = admin.page;
  await callApi(owner.page, "/api/flows", "POST", { name: "Main nurture" });
  await nav(p, "Flow builder");
  await p.getByRole("button", { name: "Blank flow" }).click();
  await p.getByRole("button", { name: "Validate" }).waitFor();
  await p.getByRole("button", { name: "← All flows" }).click();
  const mainRow = p.locator("tr", { hasText: "Main nurture" });
  await mainRow.waitFor();
  if (await mainRow.getByRole("button", { name: /Delete/ }).count()) throw new Error("admin may delete main's flow");
  await mainRow.getByRole("button", { name: /Open/ }).click();
  await p.getByText(/View only/).waitFor();
});
await step("No refused (403) or failed call for the workspace admin", async () => {
  const bad = admin.errors.filter((e) => !/^404 /.test(e));
  if (bad.length) throw new Error(bad.join(", "));
});
await step("Owner sees each sequence's workspace", async () => {
  const p = owner.page;
  await nav(p, "Sequences & templates");
  const mine = p.locator(".card", { hasText: "Client welcome" }).first();
  if (!(await mine.getByTestId("ws-tag").innerText()).includes("Client Foods")) throw new Error("owner does not see the workspace");
  const main = p.locator(".card", { hasText: "Main welcome" }).first();
  if (!(await main.getByTestId("ws-tag").innerText()).includes("Main")) throw new Error("owner does not see Main");
  await main.getByRole("button", { name: "Edit" }).waitFor();
});

await browser.close();
for (const [ok, name] of results) console.log(`${ok ? "PASS" : "FAIL"} ${name}`);
console.log(`owner errors: ${JSON.stringify(owner.errors)}\nadmin errors: ${JSON.stringify(admin.errors)}`);
process.exit(results.every(([ok]) => ok) ? 0 : 1);
