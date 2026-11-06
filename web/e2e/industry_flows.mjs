// Industry flows through the real flow-builder UI: a real-estate flow and a clinic flow are built by clicking, dragging
// and filling the step settings at 1400px, saved, activated, a lead is enrolled, the engine ticks, and the lead panel
// must show the path taken. Then the builder must open as a list at 375px with no sideways scroll. No console errors.
//
//   E2E_API=http://127.0.0.1:8064 E2E_UI=http://127.0.0.1:5264 node e2e/industry_flows.mjs
//
// Expects the API (practice mode, OUTREACH_IGNORE_BUSINESS_HOURS=1, a throwaway OUTREACH_DATA_FILE) and a built UI
// served with an /api proxy (e2e/static_proxy.mjs) to be running already. Nothing is ever sent for real.
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const API = process.env.E2E_API || "http://127.0.0.1:8064";
const UI = process.env.E2E_UI || "http://127.0.0.1:5264";
const SHOTS = process.env.SHOT_DIR || "/tmp/industry-flow-shots";
fs.mkdirSync(SHOTS, { recursive: true });

const CSV = "company,contact_name,title,email,phone,website,city,country,open_roles,company_size,hiring for\n"
  + "Skyline Realty,Neha Kapoor,Sales Director,neha@skylinerealty.in,98111 22334,https://skylinerealty.in,Pune,IN,8,120,Sales Executive\n"
  + "Sunrise Multispeciality Clinic,Dr. Ashok Verma,Medical Director,ashok@sunriseclinic.in,98270 12345,https://sunriseclinic.in,Indore,IN,5,60,Staff Nurse\n";

const call = async (url, body, method = "POST") => {
  const r = await fetch(`${API}${url}`, { method, headers: { "content-type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  if (!r.ok) throw new Error(`${method} ${url}: ${r.status} ${await r.text()}`);
  return r.json();
};
const check = (ok, what) => { if (!ok) throw new Error(`FAILED: ${what}`); console.log(`ok - ${what}`); };

async function seed() {
  await call("/api/leads/import", { csv: CSV, source: "site_form" });
  const leads = await call("/api/leads", null, "GET");
  const id = (company) => leads.find((l) => l.company === company).id;
  for (const company of ["Skyline Realty", "Sunrise Multispeciality Clinic"]) {
    await call(`/api/leads/${id(company)}/consent`, { text: "Said yes to WhatsApp on a call, 5 Nov", channel: "whatsapp" });
  }
  await call("/api/team", { team: [{ name: "Neha (Pune sales)", role: "rep" }, { name: "Ritika (clinic desk)", role: "rep" }] }, "PUT");
  return id;
}

// ---------- building blocks ----------
async function addAfter(page, nodeTitle, output, type, search) {
  const label = output ? `Add a step after ${nodeTitle} (${output})` : `Add a step after ${nodeTitle}`;
  await page.getByRole("button", { name: label, exact: true }).click();
  if (await page.getByRole("option", { name: "Add step: They reply", exact: true }).count()) throw new Error("FAILED: 'what happens next?' offers triggers");
  await page.getByLabel("Search steps").fill(search);
  await page.getByRole("option", { name: `Add step: ${type}`, exact: true }).click();
  return page.locator("[data-testid=node-config]");
}
const nodeIdOf = async (page, title) => page.locator(`[data-node-id][aria-label^="${title}:"]`).first().getAttribute("data-node-id");

async function dragBetween(page, from, to) {
  const a = await from.boundingBox();
  const b = await to.boundingBox();
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2);
  await page.mouse.down();
  await page.mouse.move((a.x + b.x) / 2 + 20, (a.y + b.y) / 2, { steps: 6 });
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 6 });
  await page.mouse.up();
}

async function dragBy(page, locator, dx, dy) {
  const a = await locator.boundingBox();
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2);
  await page.mouse.down();
  await page.mouse.move(a.x + a.width / 2 + dx / 2, a.y + a.height / 2 + dy / 2, { steps: 5 });
  await page.mouse.move(a.x + a.width / 2 + dx, a.y + a.height / 2 + dy, { steps: 5 });
  await page.mouse.up();
}

async function newFlow(page, name) {
  await page.goto(`${UI}/#/flows`);
  await page.getByRole("button", { name: "Blank flow" }).click();
  await page.locator("#fb-name").fill(name);
}

async function saveAndActivate(page) {
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.getByText(/^Saved/).waitFor();
  await page.getByRole("button", { name: "Activate", exact: true }).click();
  await page.locator("[data-testid=flow-status]", { hasText: "Active" }).waitFor();
}

