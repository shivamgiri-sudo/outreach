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
//
// RULE ABOVE ALL: never act on the wrong person, never act twice, never report what did not happen. When anything is unclear
// (the page is not the lead's, the chat is not theirs, a draft is in the box, a send cannot be proven) it does NOTHING and logs why.
//
// --cloud: the same agent started by tools/linkedin_cloud_supervisor.mjs (headless, on the portal's server) or by
// tools/linkedin_runner.mjs (on an always-on office computer, a visible window by default: --headed). It is logged in from
// the portal (tools/linkedin_cloud_login.mjs) instead of by a person at the window.
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { readInbox as readInboxList } from "./linkedin_agent_inbox.mjs";
import { cloudLogin } from "./linkedin_cloud_login.mjs";
import { chatWith, closeChats, degreeOf, identityProblem, labelledAll, linkHeal, nameProblem, norm, observe, ourCopies, readHistory, readThread, slugOf, topCard } from "./linkedin_guard_dom.mjs";

let chromium;
try { ({ chromium } = await import("playwright")); }
catch {
  const dir = process.env.MCN_PLAYWRIGHT_DIR;        // the server keeps Playwright in its own small folder (deploy/setup_cloud_linkedin.sh)
  const where = dir ? pathToFileURL(path.join(dir, "node_modules", "playwright", "index.mjs")).href
    : new URL("../web/node_modules/playwright/index.mjs", import.meta.url).href;
  ({ chromium } = await import(where));
}

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => {
  if (a.startsWith("--")) acc.push([a.slice(2), all[i + 1] && !all[i + 1].startsWith("--") ? all[i + 1] : true]);
  return acc;
}, []));
const PORTAL = String(args.portal || process.env.MCN_PORTAL || "").replace(/\/$/, "");
const TOKEN = String(args.token || process.env.MCN_TOKEN || "");
const PROFILE_DIR = String(args.profile || path.join(os.homedir(), ".mcn-linkedin-profile"));
const LINKEDIN = String(args["linkedin-base"] || process.env.MCN_LINKEDIN_BASE || "https://www.linkedin.com").replace(/\/$/, "");   // only tests change this
const DRY = !!args.dry;                 // look at the page and say what would be sent to whom: no click, no report, no side effect
const ONCE = !!args.once;               // do at most one action and exit (tests, or a first check)
const ALLOW_NO_NOTE = !!args["allow-no-note"];      // send a request without a note when LinkedIn will not allow one
const CLOUD = !!args.cloud;             // on the server: headless, logged in from the portal
const HEADLESS = args.headed ? false : !!args.headless || CLOUD;   // local use needs the window so you can log in; the office runner shows it too
const SETTLE = Number(args.settle || 1500);          // fixed wait after a page changes
const POLL = Number(args.poll || 20) * 1000;
const LOGIN_POLL = Number(args["login-poll"] || 3);
let INSTANCE = crypto.randomUUID();     // this agent (see acquireLock): the portal lets only one instance work on a person at a time

if (!PORTAL || !TOKEN) { console.error("Usage: node tools/linkedin_agent.mjs --portal https://your-portal --token mcnx_..."); process.exit(2); }
const log = (...a) => console.log(new Date().toLocaleTimeString(), ...a);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
if (LINKEDIN !== "https://www.linkedin.com") log(`LinkedIn address: ${LINKEDIN} (test stand-in)`);

// Which LinkedIn account this window is logged in to (its /in/ slug). The portal keeps caps, the gap and the safety
// pause per account, and gives each person to one account only, so several accounts can run side by side.
const ME = { account: "", name: "", checkedAt: 0 };
async function api(pathname, method = "GET", body) {
  const res = await fetch(PORTAL + pathname, { method, headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json", "X-Agent-Instance": INSTANCE,
    ...(ME.account ? { "X-LinkedIn-Account": ME.account, "X-LinkedIn-Name": encodeURIComponent(ME.name).slice(0, 200) } : {}) },
    body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(30000) });
  if (!res.ok) { const text = await res.text(); const err = new Error(`${method} ${pathname}: ${res.status} ${text.slice(0, 160)}`); err.status = res.status; throw err; }
  return res.json();
}
const event = (kind, extra = {}) => api("/api/extension/agent/event", "POST", { kind, ...extra }).catch((e) => log("could not report:", e.message));

class Fatal extends Error {}
class LoginNeeded extends Fatal {}        // a login wall: locally a stop, in the cloud a new login from the portal
const cloudState = (state, note = "") => api("/api/extension/cloud/state", "POST", { state, note, account: ME.account, name: ME.name })
  .catch((e) => log("could not report state:", e.message));

// ---- one agent per browser profile -------------------------------------------------------------------------------------
// A second agent on the same profile (a double click, a stuck old process) would act on the same people. The portal's lease
// (X-Agent-Instance) covers two computers; this lock covers two processes on this one.
const LOCK_FILE = path.join(PROFILE_DIR, "mcn-agent.lock");
const alive = (pid) => { try { process.kill(pid, 0); return true; } catch (e) { return e.code === "EPERM"; } };
async function acquireLock() {
  fs.mkdirSync(PROFILE_DIR, { recursive: true });
  for (let i = 0; ; i++) {
    let held = null;
    try { held = JSON.parse(fs.readFileSync(LOCK_FILE, "utf8")); } catch { /* none, or unreadable: take it */ }
    if (!held || held.pid === process.pid || !alive(held.pid) || Date.now() - held.at > 90000) break;
    if (i >= 10) { console.error(`Another agent (process ${held.pid}) is already using this browser profile; stopping so nobody acts twice.`); process.exit(1); }
    await sleep(1000);
  }
  // The id survives a restart of the SAME agent (same profile, same computer), or the portal's lease would lock a crashed agent
  // out of its own person for ten minutes; another computer or a copied profile gets a new id.
  const idFile = path.join(PROFILE_DIR, "mcn-agent-instance.json");
  try { const k = JSON.parse(fs.readFileSync(idFile, "utf8")); if (k.host === os.hostname() && /^[\w-]{8,64}$/.test(k.id)) INSTANCE = k.id; else throw new Error("new"); }
  catch { try { fs.writeFileSync(idFile, JSON.stringify({ id: INSTANCE, host: os.hostname() })); } catch { /* read-only disk: a per-process id still works */ } }
  const write = () => { try { fs.writeFileSync(LOCK_FILE, JSON.stringify({ pid: process.pid, instance: INSTANCE, at: Date.now() })); } catch { /* read-only disk: the portal lease still holds */ } };
  write();
  setInterval(write, 20000).unref();
  process.on("exit", () => { try { const h = JSON.parse(fs.readFileSync(LOCK_FILE, "utf8")); if (h.pid === process.pid) fs.rmSync(LOCK_FILE, { force: true }); } catch { /* gone already */ } });
}

