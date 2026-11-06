#!/usr/bin/env node
// MCN LinkedIn agent. Runs on YOUR computer, in a Chrome window YOU logged in to. It takes today's work from the portal and does
// it one action at a time: connection requests (with the note from your sequence) and the follow-up messages once someone accepted.
//
//   cd web && npm ci && npx playwright install chromium && cd ..
//   node tools/linkedin_agent.mjs --portal https://137-23-39-80.sslip.io --token mcnx_xxxxx
//
// Get the token in the portal: LinkedIn outreach -> LinkedIn agent -> Create pairing token. The portal decides when this may act
// (switch, working hours, daily and weekly caps, the fixed gap between actions). Your LinkedIn password never leaves this computer.
//
// LinkedIn forbids automation and restricts accounts that do it. This agent keeps the volume low and FIXED and stops at once on
// any sign of trouble (login wall, checkpoint, CAPTCHA, weekly invitation limit). It does not hide itself: no stealth plugins,
// no spoofed browser identity, no proxies, no randomised "human" delays. Use it on an account you can afford to have restricted.
import os from "node:os";
import path from "node:path";

let chromium;
try { ({ chromium } = await import("playwright")); }
catch { ({ chromium } = await import(new URL("../web/node_modules/playwright/index.mjs", import.meta.url).href)); }

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => {
  if (a.startsWith("--")) acc.push([a.slice(2), all[i + 1] && !all[i + 1].startsWith("--") ? all[i + 1] : true]);
  return acc;
}, []));
const PORTAL = String(args.portal || process.env.MCN_PORTAL || "").replace(/\/$/, "");
const TOKEN = String(args.token || process.env.MCN_TOKEN || "");
const PROFILE_DIR = String(args.profile || path.join(os.homedir(), ".mcn-linkedin-profile"));
const LINKEDIN = String(args["linkedin-base"] || "https://www.linkedin.com").replace(/\/$/, "");   // only tests change this
const DRY = !!args.dry;                 // look at the page and say what would be clicked, click nothing
const ONCE = !!args.once;               // do at most one action and exit (tests, or a first check)
const ALLOW_NO_NOTE = !!args["allow-no-note"];      // send a request without a note when LinkedIn will not allow one
const HEADLESS = !!args.headless;       // tests only; real use needs the window so you can log in
const SETTLE = Number(args.settle || 1500);          // fixed wait after a page changes
const POLL = Number(args.poll || 20) * 1000;

if (!PORTAL || !TOKEN) { console.error("Usage: node tools/linkedin_agent.mjs --portal https://your-portal --token mcnx_..."); process.exit(2); }
const log = (...a) => console.log(new Date().toLocaleTimeString(), ...a);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Which LinkedIn account this window is logged in to (its /in/ slug). The portal keeps caps, the gap and the safety
// pause per account, and gives each person to one account only, so several accounts can run side by side.
const ME = { account: "", name: "", checkedAt: 0 };
async function api(pathname, method = "GET", body) {
  const res = await fetch(PORTAL + pathname, { method, headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json",
    ...(ME.account ? { "X-LinkedIn-Account": ME.account, "X-LinkedIn-Name": encodeURIComponent(ME.name).slice(0, 200) } : {}) },
    body: body ? JSON.stringify(body) : undefined });
  if (!res.ok) { const text = await res.text(); const err = new Error(`${method} ${pathname}: ${res.status} ${text.slice(0, 160)}`); err.status = res.status; throw err; }
  return res.json();
}
const event = (kind, extra = {}) => api("/api/extension/agent/event", "POST", { kind, ...extra }).catch((e) => log("could not report:", e.message));

class Fatal extends Error {}