async function leadPath(page, company) {
  await page.goto(`${UI}/#/leads`);
  await page.getByRole("row", { name: new RegExp(company) }).first().click();
  await page.getByRole("tab", { name: "Outreach" }).click();
  const panel = page.locator("[data-testid=lead-flows]");
  await panel.waitFor();
  await panel.locator("summary", { hasText: /^Path/ }).first().click();
  return panel.locator("[data-testid=lead-flow-path] li b").allInnerTexts();
}

// ---------- 1. real estate, built on the canvas ----------
async function realEstate(page, id) {
  await newFlow(page, "Real estate: enquiry follow-up");
  let cfg = await addAfter(page, "Added by hand", null, "Send email", "email");
  await cfg.locator("input[id$='-subject']").fill("{{company}}: sales hiring for the new launch?");
  await cfg.locator("textarea[id$='-body']").fill("{{greeting}}\n\nScreen every {{role}} applicant for {{company}} by phone in a day.\n\n— {{sender}}\n\nNot relevant? Unsubscribe: {{unsub_link}}");
  cfg = await addAfter(page, "Send email", null, "If / else", "if");
  await cfg.locator("select[id$='-field']").selectOption("has_consent");
  await cfg.locator("select[id$='-value']").selectOption("true");
  cfg = await addAfter(page, "If / else", "yes", "Send WhatsApp", "whatsapp");
  await cfg.locator("select[id$='-template']").selectOption("pilot_offer");
  cfg = await addAfter(page, "If / else", "no", "Call task", "call");
  await cfg.locator("select[id$='-owner']").selectOption("Neha (Pune sales)");
  await cfg.locator("textarea[id$='-note']").fill("Call {{first_name}} at {{company}} about {{role}} hiring.");
  // drag the call card to the right, then drag a connection from WhatsApp's output to it
  const callId = await nodeIdOf(page, "Call task");
  const waId = await nodeIdOf(page, "Send WhatsApp");
  await page.getByRole("button", { name: "Auto-layout" }).click();
  await page.waitForTimeout(300);
  const card = page.locator(`[data-node-id="${callId}"] rect.fb-card`);
  await page.locator("[data-testid=flow-canvas]").scrollIntoViewIfNeeded();
  const before = await card.boundingBox();
  await dragBy(page, card, 60, 40);
  const after = await page.locator(`[data-node-id="${callId}"] rect.fb-card`).boundingBox();
  check(Math.abs(after.x - before.x) + Math.abs(after.y - before.y) > 20, "a step card can be dragged on the canvas");
  await dragBetween(page, page.locator(`[data-port="${waId}:next"] circle.fb-port-hit`), page.locator(`[data-node-id="${callId}"] rect.fb-card`));
  await page.getByRole("button", { name: /^Connection from Send WhatsApp to Call task/ }).waitFor();
  check(true, "dragging from WhatsApp's output to the call step connects them");
  await page.getByRole("button", { name: "Validate" }).click();
  await page.getByText("No problems found. Ready to activate.").waitFor();
  await page.screenshot({ path: path.join(SHOTS, "realestate-canvas-1400.png") });
  await saveAndActivate(page);
  await page.getByLabel("Search leads to enrol").fill("Skyline");
  await page.locator(".fb-pick label", { hasText: "Skyline Realty" }).locator("input").check();
  await page.getByRole("button", { name: "Enrol 1 lead" }).click();
  await page.getByText("Started 1.").waitFor();
  const ticked = await call("/api/flows/tick", {});
  check(ticked.steps >= 4, `the engine ran the steps (${JSON.stringify(ticked)})`);
  const steps = await leadPath(page, "Skyline Realty");
  await page.screenshot({ path: path.join(SHOTS, "realestate-lead-path-1400.png") });
  check(JSON.stringify(steps) === JSON.stringify(["Added by hand", "Send email", "If / else", "Send WhatsApp", "Call task"]),
    `the lead panel shows the real-estate path: ${steps.join(" → ")}`);
  const wa = (await call("/api/whatsapp/outbox", null, "GET")).filter((m) => m.lead_id === id("Skyline Realty"));
  check(wa.length === 1 && wa[0].template === "pilot_offer" && wa[0].status === "dry_run", "one practice WhatsApp, pilot_offer");
  const tasks = (await call("/api/tasks", null, "GET")).filter((t) => t.lead_id === id("Skyline Realty") && t.kind === "call");
  check(tasks.length === 1 && tasks[0].owner === "Neha (Pune sales)", "a call task for Neha");
}