// ---- reports that must not be lost -----------------------------------------------------------------------------------
// The action is done; telling the portal can fail (network, 5xx). The report is kept on disk next to the profile and posted
// again at the next start BEFORE anything new is done, so a lost answer can never turn into a second send.
const PENDING_FILE = path.join(PROFILE_DIR, "mcn-pending-sent.json");
const readPending = () => { try { const v = JSON.parse(fs.readFileSync(PENDING_FILE, "utf8")); return Array.isArray(v) ? v : []; } catch { return []; } };
const writePending = (rows) => {
  try { fs.mkdirSync(PROFILE_DIR, { recursive: true }); if (rows.length) fs.writeFileSync(PENDING_FILE, JSON.stringify(rows)); else fs.rmSync(PENDING_FILE, { force: true }); }
  catch (e) { log("could not save the pending report:", e.message); }
};
// "ok" | "refused" (the portal said no: do not retry) | "later" (still unreachable: kept)
async function deliverSent(entry, tries) {
  for (let i = 1; ; i++) {
    try { await api(`/api/extension/leads/${entry.lead_id}/sent`, "POST", entry.body); return "ok"; }
    catch (e) {
      if (e.status && e.status < 500 && e.status !== 408 && e.status !== 429) { log(`the portal did not accept the report for ${entry.name}: ${e.message}`); return "refused"; }
      if (i >= tries) return "later";
      log(`could not report the send for ${entry.name} (try ${i}): ${e.message}`);
      await sleep(2000 * i);
    }
  }
}
async function afterSent(entry) {
  if (entry.accepted) await api(`/api/extension/leads/${entry.lead_id}/accepted`, "POST").catch(() => {});
  await event(entry.kind, entry.event);
}
async function flushPending() {
  for (const entry of readPending()) {
    const out = await deliverSent(entry, 2);
    if (out === "later") return false;
    writePending(readPending().filter((e) => !(e.lead_id === entry.lead_id && e.body.step === entry.body.step)));
    if (out === "ok") { log(`reported the earlier ${entry.kind} to ${entry.name}`); await afterSent(entry); }
  }
  return true;
}

