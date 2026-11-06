// Shared helpers for the whole-app QA crawl (e2e/qa_ui.mjs). Browser-only: everything is done by clicking and typing.
export const PAGES = ["myday", "dashboard", "leads", "companies", "lists", "campaigns", "growth", "replies", "calls", "linkedin",
  "tasks", "meetings", "outbox", "templates", "flows", "workflows", "stories", "prompts", "analytics", "sources", "signals",
  "hygiene", "engine", "setup", "integrations", "help", "insights", "intelligence"];
export const WIDTHS = [["desktop", 1400, 900], ["tablet", 768, 1000], ["phone", 375, 812]];
export const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// Words that must never reach the screen.
const BAD_TEXT = [
  [/\{\{\s*[\w.]+\s*\}\}/, "raw {{placeholder}}"],
  [/\bundefined\b/, "undefined"],
  [/\bNaN\b/, "NaN"],
  [/(^|[\s:(])null([\s,.)]|$)/m, "null"],
  [/\bInvalid Date\b/, "Invalid Date"],
  [/\b(UTC|GMT)\b/, "UTC/GMT time"],
  [/\[object Object\]/, "[object Object]"],
  [/\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/, "raw ISO timestamp"],
];

export function watch(page, sink) {
  const ctx = { where: "", allowed: [] };
  const ok = (status, url) => ctx.allowed.some((a) => (a.status === status || a.status === "*") && url.includes(a.part));
  page.on("pageerror", (e) => sink.push(`[${ctx.where}] page error: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const status = Number((m.text().match(/status of (\d{3})/) || [])[1]);
    if (status && ok(status, m.location()?.url || "")) return;
    sink.push(`[${ctx.where}] console: ${m.text().slice(0, 200)}`);
  });
  page.on("response", (r) => {
    if (r.status() >= 400 && !r.url().includes("favicon") && !ok(r.status(), r.url())) sink.push(`[${ctx.where}] HTTP ${r.status()} ${r.request().method()} ${r.url().replace(/^https?:\/\/[^/]+/, "")}`);
  });
  page.on("dialog", (d) => d.dismiss().catch(() => {}));
  return ctx;
}

const EDITORS = /templates|setup|flows|workflows|prompts|stories|linkedin|help/;   // pages that explain {{placeholders}} on purpose
export async function textProblems(page) {
  const txt = await page.locator("main").innerText().catch(() => "");
  const hash = await page.evaluate(() => window.location.hash).catch(() => "");
  const out = [];
  for (const [re, label] of BAD_TEXT) {
    if (label.startsWith("raw {{") && EDITORS.test(hash)) continue;
    const m = txt.match(re);
    if (m) {
      const i = m.index;
      out.push(`${label}: "…${txt.slice(Math.max(0, i - 50), i + 50).replace(/\s+/g, " ")}…"`);
    }
  }
  return out;
}

export async function overflow(page) {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  if (over <= 1) return null;
  const culprits = await page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    return [...document.querySelectorAll("body *")].filter((el) => {
      const r = el.getBoundingClientRect();
      if (!r.width || r.right <= vw + 1) return false;
      // skip descendants of scroll containers
      for (let p = el.parentElement; p; p = p.parentElement) {
        const o = getComputedStyle(p).overflowX;
        if (o === "auto" || o === "scroll" || o === "hidden") return false;
      }
      return true;
    }).slice(-4).map((el) => `${el.tagName.toLowerCase()}.${String(el.className).slice(0, 40)} "${(el.textContent || "").trim().slice(0, 40)}" right=${Math.round(el.getBoundingClientRect().right)}`);
  });
  return `scrolls sideways by ${over}px: ${culprits.join(" | ")}`;
}

export async function signIn(page, url, password, email = "") {
  await page.goto(url);
  await page.getByLabel("Password").waitFor();
  if (email) await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.locator("main").waitFor({ timeout: 20000 });
  await settle(page, 500);
  const later = page.getByRole("button", { name: "Later", exact: true });
  if (await later.isVisible().catch(() => false)) { await later.click(); await settle(page, 300); }
}

export async function settle(page, ms = 400) {
  await page.waitForLoadState("networkidle", { timeout: 8000 }).catch(() => {});
  await wait(ms);
}

export const LABELS = { myday: "My day", dashboard: "Dashboard", leads: "Leads", hygiene: "Data hygiene", lists: "Lists",
  companies: "Companies", campaigns: "Campaigns", engine: "Hiring engine", signals: "Buying signals", sources: "Data sources",
  analytics: "Analytics", growth: "Free experience", flows: "Flow builder", workflows: "Simple rules", outbox: "Outbox & sending",
  calls: "Call desk", meetings: "Meetings", replies: "Replies", tasks: "Tasks & follow-ups", linkedin: "LinkedIn outreach",
  templates: "Sequences & templates", stories: "Success stories", prompts: "Prompt generator", setup: "Setup",
  integrations: "Integrations", help: "Help centre" };

// Go to a page the way a person does: the menu (on a phone: open the menu first). Pages not in the menu: by link.
export async function goPage(page, id, phone) {
  const label = LABELS[id];
  if (!label) { await page.evaluate((pid) => { window.location.hash = pid; }, id); await settle(page); return; }
  if (phone) { await page.getByRole("button", { name: "Open menu" }).click(); await wait(250); }
  await page.locator("#main-nav").getByRole("button", { name: label, exact: true }).click();
  await settle(page);
}

const UNSAFE = /delete|remove|undo|sign out|send|live|reset|restore|disconnect|clear|wipe|archive|merge|import|run|start|stop|pause|approve|reject|unsubscribe|opt.?out|do not contact|dnc|mark|log|save|add|create|new|generate|upload|connect|apply|enroll|enrol|assign|publish|enable|disable|turn|switch|test|retry|skip|complete|done|dismiss|snooze|book|cancel|copy|download|export|print|claim|take|call|whatsapp|email|open in|regenerate|use this|confirm|yes|sync|refresh|fetch|read|find|scan|check|verify|fix|duplicate|clone|promote|backfill|seed|demo|tour|theme|close|back|bulk|select all|attach|preview|install/i;

// Click every tab, sort header, pagination control and harmless toggle on the current page; cycle every <select>.
export async function clickAround(page, problems, ctx, where, opts = {}) {
  const base = await page.evaluate(() => window.location.hash);
  const groups = [
    ["[role=tab]", "tab"],
    ["main th button, main th[aria-sort], main th.sortable, main th.click", "sort"],
    ["main .pager button, main [aria-label*=page i] button, main button[aria-label*='Next page' i], main button[aria-label*='Previous page' i]", "pager"],
    ["main .chips button, main .seg button, main .segmented button, main .filter-chips button, main .tabs button, main [role=radiogroup] button", "chip"],
  ];
  let clicks = 0;
  for (const [sel, kind] of groups) {
    const n = await page.locator(sel).count();
    for (let i = 0; i < Math.min(n, opts.max || 25); i++) {
      const el = page.locator(sel).nth(i);
      if (!(await el.isVisible().catch(() => false)) || !(await el.isEnabled().catch(() => false))) continue;
      const name = ((await el.innerText({ timeout: 2000 }).catch(() => "")) || (await el.getAttribute("aria-label", { timeout: 2000 }).catch(() => "")) || "").trim().slice(0, 30);
      if (kind !== "tab" && kind !== "sort" && UNSAFE.test(name)) continue;
      ctx.where = `${where} ${kind} "${name}"`;
      await el.click({ timeout: 3000 }).catch((e) => problems.push(`[${ctx.where}] click failed: ${e.message.split("\n")[0]}`));
      clicks++;
      await settle(page, 200);
      for (const t of await textProblems(page)) problems.push(`[${ctx.where}] ${t}`);
      if (opts.phoneCheck) { const o = await overflow(page); if (o) problems.push(`[${ctx.where}] ${o}`); }
      await page.keyboard.press("Escape").catch(() => {});
      if ((await page.evaluate(() => window.location.hash)) !== base && kind !== "tab") {
        await page.evaluate((h) => { window.location.hash = h; }, base); await settle(page, 200);
      }
    }
  }
  // selects in main (not in dialogs/drawers): try each option, then put the first back
  const sels = await page.locator("main select:visible").count();
  for (let i = 0; i < Math.min(sels, 12); i++) {
    const s = page.locator("main select:visible").nth(i);
    const label = (await s.getAttribute("aria-label", { timeout: 2000 }).catch(() => null)) || (await s.getAttribute("id", { timeout: 2000 }).catch(() => null)) || `select${i}`;
    if (/stage|owner|assign|status|role|mode|provider|channel|template|sequence|field for|label|reassign|move|priority|tag/i.test(label) && !opts.allSelects) continue;
    const values = await s.locator("option").evaluateAll((os) => os.map((o) => o.value)).catch(() => []);
    const first = await s.inputValue().catch(() => null);
    for (const v of values.slice(0, 8)) {
      ctx.where = `${where} select ${label}=${v}`;
      await s.selectOption(v, { timeout: 2000 }).catch(() => {});
      clicks++;
      await settle(page, 200);
      for (const t of await textProblems(page)) problems.push(`[${ctx.where}] ${t}`);
    }
    if (first !== null) await s.selectOption(first, { timeout: 2000 }).catch(() => {});
  }
  return clicks;
}