// ---------- 2. clinic: replace the trigger, build, start from the lead panel ----------
async function clinic(page, id) {
  await newFlow(page, "Clinic: yes → WhatsApp + call");
  await page.locator('[data-node-id][aria-label^="Added by hand:"]').click();
  await page.keyboard.press("Delete");
  await page.getByTestId("add-step").click();
  await page.getByLabel("Search steps").fill("reply");
  await page.getByRole("option", { name: "Add step: They reply", exact: true }).click();
  await page.locator("[data-testid=node-config] select[id$='-label']").selectOption("interested");
  let cfg = await addAfter(page, "They reply", null, "If / else", "if");
  await cfg.locator("select[id$='-field']").selectOption("has_consent");
  cfg = await addAfter(page, "If / else", "yes", "Send WhatsApp", "whatsapp");
  await cfg.locator("select[id$='-template']").selectOption("first_after_yes");
  cfg = await addAfter(page, "Send WhatsApp", null, "Call task", "call");
  await cfg.locator("select[id$='-owner']").selectOption("Ritika (clinic desk)");
  await cfg.locator("textarea[id$='-note']").fill("{{first_name}} at {{company}} said yes: call before 18:55.");
  cfg = await addAfter(page, "If / else", "no", "Notify a person", "notify");
  await cfg.locator("textarea[id$='-message']").fill("{{company}} said yes without WhatsApp consent: call them.");
  await page.screenshot({ path: path.join(SHOTS, "clinic-canvas-1400.png") });
  await saveAndActivate(page);
  // start it from the lead panel
  await page.goto(`${UI}/#/leads`);
  await page.getByRole("row", { name: /Sunrise Multispeciality Clinic/ }).first().click();
  await page.getByRole("tab", { name: "Outreach" }).click();
  const panel = page.locator("[data-testid=lead-flows]");
  await panel.locator("select").selectOption({ label: "Clinic: yes → WhatsApp + call" });
  await panel.getByRole("button", { name: "Start" }).click();
  await panel.getByText("Started.").waitFor();
  await call("/api/flows/tick", {});
  const steps = await leadPath(page, "Sunrise Multispeciality Clinic");
  await page.screenshot({ path: path.join(SHOTS, "clinic-lead-path-1400.png") });
  check(JSON.stringify(steps) === JSON.stringify(["They reply", "If / else", "Send WhatsApp", "Call task"]),
    `the lead panel shows the clinic path: ${steps.join(" → ")}`);
  const tasks = (await call("/api/tasks", null, "GET")).filter((t) => t.lead_id === id("Sunrise Multispeciality Clinic") && t.kind === "call");
  check(tasks.length === 1 && tasks[0].owner === "Ritika (clinic desk)" && tasks[0].text.includes("Ashok at Sunrise Multispeciality Clinic said yes"),
    "a call task for Ritika with the note filled in");
}

// ---------- 3. phone width ----------
async function phone(browser, errors) {
  const ctx = await browser.newContext({ viewport: { width: 375, height: 812 } });
  const page = await ctx.newPage();
  page.on("console", (m) => { if (m.type() === "error") errors.push(`375px: ${m.text()}`); });
  page.on("pageerror", (e) => errors.push(`375px: ${e}`));
  await page.goto(`${UI}/#/flows`);
  await page.getByRole("button", { name: "Open Clinic: yes → WhatsApp + call" }).click();
  await page.locator("[data-testid=flow-list-view]").waitFor();
  const wide = await page.evaluate(() => [document.documentElement.scrollWidth, document.body.scrollWidth, window.innerWidth]);
  await page.screenshot({ path: path.join(SHOTS, "clinic-list-375.png"), fullPage: true });
  check(wide[0] <= 375 && wide[1] <= 375, `the builder opens as a list at 375px with no sideways scroll (${wide.join("/")})`);
  check(await page.locator("[data-testid=flow-canvas]").count() === 0, "no canvas on a phone (list view)");
  await ctx.close();
}

const browser = await chromium.launch();
const errors = [];
try {
  const id = await seed();
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  ctx.setDefaultTimeout(15000);
  const page = await ctx.newPage();
  page.on("console", (m) => { if (m.type() === "error") errors.push(`1400px: ${m.text()}`); });
  page.on("pageerror", (e) => errors.push(`1400px: ${e}`));
  try {
    await realEstate(page, id);
    await clinic(page, id);
  } catch (e) {
    await page.screenshot({ path: path.join(SHOTS, "failure.png") });
    console.log("visible notes:", await page.locator(".tip").allInnerTexts());
    throw e;
  }
  await ctx.close();
  await phone(browser, errors);
  check(errors.length === 0, `no console errors${errors.length ? `: ${errors.join(" | ")}` : ""}`);
  console.log("ALL BROWSER CHECKS PASSED");
} finally {
  await browser.close();
}
