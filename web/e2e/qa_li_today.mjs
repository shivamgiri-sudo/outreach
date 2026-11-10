// QA (area "LinkedIn page: Today tab, feedback where you look, start from the lead"), only through the web UI. Same rig as qa_admin.mjs.
//   cd web && E2E_UI=http://localhost:5284 node e2e/qa_li_today.mjs
// Covers: the page opens on Today with the agent status strip (switched off -> says nothing goes out yet); the tabs show
// each section and keep it in the address; "Start LinkedIn sequence" at the bottom of the list shows its message in the
// visible part of the screen and says when the agent will send; the lead drawer has Start / Stop LinkedIn outreach;
// switching the agent on changes the strip; every control on every tab can be clicked (nothing covers it), at 1400 and 390 px.
import { chromium } from "playwright";

const UI = process.env.E2E_UI || "http://localhost:5284";
const ADMIN = process.env.E2E_PASSWORD || "qa-admin-pass-1";
const SHOTS = process.env.SHOT_DIR || "e2e-shots";
const CSV = ["company,contact_name,title,email,website,linkedin_url",
  "Sharma Logistics,Rohit Sharma,Head of HR,rohit@sharmalogistics.example,https://sharmalogistics.example,https://www.linkedin.com/in/rohit-sharma-qa",
  "Kaveri Textiles,Lakshmi Iyer,HR Manager,lakshmi@kaveritextiles.example,https://kaveritextiles.example,https://www.linkedin.com/in/lakshmi-iyer-qa",
  "Konkan Realty,Suresh Naik,Sales Head,suresh@konkanrealty.example,https://konkanrealty.example,https://www.linkedin.com/in/suresh-naik-qa",
  "Delta Steels,Anita Rao,CHRO,anita@deltasteels.example,https://deltasteels.example,"].join("\n");
const TABS = ["Today", "Stages", "Agent & accounts", "Messages", "Settings & tools"];
const results = [];
const browser = await chromium.launch();
const errors = [];

async function session(width) {
  const ctx = await browser.newContext({ viewport: { width, height: 900 }, reducedMotion: "reduce" });
  const p = await ctx.newPage();
  p.on("response", (r) => { if (r.url().includes("/api/") && r.status() >= 500) errors.push(`${r.status()} ${r.url().replace(UI, "")}`); });
  p.on("pageerror", (e) => errors.push(`pageerror ${e.message}`));
  await p.goto(UI);
  await p.getByLabel("Password").fill(ADMIN);
  await p.getByRole("button", { name: "Sign in" }).click();
  await p.locator("main").waitFor();
  const later = p.getByRole("button", { name: "Skip setup" });
  await later.waitFor({ timeout: 6000 }).then(async () => { await later.click(); await later.waitFor({ state: "detached", timeout: 20000 }); }, () => {});
  return p;
}
async function step(name, fn) {
  try { await fn(); results.push([true, name]); } catch (e) { results.push([false, `${name} — ${e.message.split("\n")[0]}`]); }
}
const toLinkedIn = async (p, tab = "") => { await p.evaluate((t) => { location.hash = t ? `#linkedin?tab=${t}` : "#linkedin"; }, tab); await p.waitForTimeout(1500); };
const tab = (p, name) => p.getByRole("tablist", { name: "LinkedIn sections" }).getByRole("tab", { name, exact: true });
const inView = (p, loc) => loc.evaluate((el) => { const r = el.getBoundingClientRect(); return r.bottom > 0 && r.top < window.innerHeight; });

