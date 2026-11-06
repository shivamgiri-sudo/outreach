// Browser regressions from the automation QA sweep (flows, tasks). Everything is done through the UI, as a person would.
//
//   E2E_UI=http://127.0.0.1:5283 E2E_PASSWORD=qa-admin-pass-1 node e2e/qa_automation.mjs
//
// Expects the API (practice mode, a throwaway OUTREACH_DATA_FILE, OUTREACH_ADMIN_PASSWORD set) and a built UI served
// with e2e/static_proxy.mjs. Nothing is sent for real.
import { chromium } from "playwright";

const UI = process.env.E2E_UI || "http://127.0.0.1:5283";
const PASSWORD = process.env.E2E_PASSWORD || "qa-admin-pass-1";
const check = (ok, what) => { if (!ok) throw new Error(`FAILED: ${what}`); console.log(`ok - ${what}`); };

async function login(page) {
  await page.goto(`${UI}/`);
  if (await page.locator("#pw").count()) {
    await page.locator("#pw").fill(PASSWORD);
    await page.locator(".login-submit").click();
    await page.locator("#pw").waitFor({ state: "detached" });
  }
  await page.waitForLoadState("networkidle");
  const skip = page.getByRole("button", { name: "Skip setup" });
  if (await skip.count()) await skip.click();
}

async function dragFromPicker(page, label, search) {
  await page.getByTestId("add-step").click();
  await page.getByLabel("Search steps").fill(search);
  const item = page.getByRole("option", { name: `Add step: ${label}`, exact: true });
  const ib = await item.boundingBox();
  const cb = await page.locator("[data-testid=flow-canvas]").boundingBox();
  await page.mouse.move(ib.x + 40, ib.y + ib.height / 2);
  await page.mouse.down();
  await page.mouse.move(ib.x + 60, ib.y + ib.height / 2 + 10, { steps: 4 });
  await page.waitForTimeout(150);
  await page.mouse.move(cb.x + cb.width / 2, cb.y + 80, { steps: 10 });
  await page.mouse.up();
}

const browser = await chromium.launch();
const errors = [];
try {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, timezoneId: "Asia/Kolkata" });
  ctx.setDefaultTimeout(15000);
  const page = await ctx.newPage();
  page.on("pageerror", (e) => errors.push(String(e)));
  await login(page);

  // 1. Flow builder: blank flow, replace the trigger by dragging "Webhook received" out of the Add step box
  await page.goto(`${UI}/#/flows`);
  await page.getByRole("button", { name: "Blank flow" }).click();
  await page.locator("#fb-name").fill("QA sweep: dragged webhook flow");
  await page.locator('[data-node-id][aria-label^="Added by hand:"]').click();
  await page.keyboard.press("Delete");
  await dragFromPicker(page, "Webhook received", "webhook");
  const hook = page.locator('[data-node-id][aria-label^="Webhook received:"]');
  check(await hook.count() === 1, "a trigger dragged from Add step lands on the canvas");
  await hook.locator("rect.fb-card").click();
  check(await page.getByText("Save the flow and this flow's address for other tools appears here.").count() === 1,
    "the webhook box asks for a save instead of saying the flow does not start from a webhook");
  // the stale server problem ("Connect the trigger...") must not stay on a connected trigger
  await page.getByRole("button", { name: "Add a step after Webhook received", exact: true }).click();
  await page.getByLabel("Search steps").fill("tag");
  await page.getByRole("option", { name: "Add step: Add tag", exact: true }).click();
  await page.locator("[data-testid=node-config] input[id$='-tag']").fill("qa-webhook");
  check(!(await hook.getAttribute("aria-label")).includes("Problem:"), "no stale problem on the connected trigger");
  // a step after a far output must be on screen
  await page.getByRole("button", { name: "Add a step after Add tag", exact: true }).click();
  await page.getByLabel("Search steps").fill("reply");
  await page.getByRole("option", { name: "Add step: Wait for a reply", exact: true }).click();
  await page.getByRole("button", { name: "Add a step after Wait for a reply (no reply)", exact: true }).click();
  await page.getByLabel("Search steps").fill("stop");
  await page.getByRole("option", { name: "Add step: Stop", exact: true }).click();
  const stop = await page.locator('[data-node-id][aria-label^="Stop:"] rect.fb-card').boundingBox();
  const canvas = await page.locator("[data-testid=flow-canvas]").boundingBox();
  check(stop.x >= canvas.x && stop.x + stop.width <= canvas.x + canvas.width, "the step after 'no reply' is panned into view");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.getByText(/^Saved/).first().waitFor();
  await page.getByRole("button", { name: "Auto-layout" }).click();
  await page.waitForTimeout(300);
  await hook.locator("rect.fb-card").click();
  await page.getByText("Address for other tools").waitFor();
  check(true, "after saving, the webhook address appears");

  // 2. Flow list: names the trigger and can delete a flow
  await page.getByRole("button", { name: "← All flows" }).click();
  const leave = page.getByRole("button", { name: "Save and leave" });
  if (await leave.count()) await leave.click();                 // Auto-layout moved the cards: save them too
  const row = page.getByRole("row", { name: /QA sweep: dragged webhook flow/ }).first();
  await row.waitFor();
  check((await row.innerText()).includes("Webhook received"), "the flow list names the webhook trigger");
  const rows = page.getByRole("row", { name: /QA sweep: dragged webhook flow/ });
  const before = await rows.count();
  await page.getByRole("button", { name: "Delete QA sweep: dragged webhook flow" }).first().click();
  await page.getByTestId("dialog-ok").click();
  await page.waitForTimeout(600);
  check(await rows.count() === before - 1, "the flow is gone from the list");
  check(true, "a flow can be deleted from the list");

  // 3. Tasks: +1 hour on a task due tomorrow evening moves it later, not to now + 1 hour
  await page.goto(`${UI}/#/tasks`);
  const ist = new Date(Date.now() + 5.5 * 3600e3 + 86400e3).toISOString().slice(0, 10);
  const form = page.locator("form.card");
  await form.getByLabel("New task").fill("QA sweep: call back tomorrow 19:30");
  await form.getByLabel("Due (IST)").fill(`${ist}T19:30`);
  await form.getByRole("button", { name: "Add" }).click();
  await page.getByText("Task added.").waitFor();
  const li = page.locator("li", { hasText: "QA sweep: call back tomorrow 19:30" });
  await li.getByRole("button", { name: "+1 hour" }).click();
  await page.getByText("Snoozed 1 hour.").waitFor();
  await page.waitForTimeout(400);
  check((await li.innerText()).includes("8:30 pm IST"), `snoozed task is due 8:30 pm IST (${(await li.locator(".sub").innerText()).slice(0, 60)})`);
  await li.getByRole("button", { name: "Done" }).click();

  // 4. Phone width: long task titles wrap
  await page.setViewportSize({ width: 375, height: 812 });
  await form.getByLabel("New task").fill("https://careers.example-company-with-a-very-long-domain-name.in/jobs/view/1234567890/apply?utm=qa");
  await form.getByRole("button", { name: "Add" }).click();
  await page.getByText("Task added.").waitFor();
  const wide = await page.evaluate(() => document.documentElement.scrollWidth);
  check(wide <= 375, `no sideways scroll at 375px with a long task title (${wide})`);
  await page.locator("li", { hasText: "example-company-with-a-very-long" }).getByRole("button", { name: "Done" }).click();

  check(errors.length === 0, `no page errors${errors.length ? `: ${errors.join(" | ")}` : ""}`);
  console.log("ALL QA AUTOMATION CHECKS PASSED");
} finally {
  await browser.close();
}
