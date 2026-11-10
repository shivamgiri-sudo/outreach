// QA (area "glossary and user guide"): <Abbr> tooltips (hover, focus, tap, Esc), glossary search and deep link, role-aware guide
// (owner vs caller), guide search, the "?" links, phone width, no CSP errors.
//   rig: fresh OUTREACH_DATA_FILE, OUTREACH_ADMIN_PASSWORD=qa-admin-pass-1 OUTREACH_ADMIN_LOGIN=1 OUTREACH_EMAIL_DOMAIN_CHECK=0 OUTREACH_IGNORE_BUSINESS_HOURS=1
//   UI:  vite build --outDir <temp>/dist served by e2e/static_proxy.mjs (CADDY_HEADERS=deploy/Caddyfile.template for the production CSP)
//   cd web && E2E_UI=http://localhost:5291 SHOT_DIR=/tmp/shots node e2e/qa_guide.mjs
import { chromium } from "playwright";

const UI = process.env.E2E_UI || "http://localhost:5291";
const ADMIN = process.env.E2E_PASSWORD || "qa-admin-pass-1";
const SHOTS = process.env.SHOT_DIR || "/tmp";
const results = [];
const problems = [];
const browser = await chromium.launch();
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
function watch(page) {
  page.on("pageerror", (e) => problems.push(`pageerror ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") problems.push(`console ${m.text().slice(0, 160)}`); });
}
async function step(name, fn) {
  try { await fn(); results.push([true, name]); } catch (e) { results.push([false, `${name} — ${e.message.split("\n")[0]}`]); }
}
const ok = (c, m) => { if (!c) throw new Error(m); };
const noScroll = async (page, where) => {
  const w = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
  ok(w[0] <= w[1], `${where}: horizontal scroll ${w[0]} > ${w[1]}`);
};
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
const token = (page) => page.evaluate(() => sessionStorage.getItem("outreach-token"));
const api = (page, method, path, body) => page.evaluate(async ([m, p, b]) => {
  const r = await fetch(p, { method: m, headers: { "Content-Type": "application/json", Authorization: `Bearer ${sessionStorage.getItem("outreach-token")}` }, body: b ? JSON.stringify(b) : undefined });
  return { status: r.status, body: await r.json().catch(() => ({})) };
}, [method, path, body]);

const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
watch(page);
await signIn(page, "", ADMIN);
await api(page, "POST", "/api/users", { email: "guide.caller@example.com", name: "Guide Caller", role: "caller", password: "guide-long-password-1" });
const HEAD = "Company,Email,Lead Status,Created Date,MQL Date,Lead Owner,Lead Source";
const today = new Date(Date.now() + 330 * 60000).toISOString().slice(0, 10).replace(/-/g, "/") + " 10:00";
await api(page, "POST", "/api/import/commit", { csv: `${HEAD}\nAcme Sample,a@acme.example,Marketing Qualified Lead (MQL),${today},${today},Asha,Email\n`, source: "sheet" });

await step("Funnel stage name shows the full name with the short form, and its tooltip opens on hover", async () => {
  await page.goto(`${UI}/#funnel`); await page.reload();
  await page.getByTestId("sf-funnel").waitFor();
  const label = page.getByTestId("sf-funnel").locator(".sf-funnel-label").nth(1);
  ok(/Marketing Qualified Lead \(MQL\)/.test(await label.innerText()), "label is not 'Marketing Qualified Lead (MQL)'");
  await label.locator("abbr").hover();
  const tip = page.getByTestId("abbr-tip");
  await tip.waitFor();
  ok(/Marketing Qualified Lead/.test(await tip.innerText()) && /Learn more/.test(await tip.innerText()), "tooltip text");
  await page.screenshot({ path: `${SHOTS}/guide-tooltip-1440.png` });
});
await step("Esc closes the tooltip", async () => {
  await page.keyboard.press("Escape");
  await page.getByTestId("abbr-tip").waitFor({ state: "detached", timeout: 3000 });
});
await step("Keyboard focus opens the tooltip; it is described for screen readers", async () => {
  await page.mouse.move(5, 5);
  const ab = page.getByTestId("sf-funnel").locator("abbr").first();
  await ab.focus();
  await page.getByTestId("abbr-tip").waitFor();
  const desc = await ab.evaluate((el) => document.getElementById(el.getAttribute("aria-describedby"))?.textContent || "");
  ok(/Marketing Qualified Lead\./.test(desc), `aria-describedby text: ${desc}`);
  await page.keyboard.press("Escape");
  await page.getByTestId("abbr-tip").waitFor({ state: "detached", timeout: 3000 });
});
await step("Learn more opens the glossary entry and focuses it", async () => {
  await page.mouse.move(5, 5);
  await page.getByTestId("sf-funnel").locator("abbr").first().click();
  await page.getByTestId("abbr-tip").getByRole("link", { name: "Learn more" }).click();
  await page.getByTestId("gloss-MQL").waitFor();
  ok(/#help\/glossary\/MQL/.test(page.url()), page.url());
  ok(await page.getByTestId("gloss-MQL").evaluate((el) => document.activeElement === el), "entry not focused");
});
await step("Glossary search finds MQL; deep link works after a reload; A-Z list has many terms", async () => {
  await page.goto(`${UI}/#help/glossary`); await page.reload();
  await page.getByTestId("glossary-search").fill("marketing qualified");
  await page.getByTestId("gloss-MQL").waitFor();
  await page.getByTestId("glossary-search").fill("");
  ok((await page.locator("[data-testid^=gloss-]").count()) >= 100, "fewer than 100 glossary terms");
  await page.goto(`${UI}/#help/glossary/SLA`); await page.reload();
  await page.getByTestId("gloss-SLA").waitFor();
  ok(await page.getByTestId("gloss-SLA").evaluate((el) => document.activeElement === el), "SLA not focused");
});
await step("Owner sees the super-admin chapters in the guide", async () => {
  await page.goto(`${UI}/#help`); await page.reload();
  await page.getByTestId("toc-setup-accounts").waitFor();
  await page.getByTestId("toc-recipe-suspend-account").waitFor();
  await page.getByTestId("toc-funnel").click();
  await page.getByTestId("chapter-funnel").waitFor();
  await page.screenshot({ path: `${SHOTS}/guide-chapter-1440.png` });
});
await step("Guide search finds chapters and glossary terms", async () => {
  await page.getByTestId("guide-search").fill("bounce");
  const res = page.getByTestId("guide-results");
  await res.waitFor();
  const t = await res.innerText();
  ok(/Guide/.test(t) && /Glossary/.test(t), `results: ${t.slice(0, 200)}`);
  await page.getByTestId("guide-search").fill("");
});
await step("The ? link on the Sales funnel and on Leads opens the right chapter", async () => {
  for (const [hash, id] of [["#funnel", "funnel"], ["#leads", "leads"], ["#outbox", "outbox"], ["#reports", "reports"], ["#linkedin", "linkedin"]]) {
    await page.goto(`${UI}/${hash}`); await page.reload();
    await page.getByTestId(`help-link-${id}`).first().click();
    await page.getByTestId(`chapter-${id}`).waitFor();
  }
  await page.goto(`${UI}/#setup/audit`); await page.reload();
  await page.getByTestId("help-link-setup-audit").click();
  await page.getByTestId("chapter-setup-audit").waitFor();
});
await step("Heading copy-link and deep link to a heading", async () => {
  await page.goto(`${UI}/#help/funnel/update-stage`); await page.reload();
  await page.getByTestId("chapter-funnel").waitFor();
  const h = page.locator("#funnel--update-stage");
  await h.waitFor();
  await h.getByRole("button", { name: /Copy a link/ }).click();
});
await step("Every chapter in the contents opens without errors", async () => {
  await page.goto(`${UI}/#help`); await page.reload();
  await page.getByTestId("toc-leads").waitFor();
  const ids = await page.locator("[data-testid^=toc-]").evaluateAll((els) => els.map((e) => e.getAttribute("data-testid").slice(4)));
  ok(ids.length >= 65, `only ${ids.length} chapters`);
  for (const id of ids) { await page.goto(`${UI}/#help/${id}`); await page.getByTestId(`chapter-${id}`).waitFor({ timeout: 5000 }); }
});

// a caller sees fewer chapters
const c2 = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const caller = await c2.newPage();
watch(caller);
await step("A caller does not see the super-admin or owner-only chapters, but sees the Leads chapter", async () => {
  await signIn(caller, "guide.caller@example.com", "guide-long-password-1");
  await caller.goto(`${UI}/#help`); await caller.reload();
  await caller.getByTestId("toc-leads").waitFor();
  for (const id of ["setup-accounts", "recipe-suspend-account", "setup-connections", "recipe-onboard-client", "engine"]) ok((await caller.getByTestId(`toc-${id}`).count()) === 0, `caller sees ${id}`);
  await caller.goto(`${UI}/#help/setup-accounts`);
  await caller.getByText("not shown for yours").waitFor();
});

// phone width
const c3 = await browser.newContext({ viewport: { width: 375, height: 812 }, hasTouch: true, isMobile: true });
const phone = await c3.newPage();
watch(phone);
await step("Phone (375px): guide, chapter, glossary and funnel have no sideways scroll; a tap opens a tooltip inside the screen", async () => {
  await signIn(phone, "", ADMIN);
  for (const h of ["#help", "#help/funnel", "#help/start-roles", "#help/leads", "#help/glossary", "#help/setup-audit", "#help/recipe-import-sheet"]) {
    await phone.goto(`${UI}/${h}`); await phone.reload(); await wait(700);
    await noScroll(phone, h);
  }
  await phone.goto(`${UI}/#help`); await phone.reload();
  await phone.locator("summary", { hasText: "Contents" }).tap();
  await phone.getByTestId("toc-funnel").waitFor();
  await phone.getByTestId("toc-funnel").tap();
  await phone.getByTestId("chapter-funnel").waitFor();
  await phone.goto(`${UI}/#funnel`); await phone.reload();
  await phone.getByTestId("sf-funnel").waitFor();
  await phone.getByTestId("sf-funnel").locator("abbr").first().tap();
  const tip = phone.getByTestId("abbr-tip");
  await tip.waitFor();
  const box = await tip.boundingBox();
  ok(box.x >= 0 && box.x + box.width <= 375, `tooltip outside the screen: ${JSON.stringify(box)}`);
  await phone.screenshot({ path: `${SHOTS}/guide-tooltip-375.png` });
  await noScroll(phone, "funnel with tooltip");
  await phone.goto(`${UI}/#help/funnel`); await phone.reload(); await phone.getByTestId("chapter-funnel").waitFor(); await wait(500);
  await phone.screenshot({ path: `${SHOTS}/guide-chapter-375.png` });
});

await step("No page errors, console errors or CSP violations", async () => {
  ok(problems.length === 0, problems.slice(0, 4).join(" | "));
});
await browser.close();
let failed = 0;
for (const [pass, name] of results) { console.log(`${pass ? "PASS" : "FAIL"}  ${name}`); if (!pass) failed++; }
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
