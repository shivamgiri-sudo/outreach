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
// --cloud: the same agent started by tools/linkedin_cloud_supervisor.mjs (headless, on the portal's server) or by
// tools/linkedin_runner.mjs (on an always-on office computer, a visible window by default: --headed). It is logged in from
// the portal (tools/linkedin_cloud_login.mjs) instead of by a person at the window.
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { cloudLogin } from "./linkedin_cloud_login.mjs";

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
const DRY = !!args.dry;                 // look at the page and say what would be clicked, click nothing
const ONCE = !!args.once;               // do at most one action and exit (tests, or a first check)
const ALLOW_NO_NOTE = !!args["allow-no-note"];      // send a request without a note when LinkedIn will not allow one
const CLOUD = !!args.cloud;             // on the server: headless, logged in from the portal
const HEADLESS = args.headed ? false : !!args.headless || CLOUD;   // local use needs the window so you can log in; the office runner shows it too
const SETTLE = Number(args.settle || 1500);          // fixed wait after a page changes
const POLL = Number(args.poll || 20) * 1000;
const LOGIN_POLL = Number(args["login-poll"] || 3);

if (!PORTAL || !TOKEN) { console.error("Usage: node tools/linkedin_agent.mjs --portal https://your-portal --token mcnx_..."); process.exit(2); }
const log = (...a) => console.log(new Date().toLocaleTimeString(), ...a);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
if (LINKEDIN !== "https://www.linkedin.com") log(`LinkedIn address: ${LINKEDIN} (test stand-in)`);

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
class LoginNeeded extends Fatal {}        // a login wall: locally a stop, in the cloud a new login from the portal
const cloudState = (state, note = "") => api("/api/extension/cloud/state", "POST", { state, note, account: ME.account, name: ME.name })
  .catch((e) => log("could not report state:", e.message));