const TROUBLE = [/security (check|verification)/i, /quick security check/i, /weekly invitation limit/i, /reached the weekly/i,
  /unusual activity/i, /restricted your account|account (has been )?restricted/i, /verify (that )?you('| a)re (not )?a (human|robot)/i];
async function checkTrouble(page) {
  const url = page.url();
  if (/\/(checkpoint|authwall|uas\/login|login)/.test(new URL(url).pathname) && !/\/feed/.test(url)) throw new Fatal(`LinkedIn asked for a login or checkpoint (${new URL(url).pathname})`);
  const text = (await page.locator("body").innerText({ timeout: 5000 }).catch(() => "")).slice(0, 6000);
  const hit = TROUBLE.find((rx) => rx.test(text));
  if (hit) throw new Fatal(`LinkedIn showed: ${text.match(hit)?.[0]}`);
}

async function ensureLoggedIn(page) {
  await page.goto(LINKEDIN + "/feed/", { waitUntil: "domcontentloaded" });
  await sleep(SETTLE);
  const loggedIn = () => !/(login|authwall|signup|checkpoint)/.test(new URL(page.url()).pathname);
  if (loggedIn()) return;
  log("Please log in to LinkedIn in the open window (including any code LinkedIn sends you). Waiting up to 10 minutes…");
  await event("paused", { state: "waiting_for_login", note: "waiting for you to log in to LinkedIn in the agent window" });
  for (let i = 0; i < 120 && !loggedIn(); i++) await sleep(5000);
  if (!loggedIn()) throw new Fatal("you did not log in to LinkedIn in time");
  await page.goto(LINKEDIN + "/feed/", { waitUntil: "domcontentloaded" });
  await sleep(SETTLE);
}

async function whoAmI(page) {
  await page.goto(LINKEDIN + "/in/me/", { waitUntil: "domcontentloaded" });
  await sleep(SETTLE);
  await checkTrouble(page);
  const m = new URL(page.url()).pathname.match(/^\/in\/([^/]+)/);
  const slug = m && m[1] !== "me" ? decodeURIComponent(m[1]).toLowerCase() : "";
  if (!slug) throw new Fatal("could not tell which LinkedIn account is logged in (the /in/me page did not open your profile)");
  const heading = ((await page.locator("main h1").first().innerText({ timeout: 3000 }).catch(() => "")) || "").trim();
  const name = (heading || (await page.title()).replace(/\s*\|\s*LinkedIn\s*$/i, "").replace(/^\(\d+\)\s*/, "")).trim().slice(0, 80);
  if (ME.account && ME.account !== slug) {
    log(`The window is now logged in as ${slug} (was ${ME.account}). Work continues as ${slug}; people taken by ${ME.account} stay with it.`);
    await event("paused", { state: "account_changed", note: `account changed from ${ME.account} to ${slug}` });
  }
  Object.assign(ME, { account: slug, name, checkedAt: Date.now() });
  return ME;
}

async function claim(leadId) {
  try { await api(`/api/extension/leads/${leadId}/claim`, "POST"); return true; }
  catch (e) { if (e.status === 409) { log(`skip: ${e.message.replace(/^.*?: 409 /, "")}`); return false; } throw e; }
}

const toLocal = (url) => { try { const u = new URL(url); return LINKEDIN + u.pathname + u.search; } catch { return url; } };
const topCard = (page) => page.locator("main").first();

async function degreeOf(page) {
  const text = (await topCard(page).innerText({ timeout: 5000 }).catch(() => "")).slice(0, 1500);
  const m = text.match(/\b(1st|2nd|3rd)\b/);
  return m ? m[1] : "";
}
async function isPending(page) {
  return (await topCard(page).getByRole("button", { name: /^pending$|withdraw/i }).count()) > 0;
}

async function findConnect(page) {
  const direct = topCard(page).locator('button[aria-label*="to connect" i], button[aria-label^="Connect" i]').first();
  if (await direct.count()) return direct;
  const byText = topCard(page).getByRole("button", { name: /^connect$/i }).first();
  if (await byText.count()) return byText;
  const more = topCard(page).getByRole("button", { name: /^more( actions)?$|more actions/i }).first();
  if (await more.count()) {
    await more.click(); await sleep(600);
    const item = page.getByRole("menuitem", { name: /connect/i }).first();
    if (await item.count()) return item;
    const alt = page.locator('[role="menu"] [aria-label*="to connect" i], [role="menu"] :text-is("Connect")').first();
    if (await alt.count()) return alt;
  }
  return null;
}

async function doConnect(page, lead, note) {
  if (await isPending(page)) return { done: true, already: "pending" };
  if ((await degreeOf(page)) === "1st") return { done: true, already: "connected" };
  const connect = await findConnect(page);
  if (!connect) return { done: false, why: "no Connect button on this profile (they may only allow Follow or Message)" };
  if (DRY) return { done: false, dry: true, why: `would click Connect and send a ${note.length}-character note` };
  await connect.click(); await sleep(800);
  await checkTrouble(page);            // the weekly invitation limit appears right after Connect: stop, do not read it as "no note"
  const dialog = page.getByRole("dialog").first();
  const addNote = dialog.getByRole("button", { name: /add a note/i }).first();
  if (await addNote.count() && await addNote.isEnabled()) {
    if (note.length > 300) return { done: false, why: "the note is longer than 300 characters" };
    await addNote.click(); await sleep(500);
    await dialog.locator("textarea").first().fill(note);
  } else if (!ALLOW_NO_NOTE) {
    await page.keyboard.press("Escape");
    return { done: false, why: "LinkedIn will not let this account add a note (monthly limit). Use --allow-no-note to send without one", stopNotes: true };
  }
  await sleep(300);
  await dialog.getByRole("button", { name: /^send( invitation| now)?$|^send without a note$/i }).first().click();
  await sleep(SETTLE);
  await checkTrouble(page);
  if (await page.getByRole("dialog").count()) return { done: false, why: "the invitation box stayed open, so the request may not have been sent" };
  return { done: true };
}

async function doMessage(page, lead, text) {
  const messageButton = topCard(page).locator('button[aria-label^="Message" i], a[aria-label^="Message" i]').first();
  const button = (await messageButton.count()) ? messageButton : topCard(page).getByRole("button", { name: /^message$/i }).first();
  if (!(await button.count())) return { done: false, why: "no Message button (you may not be connected yet)" };
  if (DRY) return { done: false, dry: true, why: `would open Message and send ${text.length} characters` };
  await button.click(); await sleep(SETTLE);
  const box = page.locator('div[role="textbox"][contenteditable="true"]').last();
  if (!(await box.count())) return { done: false, why: "the message box did not open" };
  await box.click();
  await page.keyboard.insertText(text);
  await sleep(500);
  await page.getByRole("button", { name: /^send$/i }).last().click();
  await sleep(SETTLE);
  await checkTrouble(page);
  const stillTyped = ((await box.innerText().catch(() => "")) || "").trim().length > 0;
  if (stillTyped) return { done: false, why: "the message is still in the box, so it may not have been sent" };
  return { done: true };
}

// Open Profile: not a 1st-degree connection, yet LinkedIn shows a Message button (Premium members who allow free messages).
async function reportProfile(page, leadId, state) {
  if (!leadId || state.profiled[leadId]) return;
  state.profiled[leadId] = true;
  const degree = await degreeOf(page);
  const msg = topCard(page).locator('button[aria-label^="Message" i], a[aria-label^="Message" i]');
  const open = degree !== "1st" && (await msg.count()) > 0;
  await api(`/api/extension/leads/${leadId}/profile`, "POST", { open_profile: open, degree }).catch((e) => log("could not report profile:", e.message));
  if (open) log("Open Profile: can be messaged without connecting");
}

async function oneCycle(page, state) {
  if (ME.account && Date.now() - ME.checkedAt > 30 * 60 * 1000) await whoAmI(page);   // someone may have switched accounts in the window
  const ctl = await api("/api/extension/agent");
  if (!ctl.active_now) return { idle: `not now: ${ctl.why_not}` };
  const queue = await api("/api/extension/queue");
  // 1. someone waiting for acceptance? look at a few profiles (a read, no caps) at most every 6 hours each.
  for (const w of queue.waiting_for_acceptance.slice(0, 3)) {
    if (!w.profile_url || Date.now() - (state.checked[w.lead_id] || 0) < 6 * 3600 * 1000) continue;
    state.checked[w.lead_id] = Date.now();
    await page.goto(toLocal(w.profile_url), { waitUntil: "domcontentloaded" }); await sleep(SETTLE);
    await checkTrouble(page);
    await reportProfile(page, w.lead_id, state);
    if ((await degreeOf(page)) === "1st" && !DRY) {
      await api(`/api/extension/leads/${w.lead_id}/accepted`, "POST");
      await event("accepted", { lead_id: w.lead_id, note: `${w.name} accepted` });
      log(`accepted: ${w.name}`);
    }
  }                                    // looking at profiles is a read: it does not use up this cycle's action
  if (ctl.wait_seconds > 0) return { idle: `waiting ${ctl.wait_seconds}s between actions` };
  for (const item of queue.due) {
    if (!item.profile_url) continue;
    const kind = item.kind === "connect" ? "connect" : "message";
    if ((kind === "connect" && (!ctl.may_connect || state.noNotes && !ALLOW_NO_NOTE)) || (kind === "message" && !ctl.may_message)) continue;
    const info = await api(`/api/extension/match?profile_url=${encodeURIComponent(item.profile_url)}`);
    if (!info.can_send || !info.message) { log(`skip ${item.name}: ${info.reason}`); continue; }
    if (!DRY && !(await claim(item.lead_id))) continue;
    await page.goto(toLocal(item.profile_url), { waitUntil: "domcontentloaded" }); await sleep(SETTLE);
    await checkTrouble(page);
    await reportProfile(page, item.lead_id, state);
    const result = kind === "connect" ? await doConnect(page, item, info.message.text) : await doMessage(page, item, info.message.text);
    if (result.dry) { log(`DRY ${item.name}: ${result.why}`); return { acted: "dry" }; }
    if (!result.done) {
      log(`could not ${kind} ${item.name}: ${result.why}`);
      if (result.stopNotes) state.noNotes = true;
      await event("error", { lead_id: item.lead_id, note: `${kind} ${item.name}: ${result.why}` });
      continue;
    }
    await api(`/api/extension/leads/${item.lead_id}/sent`, "POST", { step: info.message.step });
    if (result.already === "connected") await api(`/api/extension/leads/${item.lead_id}/accepted`, "POST").catch(() => {});
    await event(kind, { lead_id: item.lead_id, note: `${kind === "connect" ? "connection request" : "message"} to ${item.name}${result.already ? ` (${result.already})` : ""}` });
    log(`${kind === "connect" ? "connection request" : "message"} sent: ${item.name}`);
    return { acted: kind };
  }
  return { idle: "nothing due that is allowed right now" };
}

async function main() {
  const context = await chromium.launchPersistentContext(PROFILE_DIR, { headless: HEADLESS, viewport: { width: 1280, height: 900 } });
  const page = context.pages()[0] || await context.newPage();
  const state = { checked: {}, noNotes: false, profiled: {} };
  let exitCode = 0;
  try {
    await event("started", { state: "running", note: DRY ? "started in dry-run mode" : "agent started" });
    if (!args["skip-login-check"]) await ensureLoggedIn(page);
    if (!args["no-account"]) { await whoAmI(page); log(`Logged in to LinkedIn as ${ME.name || ME.account} (${ME.account}).`); }
    log(DRY ? "Dry run: nothing will be clicked." : "Running. Press Ctrl+C to stop.");
    for (;;) {
      const outcome = await oneCycle(page, state);
      if (outcome.idle) log(outcome.idle);
      if (ONCE && outcome.acted) break;
      if (ONCE && outcome.idle) break;
      await sleep(outcome.acted ? 1000 : POLL);
    }
  } catch (e) {
    if (e instanceof Fatal) {
      log("STOPPING:", e.message);
      await event("error", { fatal: true, note: e.message, state: "stopped" });
      exitCode = 3;
    } else { log("error:", e.message); await event("error", { note: e.message.slice(0, 200), state: "error" }); exitCode = 1; }
  } finally {
    await context.close();
  }
  process.exit(exitCode);
}
main();