const p = await session(1400);
await step("Import four leads (three with a LinkedIn link)", async () => {
  await p.evaluate(() => { location.hash = "#leads"; }); await p.waitForTimeout(1000);
  await p.getByRole("button", { name: "Quick paste" }).click();
  await p.locator("textarea").first().fill(CSV);
  await p.getByRole("button", { name: "Import", exact: true }).click();
  await p.getByText("Added 4").waitFor();
});
await step("LinkedIn page opens on Today with the agent status strip: switched off, nothing goes out yet", async () => {
  await toLinkedIn(p);
  if ((await tab(p, "Today").getAttribute("aria-selected")) !== "true") throw new Error("Today is not the first tab");
  const head = await p.getByTestId("li-status-head").innerText();
  if (!/switched off/i.test(head)) throw new Error(head);
  await p.getByRole("button", { name: "Start LinkedIn sequence" }).first().waitFor();
});
await step("Each tab shows its own section and keeps it in the address", async () => {
  const want = { "Stages": "li-stage-board", "Agent & accounts": "linkedin-agent", "Settings & tools": "download-linkedin-tool" };
  for (const name of TABS) {
    await tab(p, name).click(); await p.waitForTimeout(600);
    if (want[name]) await p.getByTestId(want[name]).waitFor({ timeout: 5000 });
    if (name === "Messages") await p.getByText("Connection notes A / B").waitFor();
  }
  if (!/tab=settings/.test(await p.evaluate(() => location.hash))) throw new Error(await p.evaluate(() => location.hash));
  await p.reload(); await p.locator("main").waitFor(); await p.waitForTimeout(1200);
  if ((await tab(p, "Settings & tools").getAttribute("aria-selected")) !== "true") throw new Error("tab not kept after reload");
});
await step("Start LinkedIn sequence at the bottom: the message shows on screen and says when it will be sent", async () => {
  await tab(p, "Today").click(); await p.waitForTimeout(800);
  const btn = p.getByRole("button", { name: "Start LinkedIn sequence" }).last();
  await btn.scrollIntoViewIfNeeded();
  await btn.click();
  const toast = p.getByTestId("li-toast");
  await toast.waitFor({ timeout: 8000 });
  const text = await toast.innerText();
  if (!/Step 1 is in today's list/i.test(text) || !/nothing goes out yet|switched off/i.test(text)) throw new Error(text);
  if (!(await inView(p, toast))) throw new Error("message is outside the visible screen");
  await p.screenshot({ path: `${SHOTS}/li-today-toast.png` });
});
await step("Lead drawer: Start LinkedIn outreach puts the person on the sequence, then Stop is offered", async () => {
  await p.evaluate(() => { location.hash = "#leads"; }); await p.waitForTimeout(1200);
  await p.getByRole("button", { name: "Kaveri Textiles" }).first().click();
  const d = p.getByRole("dialog").first();
  await d.getByRole("tab", { name: "Outreach" }).click();
  const start = d.getByTestId("drawer-li-start");
  await start.waitFor();
  await start.click();
  const box = p.getByTestId("app-dialog");
  await box.waitFor();
  const said = await box.innerText();
  if (!/on the LinkedIn sequence/i.test(said)) throw new Error(said);
  await box.getByTestId("dialog-ok").click();
  await d.getByTestId("drawer-li-stop").waitFor();
  if (!/On the LinkedIn sequence/i.test(await d.innerText())) throw new Error("drawer does not show the sequence");
  await p.keyboard.press("Escape");
});
await step("Lead drawer: a person with no LinkedIn link is looked up first (the drawer says so)", async () => {
  await p.getByRole("button", { name: "Delta Steels" }).first().click();
  const d = p.getByRole("dialog").first();
  await d.getByRole("tab", { name: "Outreach" }).click();
  await d.getByText("No LinkedIn link yet").waitFor();
  await d.getByTestId("drawer-li-start").click();
  const box = p.getByTestId("app-dialog");
  await box.waitFor();
  if (!/no LinkedIn link yet/i.test(await box.innerText())) throw new Error(await box.innerText());
  await box.getByTestId("dialog-ok").click();
  await p.keyboard.press("Escape");
});
await step("Switching the agent on changes the strip (no agent program running, so it says that)", async () => {
  await toLinkedIn(p, "agent");
  await p.getByTestId("linkedin-agent").getByRole("button", { name: "Switch on" }).click();
  await p.getByTestId("li-toast").waitFor();
  await tab(p, "Today").click(); await p.waitForTimeout(1500);
  const head = await p.getByTestId("li-status-head").innerText();
  if (!/not running/i.test(head)) throw new Error(head);
});

async function everyControlClickable(page, width) {
  const bad = [];
  for (const name of TABS) {
    // From the top of the page, with no message showing: Playwright may otherwise scroll a control under the phone's
    // fixed bottom menu or under a message at the bottom (a person scrolls; the test should not fail on that).
    await page.getByRole("button", { name: "Close message" }).click().catch(() => {});
    await page.evaluate(() => window.scrollTo(0, 0));
    await tab(page, name).click(); await page.waitForTimeout(800);
    await page.evaluate(() => document.querySelectorAll("main details").forEach((d) => { d.open = true; }));
    await page.waitForTimeout(300);
    const all = page.locator("main button, main a[href], main select, main summary, main input:not([type=hidden]), main textarea");
    for (let i = 0, n = await all.count(); i < n; i++) {
      const el = all.nth(i);
      if (!(await el.isVisible()) || await el.isDisabled().catch(() => false)) continue;
      try { await el.click({ trial: true, timeout: 2500 }); }
      catch (e) { bad.push(`${name}: ${(await el.innerText().catch(() => "")).slice(0, 40)} ${e.message.split("\n")[0].slice(0, 120)}`); }
    }
    const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    if (over > 1) bad.push(`${name}: page is ${over}px wider than the screen`);
  }
  if (bad.length) throw new Error(`${bad.length} at ${width}px: ${bad.slice(0, 4).join(" | ")}`);
}
await step("Every control on every tab can be clicked at 1400 px", () => everyControlClickable(p, 1400));
const phone = await session(390);
await step("Every control on every tab can be clicked at 390 px", async () => { await toLinkedIn(phone); await everyControlClickable(phone, 390); });
await step("On a phone the message sits above the bottom menu, not under it", async () => {
  await phone.evaluate(() => window.scrollTo(0, 0));
  await tab(phone, "Settings & tools").click(); await phone.waitForTimeout(600);
  const box = phone.locator("details", { hasText: "LinkedIn settings" }).first();
  await box.evaluate((d) => { d.open = true; });                    // the check above may have opened it already
  await box.getByRole("button", { name: "Save", exact: true }).click();
  const toast = phone.getByTestId("li-toast");
  await toast.waitFor();
  const [t, bar] = await Promise.all([toast.boundingBox(), phone.locator("nav.bottombar").boundingBox()]);
  if (!bar) throw new Error("no bottom menu at phone width");
  if (t.y + t.height > bar.y + 1) throw new Error(`message bottom ${Math.round(t.y + t.height)} overlaps the menu at ${Math.round(bar.y)}`);
});
await step("No server errors or page errors", async () => { if (errors.length) throw new Error(errors.slice(0, 5).join(" | ")); });

await browser.close();
for (const [ok, name] of results) console.log(`${ok ? "PASS" : "FAIL"} ${name}`);
console.log(`${results.filter((r) => r[0]).length}/${results.length} passed`);
process.exit(results.every((r) => r[0]) ? 0 : 1);