const TROUBLE = [/security (check|verification)/i, /quick security check/i, /weekly invitation limit/i, /reached the weekly/i,
  /unusual activity/i, /restricted your account|account (has been )?restricted/i, /verify (that )?you('| a)re (not )?a (human|robot)/i];
async function checkTrouble(page) {
  const url = page.url();
  const where = new URL(url).pathname;
  if (/\/checkpoint/.test(where) && !/\/feed/.test(url)) throw new Fatal(`LinkedIn asked for a checkpoint (${where})`);
  if (/\/(authwall|uas\/login|login)/.test(where) && !/\/feed/.test(url)) throw new LoginNeeded(`LinkedIn asked for a login (${where})`);
  const text = (await page.locator("body").innerText({ timeout: 5000 }).catch(() => "")).slice(0, 6000);
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
// The person's own card is the first section of the profile. Everything further down (people you may know, "more
// profiles for you") has Connect / Message buttons for OTHER people, so a button is only used when its label names this
// lead, or when it sits inside that first card. LinkedIn moves the buttons between layouts (sometimes outside <main>).
const topCard = (page) => page.locator("main section, main:not(:has(section))").first();
const tokens = (name) => String(name || "").toLowerCase().normalize("NFKD").replace(/[^a-z\s]/g, " ").split(/\s+/).filter((w) => w.length > 1);
function namesLead(label, name) {
  const want = tokens(name), have = tokens(label);
  if (!want.length) return false;
  return have.includes(want[0]) && (want.length === 1 || have.includes(want[want.length - 1]));
}
// Every visible element whose label names this lead, the ones on the profile itself first (LinkedIn also repeats the
// buttons in a sticky header that is often covered, and keeps hidden copies).
async function labelledAll(page, selector, name) {
  const all = page.locator(selector), found = [];
  for (let i = 0, n = await all.count(); i < n; i++) {
    const el = all.nth(i);
    if (!namesLead(await el.getAttribute("aria-label"), name) || !(await el.isVisible().catch(() => false))) continue;
    const box = await el.boundingBox().catch(() => null);
    if (!box || box.width < 2 || box.height < 2) continue;
    found.push({ el, inMain: await el.evaluate((e) => !!e.closest("main")).catch(() => false) });
  }
  return found.sort((x, y) => Number(y.inMain) - Number(x.inMain)).map((f) => f.el);
}
async function labelled(page, selector, name) {
  return (await labelledAll(page, selector, name))[0] || null;
}
// Click the first candidate that takes the click; something covering a button (a banner, the sticky header) moves on.
async function clickOne(candidates) {
  for (const el of candidates) {
    try { await el.click({ timeout: 5000 }); return true; } catch { /* covered or gone: try the next copy */ }
  }
  return false;
}

async function degreeOf(page) {
  const text = (await topCard(page).innerText({ timeout: 5000 }).catch(() => "")).slice(0, 1500);
  const m = text.match(/\b(1st|2nd|3rd)\b/);
  return m ? m[1] : "";
}
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

// Connect with the note first. When LinkedIn will not take a note (free accounts: a few a month) it either disables
// "Add a note" or answers it with a Premium offer and no text box; then, only if "send without a note" is allowed,
// the request is sent bare. Every wait is bounded, so one person can never hold the agent.
async function doConnect(page, lead, note, retried = false, shortNote = "") {
  if (!tokens(lead.name).length) return { done: false, why: "the lead has no name, so the right Connect button cannot be told apart" };
  if (await isPending(page, lead.name)) return { done: true, already: "pending" };
  if ((await degreeOf(page)) === "1st") return { done: true, already: "connected" };
  const connect = await findConnect(page, lead.name);
  if (!connect || !connect.length) return { done: false, why: "no Connect button on this profile (they may only allow Follow or Message)" };
  if (DRY) return { done: false, dry: true, why: `would click Connect and send a ${note.length}-character note` };
  if (!(await clickOne(connect))) return { done: false, why: "the Connect button for this person was covered by something on the page; nothing was sent" };
  await sleep(800);
  await checkTrouble(page);            // the weekly invitation limit appears right after Connect: stop, do not read it as "no note"
  const dialog = await inviteDialog(page);
  if (!dialog) { await closeDialogs(page); return { done: false, why: "the invitation box did not open; nothing was sent" }; }
  const allowBare = ALLOW_NO_NOTE || portalAllowsNoNote;
  const addNote = dialog.getByRole("button", { name: /add a note/i }).first();
  const notesOk = Date.now() > notesBlockedUntil && (await addNote.count()) && (await addNote.isEnabled().catch(() => false));
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
      return doConnect(page, lead, note, true, shortNote);
    }
    // The box's real limit (free accounts: 200). Use the short note when the long one does not fit; never cut a sentence.
    const limit = await box.getAttribute("maxlength").then((v) => parseInt(v, 10) || 0).catch(() => 0);
    let text = note;
    if (limit && text.length > limit) {
      if (shortNote && shortNote.length <= limit) { text = shortNote; log(`the box takes ${limit} characters: sending the short note`); }
      else if (allowBare && !retried) {
        await closeDialogs(page); await page.reload({ waitUntil: "domcontentloaded" }); await sleep(SETTLE);
        log(`the box takes ${limit} characters and no note fits: sending without one`);
        notesBlockedUntil = Date.now() + 24 * 3600 * 1000;
        return doConnect(page, lead, note, true, shortNote);
      } else { await closeDialogs(page); return { done: false, why: `LinkedIn takes only ${limit} characters in the note and no saved note is that short; nothing was sent` }; }
    }
    await box.fill(text, { timeout: 5000 }).catch(() => {});
    await sleep(300);
    const send = page.getByRole("dialog").getByRole("button", { name: /^send( invitation| now)?$/i }).first();
    if (!(await tap(send))) { await closeDialogs(page); return { done: false, why: "the Send button in the invitation box could not be pressed; nothing was sent" }; }
  } else {
    if (!allowBare) {
      await closeDialogs(page);
      return { done: false, why: "LinkedIn will not let this account add a note (monthly limit). Tick \"send without a note\" under Limits to send it anyway", stopNotes: true };
    }
    const bare = dialog.getByRole("button", { name: /send without a note/i }).first();
    const send = (await bare.count()) ? bare : dialog.getByRole("button", { name: /^send( invitation| now)?$/i }).first();
    if (!(await tap(send))) { await closeDialogs(page); return { done: false, why: "the Send button in the invitation box could not be pressed; nothing was sent" }; }
  }
  await sleep(SETTLE);
  await checkTrouble(page);
  if (await page.getByRole("dialog").filter({ has: page.getByRole("button", { name: INVITE_BUTTONS }) }).count()) {
    await closeDialogs(page);
    return { done: false, why: "the invitation box stayed open, so the request may not have been sent" };
  }
  return { done: true, bare: !notesOk || retried };
}

async function doMessage(page, lead, text) {
  const button = await findMessage(page, lead.name);
  if (!button) return { done: false, why: "no Message button for this person (you may not be connected yet)" };
  if (DRY) return { done: false, dry: true, why: `would open Message and send ${text.length} characters` };
  if (!(await clickOne([button]))) return { done: false, why: "the Message button was covered by something on the page; nothing was sent" };
  await sleep(SETTLE);
  const box = page.locator('div[role="textbox"][contenteditable="true"]').last();
  if (!(await box.count())) return { done: false, why: "the message box did not open" };
  const theirs = await readThread(page, lead);
  if (theirs.length) { await page.keyboard.press("Escape"); return { done: false, replied: theirs.join("\n\n") }; }
  await box.click({ timeout: 5000 });
  await page.keyboard.insertText(text);
  await sleep(500);
  if (!(await tap(page.getByRole("button", { name: /^send$/i }).last()))) return { done: false, why: "the Send button could not be pressed; nothing was sent" };
  await sleep(SETTLE);
  await checkTrouble(page);
  const stillTyped = ((await box.innerText().catch(() => "")) || "").trim().length > 0;
  if (stillTyped) return { done: false, why: "the message is still in the box, so it may not have been sent" };
  return { done: true };
}

// LinkedIn's chat window lists the conversation as groups (sender name + messages). Their messages are the ones marked
// "--other" or in a group under their name. Read before every follow-up: a person who already answered is never sent
// the next step; the reply goes to the portal instead. Returns their message texts (empty when there are none).
async function readThread(page, lead) {
  await sleep(800);
  return page.evaluate(({ first, me }) => {
    const out = [];
    let sender = "";
    for (const ev of document.querySelectorAll(".msg-s-message-list__event")) {
      const n = ev.querySelector(".msg-s-message-group__name");
      if (n) sender = n.textContent.trim().toLowerCase();
      for (const it of ev.querySelectorAll(".msg-s-event-listitem")) {
        const byName = sender && first && sender.includes(first) && !(me && me !== first && sender.includes(me));
        const body = it.querySelector(".msg-s-event-listitem__body");
        if (body && (it.className.includes("msg-s-event-listitem--other") || byName)) out.push(body.textContent.trim());
      }
    }
    return out.filter(Boolean).slice(-5);
  }, { first: tokens(lead.name)[0] || "", me: tokens(ME.name)[0] || "" }).catch(() => []);
}

// The messaging list (linkedin.com/messaging/): each conversation's name and last-message preview. A preview that
// starts "You:" is ours; anything else is theirs. Group conversations (several names) are left out.
async function readInbox(page) {
  try {
    await page.goto(LINKEDIN + "/messaging/", { waitUntil: "domcontentloaded" }); await sleep(SETTLE);
  } catch (e) {                         // the list did not load: try again next time, never stop the agent over a read
    log("could not open the messaging list:", String(e.message || e).split("\n")[0]);
    return [];
  }
  await checkTrouble(page);             // a login wall or security check here still stops it, as anywhere else
  return page.evaluate(() => [...document.querySelectorAll(".msg-conversation-listitem, .msg-conversation-card")].slice(0, 40).map((li) => {
    const name = (li.querySelector(".msg-conversation-listitem__participant-names, .msg-conversation-card__participant-names")?.textContent || "").trim();
    const snip = (li.querySelector(".msg-conversation-card__message-snippet, .msg-conversation-listitem__message-snippet")?.textContent || "").trim();
    const ours = /^you\s*:/i.test(snip);
    return { name, text: snip.replace(/^[^:]{1,60}:\s*/, ""), from_them: !!name && !ours };
  }).filter((i) => i.name && !i.name.includes(","))).catch(() => []);
}

// Open Profile: not a 1st-degree connection, yet LinkedIn shows a Message button (Premium members who allow free messages).
async function reportProfile(page, leadId, state, name) {
  if (!leadId || state.profiled[leadId]) return;
  state.profiled[leadId] = true;
  const degree = await degreeOf(page);
  const open = degree !== "1st" && !!(await findMessage(page, name));
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
    await reportProfile(page, w.lead_id, state, w.name);
    if ((await degreeOf(page)) === "1st" && !DRY) {
      await api(`/api/extension/leads/${w.lead_id}/accepted`, "POST");
      await event("accepted", { lead_id: w.lead_id, note: `${w.name} accepted` });
      log(`accepted: ${w.name}`);
    }
  }                                    // looking at profiles is a read: it does not use up this cycle's action
  // 2. replies: read the messaging list every 20 minutes (a read, no caps), so a reply between steps is seen the same day.
  if (ctl.check_replies && !DRY && Date.now() - (state.inboxAt || 0) > 20 * 60 * 1000) {
    state.inboxAt = Date.now();
    const items = await readInbox(page);
    if (items.length) {
      const got = await api("/api/extension/inbox", "POST", { items }).catch((e) => { log("could not report the inbox:", e.message); return { replied: [] }; });
      for (const r of got.replied || []) log(`reply from ${r.name} (sequence stopped)`);
    }
  }
  portalAllowsNoNote = !!ctl.allow_no_note;
  if (ctl.wait_seconds > 0) return { idle: `waiting ${ctl.wait_seconds}s between actions` };
  for (const item of queue.due) {
    if (!item.profile_url) continue;
    const kind = item.kind === "connect" ? "connect" : "message";
    if ((kind === "connect" && (!ctl.may_connect || state.noNotes && !(ALLOW_NO_NOTE || portalAllowsNoNote))) || (kind === "message" && !ctl.may_message)) continue;
    const info = await api(`/api/extension/match?profile_url=${encodeURIComponent(item.profile_url)}`);
    if (!info.can_send || !info.message) { log(`skip ${item.name}: ${info.reason}`); continue; }
    if (!DRY && !(await claim(item.lead_id))) continue;
    await page.goto(toLocal(item.profile_url), { waitUntil: "domcontentloaded" }); await sleep(SETTLE);
    await checkTrouble(page);
    await reportProfile(page, item.lead_id, state, item.name);
    if ((state.failed[item.lead_id] || 0) >= 2) continue;          // failed twice today: leave them, never loop on one person
    let result;
    try {
      result = kind === "connect" ? await doConnect(page, item, info.message.text, false, info.message.short_text || "") : await doMessage(page, item, info.message.text);
    } catch (e) {
      if (e instanceof Fatal) throw e;                               // checkpoint / login / weekly limit: stop as before
      result = { done: false, why: `the page did not behave as expected (${String(e.message || e).split("\n")[0].slice(0, 120)}); nothing more was clicked` };
      await closeDialogs(page).catch(() => {});
    }
    if (!result.done && !result.dry && !result.replied) state.failed[item.lead_id] = (state.failed[item.lead_id] || 0) + 1;
    if (result.dry) { log(`DRY ${item.name}: ${result.why}`); return { acted: "dry" }; }
    if (result.replied) {                 // they had already answered: nothing sent, the reply goes to the portal
      await api(`/api/extension/leads/${item.lead_id}/replied`, "POST", { text: result.replied.slice(0, 2000) }).catch((e) => log("could not report the reply:", e.message));
      await event("replied", { lead_id: item.lead_id, note: `${item.name} replied on LinkedIn (seen before step ${info.message.step}); sequence stopped` });
      log(`reply from ${item.name}: not sending step ${info.message.step}`);
      continue;
    }
    if (!result.done) {
      log(`could not ${kind} ${item.name}: ${result.why}`);
      if (result.stopNotes) state.noNotes = true;
      await event("error", { lead_id: item.lead_id, note: `${kind} ${item.name}: ${result.why}` });
      continue;
    }
    await api(`/api/extension/leads/${item.lead_id}/sent`, "POST", { step: info.message.step });
    if (result.already === "connected") await api(`/api/extension/leads/${item.lead_id}/accepted`, "POST").catch(() => {});
    await event(kind, { lead_id: item.lead_id, note: `${kind === "connect" ? `connection request${result.bare ? " (without a note: LinkedIn's note limit)" : ""}` : "message"} to ${item.name}${result.already ? ` (${result.already})` : ""}` });
    log(`${kind === "connect" ? "connection request" : "message"} sent: ${item.name}`);
    return { acted: kind };
  }
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
  const state = { checked: {}, noNotes: false, profiled: {}, failed: {} };
  let exitCode = 0;
  try {
    await event("started", { state: "running", note: DRY ? "started in dry-run mode" : CLOUD ? "cloud agent started" : "agent started" });
    if (CLOUD) await cloudState("starting", "the cloud browser started");
    for (;;) {
      try {
        if (!args["skip-login-check"]) await ensureLoggedIn(page);
        if (!args["no-account"]) { await whoAmI(page); log(`Logged in to LinkedIn as ${ME.name || ME.account} (${ME.account}).`); }
        if (CLOUD) await cloudState("connected", `Logged in as ${ME.name || ME.account}. Works inside the hours and limits set below.`);
        log(DRY ? "Dry run: nothing will be clicked." : CLOUD ? "Running in the cloud." : "Running. Press Ctrl+C to stop.");
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