const TROUBLE = [/security (check|verification)/i, /quick security check/i, /weekly invitation limit/i, /reached the weekly/i,
  /unusual activity/i, /restricted your account|account (has been )?(temporarily )?restricted|temporarily restricted/i,
  /verify (that )?you('| a)re (not )?a (human|robot|real person)/i, /verify your identity/i, /complete this (puzzle|security)/i, /too many requests/i];
async function checkTrouble(page) {
  const url = page.url();
  const where = new URL(url).pathname;
  if (/\/checkpoint/.test(where) && !/\/feed/.test(url)) throw new Fatal(`LinkedIn asked for a checkpoint (${where})`);
  if (/\/(authwall|uas\/login|login)/.test(where) && !/\/feed/.test(url)) throw new LoginNeeded(`LinkedIn asked for a login (${where})`);
  // A profile's own text (headline, About), a search result row or a chat preview can say "security verification" or "unusual
  // activity" about someone else: only the title, headings, dialogs, alerts, toasts and loose page text count.
  const text = await page.evaluate(() => {
    const keep = [document.title, ...[...document.querySelectorAll('h1, h2, [role="dialog"], [role="alertdialog"], [role="alert"], .artdeco-toast-item')].map((e) => e.innerText || "")];
    const c = document.body.cloneNode(true);
    c.querySelectorAll('section, li, aside, script, style, [class*="msg-"], [role="dialog"]').forEach((n) => n.remove());
    return [...keep, c.textContent || ""].join("\n").replace(/\s+/g, " ").slice(0, 8000);
  }).catch(() => "");
  {                                                                     // logged-out page at a normal URL (public profile with a login wall)
    const wall = await page.locator('a[href*="/login"], a[href*="/uas/login"], form[action*="login-submit"], input[name="session_key"]').count().catch(() => 0);
    if (wall && !(await page.locator("nav.global-nav, #global-nav").count().catch(() => 0))) throw new LoginNeeded("LinkedIn shows its sign-in page instead of the profile (logged out)");
  }
  const hit = TROUBLE.find((rx) => rx.test(text));
  if (hit) throw new Fatal(`LinkedIn showed: ${text.match(hit)?.[0]}`);
}

async function ensureLoggedIn(page) {
  await page.goto(LINKEDIN + "/feed/", { waitUntil: "domcontentloaded" });
  await sleep(SETTLE);
  const loggedIn = () => !/(login|authwall|signup|checkpoint)/.test(new URL(page.url()).pathname);
  if (loggedIn()) return;
  if (CLOUD) {
    // A visible window (the office runner) has a person nearby: they may solve a CAPTCHA or log in by hand in it, and the
    // agent carries on by itself. --person-at-window forces that for the tests, which run headless.
    await cloudLogin({ page, api, base: LINKEDIN, log, sleep, settle: SETTLE, pollSeconds: LOGIN_POLL,
      personAtWindow: !HEADLESS || !!args["person-at-window"], personWaitSeconds: Number(args["person-wait"] || 900) });
    await page.goto(LINKEDIN + "/feed/", { waitUntil: "domcontentloaded" });
    await sleep(SETTLE);
    if (!loggedIn()) throw new LoginNeeded("LinkedIn sent the browser back to the login page right after logging in");
    return;
  }
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
  const lang = ((await page.evaluate(() => document.documentElement.lang || "").catch(() => "")) || "").toLowerCase();
  if (lang && !lang.startsWith("en")) throw new Fatal(`LinkedIn for this account is set to "${lang}", not English. The agent reads English button names only: switch the account's LinkedIn language to English, then press Resume`);
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

// Opens a profile and waits until the card is drawn (LinkedIn fills it in after domcontentloaded). HTTP 999 / 429 = LinkedIn refuses us.
async function openProfile(page, url) {
  const res = await page.goto(url, { waitUntil: "domcontentloaded" });
  const status = res ? res.status() : 0;
  if (status === 999 || status === 429) throw new Fatal(`LinkedIn is refusing requests (HTTP ${status}): the account or this computer is rate-limited`);
  await sleep(SETTLE);
  await checkTrouble(page);
  if (!(await page.locator("main h1").first().waitFor({ timeout: 15000 }).then(() => true, () => false))) return false;
  await page.waitForFunction(() => { const m = document.querySelector("main"); return !!m && m.querySelector("button, a[role='button'], a[aria-label]") !== null; }, null, { timeout: 10000 }).catch(() => {});
  await sleep(500);
  return true;
}
// The portal's answer to "may I act on this person": ok, or the reason it said no (409). Anything else is an error.
async function claim(leadId) {
  try { await api(`/api/extension/leads/${leadId}/claim`, "POST"); return ""; }
  catch (e) { if (e.status === 409) return e.message.replace(/^.*?: 409 /, "").replace(/^\{"detail":"|"\}$/g, "") || "refused"; throw e; }
}

const toLocal = (url) => { try { const u = new URL(url); return LINKEDIN + u.pathname + u.search; } catch { return url; } };
// Click the first candidate that takes the click; something covering a button (a banner, the sticky header) moves on.
async function clickOne(candidates) {
  for (const el of candidates) {
    try { await el.click({ timeout: 5000 }); return true; } catch { /* covered or gone: try the next copy */ }
  }
  return false;
}

// Buttons only from the lead's own card, its open menu or the sticky header (see labelledAll in linkedin_guard_dom.mjs).
const labelled = async (page, selector, name) => (await labelledAll(page, selector, name))[0] || null;
async function isPending(page, name) {
  if (await labelled(page, 'button[aria-label*="pending" i], button[aria-label*="withdraw" i]', name)) return true;
  return (await topCard(page).getByRole("button", { name: /^pending\b/i }).count()) > 0;
}
async function findMessage(page, name) {
  const own = await labelled(page, 'button[aria-label^="Message" i], a[aria-label^="Message" i]', name);   // on-profile copy first
  if (own) return own;
  const inCard = topCard(page).getByRole("button", { name: /^message$/i }).or(topCard(page).getByRole("link", { name: /^message$/i })).first();
  return (await inCard.count()) && (await inCard.isVisible().catch(() => false)) ? inCard : null;
}
async function findConnect(page, name) {
  const own = await labelledAll(page, 'button[aria-label*="to connect" i], a[aria-label*="to connect" i]', name);
  if (own.length) return own;
  const inCard = topCard(page).getByRole("button", { name: /^connect$/i }).first();
  if (await inCard.count() && await inCard.isVisible().catch(() => false)) return [inCard];
  const more = topCard(page).getByRole("button", { name: /^more( actions)?$|more actions/i }).first();
  if (await more.count()) {
    await more.click(); await sleep(600);
    const items = await labelledAll(page, '[role="menu"] [aria-label*="to connect" i], [role="menuitem"][aria-label*="to connect" i]', name);
    if (items.length) return items;
    const plain = page.locator('[role="menu"]').getByText("Connect", { exact: true }).first();     // menu we opened from their own card
    if (await plain.count()) return [plain];
    await page.keyboard.press("Escape");
  }
  return null;
}

let portalAllowsNoNote = false;                   // the portal switch "send without a note" (control.allow_no_note)
let notesBlockedUntil = 0;                       // LinkedIn showed it will not take a note (monthly limit): go bare for a day
const INVITE_BUTTONS = /add a note|send without a note|^send( invitation| now)?$/i;
const tap = async (loc, ms = 8000) => { try { await loc.click({ timeout: ms }); return true; } catch { return false; } };

async function closeDialogs(page) {
  for (let i = 0; i < 3 && (await page.getByRole("dialog").count()); i++) { await page.keyboard.press("Escape"); await sleep(400); }
}

// The invitation box LinkedIn opens after Connect: the dialog that holds "Add a note" / "Send without a note" / "Send".
async function inviteDialog(page) {
  const d = page.getByRole("dialog").filter({ has: page.getByRole("button", { name: INVITE_BUTTONS }) }).first();
  await d.waitFor({ timeout: 8000 }).catch(() => {});
  return (await d.count()) ? d : null;
}

// ctx = { recheck(): "" or the reason to stop, clicked: true once a Send was pressed }.  recheck() is run right before every
// click that acts: the portal still allows it (not paused / stopped / over a limit), the lead's step is unchanged, the page is
// still the lead's, and no new browser tab has popped up.
// Connect with the note first. When LinkedIn will not take a note (free accounts: a few a month) it either disables
// "Add a note" or answers it with a Premium offer and no text box; then, only if "send without a note" is allowed,
// the request is sent bare. Every wait is bounded, so one person can never hold the agent.
async function doConnect(page, lead, note, ctx, retried = false, shortNote = "") {
  if (await isPending(page, lead.name)) return { done: true, already: "pending", proof: await observe(page) };
  if ((await degreeOf(page)) === "1st") return { done: true, already: "connected", proof: await observe(page) };
  const connect = await findConnect(page, lead.name);
  if (!connect || !connect.length) return { done: false, why: "no Connect button on this profile (they may only allow Follow or Message)" };
  if (DRY) { await page.keyboard.press("Escape").catch(() => {}); return { done: false, dry: true, why: `would click Connect and send a ${note.length}-character note`, wouldSend: note }; }
  const stop = await ctx.recheck();
  if (stop) { await page.keyboard.press("Escape").catch(() => {}); return { done: false, why: `${stop}; nothing was sent` }; }
  if (!(await clickOne(connect))) return { done: false, why: "the Connect button for this person was covered by something on the page; nothing was sent" };
  await sleep(800);
  await checkTrouble(page);            // the weekly invitation limit appears right after Connect: stop, do not read it as "no note"
  const dialog = await inviteDialog(page);
  if (!dialog) {                         // some profiles send at once, with no box: look before saying "nothing was sent"
    await closeDialogs(page); await page.reload({ waitUntil: "domcontentloaded" }).catch(() => {}); await sleep(SETTLE);
    if (await isPending(page, lead.name)) return { done: true, already: "pending", proof: await observe(page) };
    return { done: false, why: "the invitation box did not open; nothing was sent" };
  }
  const allowBare = ALLOW_NO_NOTE || portalAllowsNoNote;
  const addNote = dialog.getByRole("button", { name: /add a note/i }).first();
  const notesOk = Date.now() > notesBlockedUntil && (await addNote.count()) && (await addNote.isEnabled().catch(() => false));
  let text = "";
  if (notesOk) {
    if (note.length > 300) { await closeDialogs(page); return { done: false, why: "the note is longer than 300 characters" }; }
    await tap(addNote, 5000); await sleep(600);
    const box = page.getByRole("dialog").locator("textarea").first();
    const opened = await box.waitFor({ timeout: 5000 }).then(() => true, () => false);
    if (!opened) {                     // a Premium offer instead of a text box: the monthly note limit
      notesBlockedUntil = Date.now() + 24 * 3600 * 1000;
      await closeDialogs(page);
      if (!allowBare) return { done: false, why: "LinkedIn will not let this account add a note (monthly limit). Tick \"send without a note\" under Limits to send it anyway", stopNotes: true };
      if (retried) return { done: false, why: "LinkedIn would not take a note and the request could not be sent without one" };
      await page.reload({ waitUntil: "domcontentloaded" }); await sleep(SETTLE);
      log("LinkedIn will not take a note this month: sending without one");
      return doConnect(page, lead, note, ctx, true, shortNote);
    }
    // The box's real limit (free accounts: 200). Use the short note when the long one does not fit; never cut a sentence.
    const limit = await box.getAttribute("maxlength").then((v) => parseInt(v, 10) || 0).catch(() => 0);
    text = note;
    if (limit && text.length > limit) {
      if (shortNote && shortNote.length <= limit) { text = shortNote; log(`the box takes ${limit} characters: sending the short note`); }
      else if (allowBare && !retried) {
        await closeDialogs(page); await page.reload({ waitUntil: "domcontentloaded" }); await sleep(SETTLE);
        log(`the box takes ${limit} characters and no note fits: sending without one`);
        notesBlockedUntil = Date.now() + 24 * 3600 * 1000;
        return doConnect(page, lead, note, ctx, true, shortNote);
      } else { await closeDialogs(page); return { done: false, why: `LinkedIn takes only ${limit} characters in the note and no saved note is that short; nothing was sent` }; }
    }
    // The note must really be in ITS box: a bare request is never sent by accident (that needs the portal's "send without a note").
    const typed = await box.fill(text, { timeout: 5000 }).then(() => true, () => false);
    if (!typed || (await box.inputValue().catch(() => "")) !== text) { await closeDialogs(page); return { done: false, why: "the note could not be typed into the box; nothing was sent" }; }
    await sleep(300);
    const send = page.getByRole("dialog").getByRole("button", { name: /^send( invitation| now)?$/i }).first();
    const stop2 = await ctx.recheck();
    if (stop2) { await closeDialogs(page); return { done: false, why: `${stop2}; nothing was sent` }; }
    ctx.proof = { ...(await observe(page)), text_sent: text };
    if (!(await tap(send))) { await closeDialogs(page); return { done: false, why: "the Send button in the invitation box could not be pressed; nothing was sent" }; }
    ctx.clicked = true;
  } else {
    if (await dialog.locator('input[type="email"], input[name="email"]').count()) {      // "enter their email to connect": this person only
      await closeDialogs(page);
      return { done: false, why: "LinkedIn asks for this person's email address before it takes an invitation; skipped, nothing was sent" };
    }
    if (!allowBare) {
      await closeDialogs(page);
      return { done: false, why: "LinkedIn will not let this account add a note (monthly limit). Tick \"send without a note\" under Limits to send it anyway", stopNotes: true };
    }
    const bare = dialog.getByRole("button", { name: /send without a note/i }).first();
    const send = (await bare.count()) ? bare : dialog.getByRole("button", { name: /^send( invitation| now)?$/i }).first();
    const stop2 = await ctx.recheck();
    if (stop2) { await closeDialogs(page); return { done: false, why: `${stop2}; nothing was sent` }; }
    ctx.proof = await observe(page);
    if (!(await tap(send))) { await closeDialogs(page); return { done: false, why: "the Send button in the invitation box could not be pressed; nothing was sent" }; }
    ctx.clicked = true;
  }
  await sleep(SETTLE);
  await checkTrouble(page);
  if (await page.getByRole("dialog").filter({ has: page.getByRole("button", { name: INVITE_BUTTONS }) }).count()) {
    await closeDialogs(page);
    return { done: false, why: "the invitation box stayed open, so the request may not have been sent" };
  }
  return { done: true, bare: !notesOk || retried, proof: ctx.proof };
}

// A message must only ever go into the conversation with THIS person, and only to a 1st-degree connection (or an Open Profile).
// LinkedIn keeps chat windows open between pages, so every chat window is closed first; then the one that opened must have this
// person's name in its header. Delivery is proven by the text appearing in that conversation as OURS, never by "the box is empty".
const clearBox = async (page, box) => { await box.click({ timeout: 3000 }).catch(() => {}); await page.keyboard.press("ControlOrMeta+A").catch(() => {}); await page.keyboard.press("Delete").catch(() => {}); };
async function doMessage(page, lead, text, ctx, step = 0, openProfile = false) {
  if (!norm(text)) return { done: false, why: "the message text is empty; nothing was sent" };
  const degree = await degreeOf(page);
  if (degree !== "1st" && !openProfile) return { done: false, why: `not shown as a 1st-degree connection (${degree || "no degree shown"}) and not an Open Profile: no message` };
  const button = await findMessage(page, lead.name);
  if (!button) return { done: false, why: "no Message button for this person (you may not be connected yet)" };
  if (degree !== "1st" && await button.evaluate((e) => !!e.querySelector('svg[data-test-icon*="lock"]') || /inmail|premium/i.test(e.getAttribute("aria-label") || "")).catch(() => false)) return { done: false, why: "this Message button is Premium InMail (they are not a connection); nothing was sent" };
  if (DRY) return { done: false, dry: true, why: `would open Message and send ${text.length} characters`, wouldSend: text };
  await closeChats(page);                                       // no other person's chat window may be open next to this one
  const stop = await ctx.recheck();
  if (stop) return { done: false, why: `${stop}; nothing was sent` };
  if (!(await clickOne([button]))) return { done: false, why: "the Message button was covered by something on the page; nothing was sent" };
  await sleep(SETTLE);
  if (/\/messaging\/(thread|compose)/.test(new URL(page.url()).pathname)) { await page.goBack({ waitUntil: "domcontentloaded" }).catch(() => {}); return { done: false, why: "LinkedIn opened the full-screen messaging page instead of the chat window (not supported); nothing was sent" }; }
  const chat = await chatWith(page, lead);
  if (!chat.bubble) { await closeChats(page); return { done: false, why: `${chat.why}; nothing was sent` }; }
  const box = chat.bubble.locator('div[role="textbox"][contenteditable="true"]').last();
  if (!(await box.count())) return { done: false, why: "the message box did not open" };
  const hist = await readHistory(chat.bubble, step > 2);
  if (hist.unloaded) { await closeChats(page); return { done: false, why: "the chat history did not finish loading, so a reply could not be ruled out; nothing was sent" }; }
  const theirs = await readThread(chat.bubble, lead, ME.name);
  if (theirs.length) { await page.keyboard.press("Escape"); return { done: false, replied: theirs.join("\n\n") }; }
  const before = await ourCopies(chat.bubble, text);
  if (before.ok) return { done: true, already: "already in the chat", proof: { ...(await observe(page, chat.title)), text_sent: text } };   // an earlier try did get through: never twice
  // A draft that is not ours is somebody's own writing: it is never typed over or sent. Our own earlier unsent text is cleared.
  const draft = norm(await box.innerText().catch(() => ""));
  if (draft) {
    const mine = norm(text);
    const ours = (draft.length >= 20 && mine.startsWith(draft)) || (mine.length >= 20 && draft.startsWith(mine.slice(0, 40)));
    if (!ours) return { done: false, why: "a draft that is not ours is already in the message box; nothing was typed or sent" };
    log("clearing our own earlier unsent text from the message box");
    await clearBox(page, box);
    if (norm(await box.innerText().catch(() => ""))) return { done: false, why: "the old draft could not be cleared from the message box; nothing was sent" };
  }
  await box.click({ timeout: 5000 });
  await page.keyboard.insertText(text);
  await sleep(500);
  if (norm(await box.innerText().catch(() => "")) !== norm(text)) {
    await clearBox(page, box);
    return { done: false, why: "the text did not land in this person's message box as written; nothing was sent" };
  }
  const stop2 = await ctx.recheck();
  if (stop2) { await clearBox(page, box); return { done: false, why: `${stop2}; nothing was sent` }; }
  const recheckChat = await chatWith(page, lead);              // still exactly this person's chat?
  if (!recheckChat.bubble) { await closeChats(page); return { done: false, why: `${recheckChat.why}; nothing was sent` }; }
  ctx.proof = { ...(await observe(page, chat.title)), text_sent: text };
  if (!(await tap(chat.bubble.getByRole("button", { name: /^send$/i }).last()))) { await clearBox(page, box); return { done: false, why: "the Send button could not be pressed (Enter-to-send, or Send is disabled); nothing was sent" }; }
  ctx.clicked = true;
  for (let i = 0; i < 30; i++) {                                 // delivered = our text is in THIS conversation as ours (a slow network is not a failure)
    const now = await ourCopies(chat.bubble, text);
    if (now.ok > before.ok) { await checkTrouble(page); return { done: true, proof: ctx.proof }; }
    if (now.failed > before.failed) return { done: false, why: "LinkedIn shows the message as not delivered; not recorded as sent" };
    await sleep(500);
  }
  await checkTrouble(page);
  return { done: false, uncertain: true, why: "the message did not appear in the conversation within 15 s, so it may not have been sent (the chat is checked again before any retry)" };
}

// Profile lookup for a lead that has no LinkedIn link. ONE lead per cycle, only when the portal says may_lookup (switch,
// hours, pause, platform stop, the gap shared with every other action, and the daily lookups number). It opens the people
// search for "<name> <company>" ONCE and reads only the visible cards of that first page (no scrolling, no next page, no
// clicking into profiles). The portal decides which card, if any, is the person; nothing is accepted here.
const NOISE = /^([^\w]*(1st|2nd|3rd\+?)|[^\p{L}\p{N}]+|connect|message|follow|pending|view .*profile|status is .*|mutual connection.*)$/i;
function readCards(page) {
  return page.evaluate((noise) => {
    const rx = new RegExp(noise, "iu"), out = [], seen = new Set();
    for (const a of document.querySelectorAll('main a[href*="/in/"]')) {
      const m = (a.getAttribute("href") || "").match(/\/in\/([^/?#]+)/);
      const box = a.closest("li");
      if (!m || !box || seen.has(m[1]) || !a.getClientRects().length) continue;
      seen.add(m[1]);
      // The link holds the name twice (a visible copy and a screen-reader copy): take the visible one, else collapse the repeat.
      const shown = (a.querySelector('[aria-hidden="true"]') || {}).textContent || a.innerText || "";
      const nm = shown.replace(/\s+/g, " ").trim().replace(/^(.{2,}?) \1$/, "$1").replace(/^(.{2,}?)\1$/, "$1");
      const lines = nm ? [nm] : [];
      for (const raw of box.innerText.split("\n")) {
        if (nm && raw.replace(/\s+/g, " ").trim().replace(/\s*[•·].*$/, "").replace(/^(.{2,}?)\s?\1$/, "$1") === nm) continue;
        const t = raw.replace(/\s*[•·]\s*(1st|2nd|3rd\+?)\s*$/i, "").trim();
        if (t && !rx.test(t) && lines[lines.length - 1] !== t) lines.push(t);
      }
      out.push({ name: lines[0] || "", headline: lines[1] || "", location: lines[2] || "", url: new URL(a.getAttribute("href"), location.href).pathname.replace(/^/, "https://www.linkedin.com") });
      if (out.length >= 10) break;
    }
    return out;
  }, NOISE.source);
}
async function lookupStep(page, ctl) {
  if (DRY || !ctl.may_lookup) return null;
  const { lead } = await api("/api/extension/lookup/next");
  if (!lead) return null;
  await page.goto(LINKEDIN + "/search/results/people/?keywords=" + encodeURIComponent(`${lead.name} ${lead.company}`.trim()), { waitUntil: "domcontentloaded" });
  await sleep(SETTLE);
  await checkTrouble(page);
  let cards = [], error = "";
  try {
    await page.locator('main a[href*="/in/"]').first().waitFor({ timeout: 10000 }).catch(() => {});      // the cards load after domcontentloaded
    cards = await readCards(page);
    if (!cards.length) {
      const shown = (await page.locator("main").innerText({ timeout: 3000 }).catch(() => "")).slice(0, 3000);
      if (/commercial use limit|search limit|upgrade to premium/i.test(shown)) error = "LinkedIn shows its search limit page (commercial use limit); searched again later";
      else if (!/no results found|no people found|couldn.?t find/i.test(shown)) error = "the search page showed neither people nor 'No results found' (still loading?); searched again later";
    }
  } catch (e) { error = `could not read the search page (${String(e.message || e).split("\n")[0].slice(0, 100)})`; }
  try {
    const out = await api(`/api/extension/leads/${lead.lead_id}/lookup-result`, "POST", { cards, error });
    log(`profile search for ${lead.name}: ${cards.length} people on the first page -> ${out.status}`);
  } catch (e) { log("could not report the search:", e.message); }
  return { acted: "lookup" };
}

// The messaging list (tools/linkedin_agent_inbox.mjs): rows from them, with the profile link when it can be read safely.
const readInbox = (page, watching) => readInboxList(page, { linkedin: LINKEDIN, settle: SETTLE, log, checkTrouble, watching });

// Open Profile: not a 1st-degree connection, yet LinkedIn shows a (free) Message button (Premium members who allow free messages).
// Only called on a page that is confirmed to be the lead's, and never in a dry run.
async function reportProfile(page, leadId, state, name) {
  if (!leadId || state.profiled[leadId]) return;
  state.profiled[leadId] = true;
  const degree = await degreeOf(page);
  const msgBtn = degree !== "1st" ? await findMessage(page, name) : null;
  const inmail = msgBtn ? await msgBtn.evaluate((e) => !!e.querySelector('svg[data-test-icon*="lock"], [data-test-icon*="premium"]') || /inmail|premium/i.test(e.getAttribute("aria-label") || "")).catch(() => false) : false;
  const open = !!msgBtn && !inmail && !!degree;
  await api(`/api/extension/leads/${leadId}/profile`, "POST", { open_profile: open, degree }).catch((e) => log("could not report profile:", e.message));
  if (open) log("Open Profile: can be messaged without connecting");
}

// The page shows the lead's exact name at another address (member id replaced by a vanity link, or a renamed slug): tell the portal
// what was seen; it rewrites the lead's link only when that is provably the same person. Nothing is clicked. true = healed.
async function healLink(page, item) {
  if (DRY) return false;
  const seen = await linkHeal(page, item).catch(() => null);
  if (!seen) return false;
  const out = await api(`/api/extension/leads/${item.lead_id}/profile`, "POST", seen).catch((e) => { log("could not report the link:", e.message); return null; });
  if (out && out.healed) { log(`link of ${item.name} updated to ${out.url} (same person: page name matched); acted on next round`); return true; }
  return false;
}

const POPUP = { at: 0 };                  // a second browser tab opened by the page (closed at once)
// Say once per 6 h why a person was skipped, so the owner can see it in the portal (the portal also drops repeats).
async function skipNote(state, item, why) {
  log(`skip ${item.name}: ${why}`);
  const key = `${item.lead_id}|${why}`;
  if (Date.now() - (state.skipped[key] || 0) < 6 * 3600 * 1000) return;
  state.skipped[key] = Date.now();
  await event("skip", { lead_id: item.lead_id, note: `skip ${item.name}: ${why}`.slice(0, 280) });
}

async function oneCycle(page, state) {
  if (!(await flushPending())) return { idle: "the report of an earlier send has not reached the portal yet; nothing new is done until it has" };
  if (ME.account && Date.now() - ME.checkedAt > 30 * 60 * 1000) await whoAmI(page);   // someone may have switched accounts in the window
  const ctl = await api("/api/extension/agent");
  if (!ctl.active_now) return { idle: `not now: ${ctl.why_not}` };
  const queue = await api("/api/extension/queue");
  // 1. someone waiting for acceptance? look at a few profiles (a read, no caps) at most every 6 hours each.
  // Only people not looked at in the last 6 hours, longest-waiting first, before taking 3: otherwise the same first 3
  // people fill the slice every cycle and everyone behind them is never checked (acceptances were missed).
  const toCheck = queue.waiting_for_acceptance
    .filter((w) => w.profile_url && Date.now() - (state.checked[w.lead_id] || 0) >= 6 * 3600 * 1000)
    .sort((a, b) => String(a.since || "").localeCompare(String(b.since || "")))
    .slice(0, 3);
  for (const w of toCheck) {
    state.checked[w.lead_id] = Date.now();
    let drawn = false;
    try { drawn = await openProfile(page, toLocal(w.profile_url)); } catch (e) { if (e instanceof Fatal) throw e; log(`could not open ${w.name}'s profile (${String(e.message || e).split("\n")[0].slice(0, 100)}); looking again later`); continue; }
    if (!drawn) { log(`${w.name}'s profile did not finish loading; acceptance not checked this time`); state.checked[w.lead_id] = Date.now() - 5 * 3600 * 1000; continue; }
    const who = await identityProblem(page, { name: w.name, profile_url: w.profile_url });   // "1st" on somebody else's page is not their acceptance
    if (who) { if (/another address/.test(who) && await healLink(page, { name: w.name, lead_id: w.lead_id, profile_url: w.profile_url })) { state.checked[w.lead_id] = Date.now() - 5 * 3600 * 1000; continue; }
      await skipNote(state, w, `acceptance not checked: ${who}`); continue; }
    if (!DRY) await reportProfile(page, w.lead_id, state, w.name);
    if ((await degreeOf(page)) === "1st" && !DRY) {
      await api(`/api/extension/leads/${w.lead_id}/accepted`, "POST");
      await event("accepted", { lead_id: w.lead_id, note: `${w.name} accepted`, ...(await observe(page)) });
      log(`accepted: ${w.name}`);
    }
  }                                    // looking at profiles is a read: it does not use up this cycle's action
  // 2. replies: read the messaging list every 20 minutes (a read, no caps), so a reply between steps is seen the same day.
  if (ctl.check_replies && !DRY && Date.now() - (state.inboxAt || 0) > 20 * 60 * 1000) {
    state.inboxAt = Date.now();
    const items = await readInbox(page, queue.reply_watch || []);
    if (items.length) {
      const got = await api("/api/extension/inbox", "POST", { items }).catch((e) => { log("could not report the inbox:", e.message); return { replied: [] }; });
      for (const r of got.replied || []) log(`reply from ${r.name} (sequence stopped)`);
    }
  }
  portalAllowsNoNote = !!ctl.allow_no_note;
  if (ctl.wait_seconds > 0) return { idle: `waiting ${ctl.wait_seconds}s between actions` };
  const seenLeads = new Set(), seenSlugs = new Set();
  for (const item of queue.due) {
    if (!item.profile_url || seenLeads.has(item.lead_id)) continue;      // the same lead twice in one queue is one lead
    seenLeads.add(item.lead_id);
    const slug = slugOf(item.profile_url);
    if (slug && seenSlugs.has(slug)) { await skipNote(state, item, "another lead in this queue has the same LinkedIn link"); continue; }
    seenSlugs.add(slug);
    const kind = item.kind === "connect" ? "connect" : "message";
    if ((kind === "connect" && (!ctl.may_connect || state.noNotes > Date.now() && !(ALLOW_NO_NOTE || portalAllowsNoNote))) || (kind === "message" && !ctl.may_message)) continue;
    if ((state.failed[item.lead_id] || 0) >= 2 || state.dryDone.has(item.lead_id)) continue;    // failed twice: leave them, never open their page again this run
    const nameBad = nameProblem(item.name);
    if (nameBad) { await skipNote(state, item, nameBad); continue; }
    const info = await api(`/api/extension/match?profile_url=${encodeURIComponent(item.profile_url)}`);
    if (!info.can_send || !info.message) { await skipNote(state, item, info.reason || "the portal says nothing is due for this person"); continue; }
    if (info.lead_id !== item.lead_id) { await skipNote(state, item, "the profile link belongs to another lead in the portal"); continue; }
    if (!DRY) { const no = await claim(item.lead_id); if (no) { await skipNote(state, item, no); continue; } }
    const stopHere = async (why, kindOfEvent = "error") => { state.failed[item.lead_id] = (state.failed[item.lead_id] || 0) + 1; log(`could not ${kind} ${item.name}: ${why}`);
      await event(kindOfEvent, { lead_id: item.lead_id, note: `${kind} ${item.name}: ${why}`.slice(0, 280) }); };
    let drawn = false;
    try { drawn = await openProfile(page, toLocal(item.profile_url)); }
    catch (e) { if (e instanceof Fatal) throw e; await stopHere(`the profile page did not load (${String(e.message || e).split("\n")[0].slice(0, 100)}); nothing was done`); continue; }
    if (!drawn) { await stopHere("the profile page did not finish loading; nothing was done"); continue; }
    const who = await identityProblem(page, item);
    if (who && /another address/.test(who) && await healLink(page, item)) { await skipNote(state, item, "the saved LinkedIn link was replaced by the address LinkedIn shows (same name); sent next round"); continue; }
    if (who) { await stopHere(`${who}; nothing was done`, "skip"); continue; }
    if (!DRY) await reportProfile(page, item.lead_id, state, item.name);       // a dry run writes nothing, not even the profile report
    const ctx = { clicked: false, proof: null, openProfile: !!info.open_profile };
    ctx.recheck = async () => {             // right before each click: portal still allows it, page is still the lead's, no stray tab
      if (Date.now() - POPUP.at < 20000) return "a new browser tab opened by the page (closed); the page cannot be trusted";
      const live = await api("/api/extension/agent").catch(() => null);
      if (!live) return "the portal could not be reached just before sending";
      if (!live.active_now) return `the portal stopped the agent just before sending (${live.why_not || "not active"})`;
      const again = await api(`/api/extension/match?profile_url=${encodeURIComponent(item.profile_url)}`).catch(() => null);
      if (!again || !again.can_send || again.lead_id !== item.lead_id || !again.message || again.message.step !== info.message.step) return `the portal no longer lists this step for ${item.name} (${(again && again.reason) || "changed or stopped"})`;
      const bad = await identityProblem(page, item);
      return bad ? `the page is no longer confirmed as ${item.name} (${bad})` : "";
    };
    let result;
    try {
      result = kind === "connect" ? await doConnect(page, item, info.message.text, ctx, false, info.message.short_text || "")
        : await doMessage(page, item, info.message.text, ctx, info.message.step, !!info.open_profile);
    } catch (e) {
      if (e instanceof Fatal) throw e;                               // checkpoint / login / weekly limit: stop as before
      result = { done: false, uncertain: ctx.clicked, why: ctx.clicked
        ? `the page failed after Send was pressed (${String(e.message || e).split("\n")[0].slice(0, 100)}); it may have gone out, so the profile / chat is checked again before anything is retried`
        : `the page did not behave as expected (${String(e.message || e).split("\n")[0].slice(0, 120)}); nothing more was clicked` };
      await closeDialogs(page).catch(() => {});
    }
    if (!result.done && !result.dry && !result.replied) state.failed[item.lead_id] = (state.failed[item.lead_id] || 0) + 1;
    if (result.dry) {                    // a dry run changes nothing: it says what it WOULD do, to whom, once per person
      log(`DRY would ${kind} ${item.name} (${item.profile_url}): ${result.why}`);
      if (result.wouldSend) log(`DRY text for ${item.name}: ${result.wouldSend.slice(0, 300)}`);
      state.dryDone.add(item.lead_id);
      await event("dry", { lead_id: item.lead_id, note: `DRY would ${kind} ${item.name}: ${result.why}${result.wouldSend ? ` - "${norm(result.wouldSend).slice(0, 110)}"` : ""}`.slice(0, 290), dry: true, text_sent: (result.wouldSend || "").slice(0, 2000), step: info.message.step });
      return { acted: "dry" };
    }
    if (result.replied) {                 // they had already answered: nothing sent, the reply goes to the portal
      await api(`/api/extension/leads/${item.lead_id}/replied`, "POST", { text: result.replied.slice(0, 2000) }).catch((e) => log("could not report the reply:", e.message));
      await event("replied", { lead_id: item.lead_id, note: `${item.name} replied on LinkedIn (seen before step ${info.message.step}); sequence stopped` });
      log(`reply from ${item.name}: not sending step ${info.message.step}`);
      continue;
    }
    if (!result.done) {
      log(`could not ${kind} ${item.name}: ${result.why}`);
      if (result.stopNotes) { state.noNotes = Date.now() + 24 * 3600 * 1000; log("connection requests wait a day: LinkedIn takes no note and 'send without a note' is off"); }
      await event("error", { lead_id: item.lead_id, note: `${kind} ${item.name}: ${result.why}`.slice(0, 280) });
      continue;
    }
    // Proof it went through: a connection request must now show "Pending" on the lead's profile (3 looks); a message was
    // proven by its text appearing in the conversation. Anything less is NOT recorded as sent.
    let verified = true;
    if (kind === "connect" && !result.already) {
      verified = false;
      for (let t = 0; t < 3 && !verified; t++) {
        if (t) await sleep(3000);
        try {
          await page.reload({ waitUntil: "domcontentloaded" }); await sleep(SETTLE);
          verified = !(await identityProblem(page, item)) && await isPending(page, item.name);
        } catch { verified = false; }
      }
      if (!verified) {                  // LinkedIn closed the box but did not take the request (error toast): never record it as sent
        state.failed[item.lead_id] = (state.failed[item.lead_id] || 0) + 1;
        log(`could not connect ${item.name}: the profile does not show Pending after sending`);
        await event("error", { lead_id: item.lead_id, note: `connect ${item.name}: the box closed but the profile does not show Pending, so it is NOT recorded as sent (checked again next time)` });
        continue;
      }
    }
    const proof = result.proof || ctx.proof || {};
    const entry = { lead_id: item.lead_id, name: item.name, kind, accepted: result.already === "connected", at: Date.now(),
      body: { step: info.message.step, ...proof },
      event: { lead_id: item.lead_id, step: info.message.step, verified, ...proof,
        note: `${kind === "connect" ? `connection request${result.bare ? " (without a note: LinkedIn's note limit)" : ""}` : "message"} to ${item.name}${result.already ? ` (${result.already})` : ""}` } };
    writePending([...readPending().filter((e) => !(e.lead_id === entry.lead_id && e.body.step === entry.body.step)), entry]);   // kept until the portal has it
    const out = await deliverSent(entry, 4);
    if (out === "later") { log(`the portal could not be reached to report the ${kind} to ${item.name}; it is kept and reported first at the next start`); return { idle: "waiting to report a send to the portal" }; }
    writePending(readPending().filter((e) => !(e.lead_id === entry.lead_id && e.body.step === entry.body.step)));
    if (out === "refused") {              // e.g. 409: stop for this person, never retry (a mismatch pauses the account on the portal)
      state.failed[item.lead_id] = 9;
      await event("error", { lead_id: item.lead_id, note: `${kind} ${item.name}: the portal did not record the send (see its answer); stopped for this person` });
      return { acted: kind };
    }
    await afterSent(entry);
    log(`${kind === "connect" ? "connection request" : "message"} sent: ${item.name}`);
    return { acted: kind };
  }
  const looked = await lookupStep(page, ctl);
  if (looked) return looked;
  return { idle: "nothing due that is allowed right now" };
}

// After a fatal stop in the cloud the agent does not exit (the supervisor would only start it again): it reports the
// pause and waits, touching nothing on LinkedIn, until the owner presses Resume for this account in the portal.
async function waitForResume() {
  for (;;) {
    await sleep(Math.max(POLL, 15000));
    const ctl = await api("/api/extension/agent").catch(() => null);
    if (ctl && ctl.enabled && !/paused/.test(ctl.why_not || "")) return;
  }
}

async function launch() {
  // handleSIGTERM/SIGINT off: Playwright would kill Chromium at once on those signals, before our close() lets it write
  // the profile (main() closes the browser cleanly on them instead).
  const options = { headless: HEADLESS, viewport: { width: 1280, height: 900 }, handleSIGTERM: false, handleSIGINT: false };
  // Headless cloud agents use Chromium's new headless mode (the full browser): the old headless shell does not write the
  // profile's cookies to disk, so every agent restart (switch off/on, Restart, office/server switch, a runner without a
  // desktop) logged the LinkedIn account out and needed a new login with a code.
  if (CLOUD && HEADLESS && process.env.MCN_HEADLESS_SHELL !== "1") options.channel = "chromium";
  if (CLOUD && HEADLESS) options.args = ["--disable-dev-shm-usage", "--disable-gpu", "--disable-extensions", "--mute-audio", "--no-first-run",
    "--renderer-process-limit=2", "--js-flags=--max-old-space-size=256"];
  const context = await chromium.launchPersistentContext(PROFILE_DIR, options);
  if (CLOUD && HEADLESS) await context.route("**/*", (route) => route.request().resourceType() === "media" ? route.abort() : route.continue());   // videos only cost memory
  return context;
}

async function main() {
  await acquireLock();
  const context = await launch();
  let closing = false, closed = null;
  // Once only: the pool sends "stop" and SIGTERM together, and a second close() must not exit before Chromium has
  // written its cookies (that logged the account out of LinkedIn on every switch-off, Restart or runner stop).
  const close = (why) => {
    if (closed) return closed;
    closing = true;
    log(`${why}: closing the browser`);
    closed = context.close().catch(() => {}).then(() => process.exit(0));
    return closed;
  };
  for (const signal of ["SIGTERM", "SIGINT"]) process.on(signal, () => close(signal));   // the supervisor (or Ctrl+C) stops it
  // The browser went away (its window was closed, or it crashed): stop with an error so the runner / supervisor starts a
  // new window (it restarts a crashed agent). Without this the agent could sit waiting for a login with no browser at all.
  context.on("close", () => { if (!closing) { console.error("The browser was closed; stopping so it is opened again."); process.exit(1); } });
  if (process.channel) {                // started by the agent pool: "stop" over IPC (on Windows a signal cannot be caught)
    process.on("message", (m) => { if (m === "stop") close("stop requested"); });
    // The runner / supervisor itself died (killed, crashed): stop too, or the restarted runner would start a second agent
    // on the same LinkedIn account while this orphan kept going.
    process.on("disconnect", () => close("the office runner or supervisor stopped"));
    process.channel.unref();
  }
  const page = context.pages()[0] || await context.newPage();
  // A page that opens a second tab (a Premium offer, an ad) must never receive a click or a message: the tab is closed at once
  // and the action in progress is refused (POPUP is checked right before every click).
  context.on("page", (p) => { if (p !== page) { POPUP.at = Date.now(); log("a second browser tab opened; closing it"); p.close().catch(() => {}); } });
  const state = { checked: {}, noNotes: 0, profiled: {}, failed: {}, skipped: {}, dryDone: new Set() };
  let exitCode = 0;
  try {
    await event("started", { state: "running", note: DRY ? "started in dry-run mode" : CLOUD ? "cloud agent started" : "agent started" });
    if (CLOUD) await cloudState("starting", "the cloud browser started");
    for (;;) {
      try {
        if (!args["skip-login-check"]) await ensureLoggedIn(page);
        if (!args["no-account"]) { await whoAmI(page); log(`Logged in to LinkedIn as ${ME.name || ME.account} (${ME.account}).`); }
        if (CLOUD) await cloudState("connected", `Logged in as ${ME.name || ME.account}. Works inside the hours and limits set below.`);
        log(DRY ? "Dry run: nothing will be clicked or sent, nothing is changed in the portal." : CLOUD ? "Running in the cloud." : "Running. Press Ctrl+C to stop.");
        for (;;) {
          const outcome = await oneCycle(page, state);
          if (outcome.idle) log(outcome.idle);
          if (ONCE && outcome.acted) break;
          if (ONCE && outcome.idle) break;
          await sleep(outcome.acted ? 1000 : POLL);
        }
        break;
      } catch (e) {
        if (!CLOUD || !(e instanceof Fatal)) throw e;
        if (e instanceof LoginNeeded) {
          log("LinkedIn wants a new login:", e.message);
          await event("paused", { state: "needs_login", note: e.message });
          continue;                       // ensureLoggedIn reports needs_login and waits for the portal
        }
        log("PAUSED:", e.message);
        await event("error", { fatal: true, note: e.message, state: "stopped" });
        await cloudState("paused", `stopped itself: ${e.message}. Sort it out on LinkedIn, then press Resume for this account.`);
        await waitForResume();
        log("Resumed from the portal.");
      }
    }
  } catch (e) {
    if (e instanceof Fatal) {
      log("STOPPING:", e.message);
      await event("error", { fatal: true, note: e.message, state: "stopped" });
      exitCode = 3;
    } else { log("error:", e.message); await event("error", { note: e.message.slice(0, 200), state: "error" }); exitCode = 1; }
    if (CLOUD) await cloudState("error", e.message.slice(0, 200));
  } finally {
    closing = true;
    await context.close();
  }
  process.exit(exitCode);
}
main();
