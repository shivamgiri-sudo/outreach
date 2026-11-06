#!/usr/bin/env node
// Runs the cloud LinkedIn agents on the server: one headless `tools/linkedin_agent.mjs --cloud` per enabled slot in
// slots.json (written by the portal, app/linkedin_cloud.py). Every pass (15 s) it:
//   - starts agents for enabled slots (at most OUTREACH_CLOUD_LI_MAX), each with its own browser profile folder (0700);
//   - stops agents whose slot was disabled or removed, and deletes the browser profile of removed slots;
//   - restarts an agent that crashed, waiting longer each time (5 s, 10 s, 20 s ... up to 5 minutes);
//   - does NOT restart an agent that stopped itself for safety (exit code 3) until the owner acts in the portal
//     (the slot's run_seq changes: Log in, Restart, Switch on);
//   - "Restart" in the portal raises restart_seq: a running agent is stopped and started again;
//   - "Log out" in the portal raises the slot's wipe_seq: the agent is stopped, its browser profile deleted, and it starts
//     again logged out;
//   - writes supervisor.json (a heartbeat the portal shows).
// The slot token goes to the agent in its environment (MCN_TOKEN), not on the command line, so `ps` does not show it.
//
//   node tools/linkedin_cloud_supervisor.mjs [--dir /var/lib/outreach/linkedin-cloud] [--portal http://127.0.0.1:8000]
//        [--interval 15] [--max 1] [--agent tools/linkedin_agent.mjs] [--agent-args="--poll 20"]
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// --key value, or --key=value (needed for --agent-args="--poll 5", whose value starts with --)
const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => {
  if (a.startsWith("--") && a.includes("=")) acc.push([a.slice(2, a.indexOf("=")), a.slice(a.indexOf("=") + 1)]);
  else if (a.startsWith("--")) acc.push([a.slice(2), all[i + 1] && !all[i + 1].startsWith("--") ? all[i + 1] : true]);
  return acc;
}, []));
const HERE = path.dirname(fileURLToPath(import.meta.url));
const DIR = path.resolve(String(args.dir || process.env.OUTREACH_CLOUD_LI_DIR || "/var/lib/outreach/linkedin-cloud"));
const PORTAL = String(args.portal || process.env.OUTREACH_CLOUD_LI_PORTAL || "http://127.0.0.1:8000");
const INTERVAL = Number(args.interval || process.env.OUTREACH_CLOUD_LI_INTERVAL || 15) * 1000;
const MAX = Math.max(0, Number(args.max || process.env.OUTREACH_CLOUD_LI_MAX || 1));
const AGENT = path.resolve(String(args.agent || path.join(HERE, "linkedin_agent.mjs")));
const EXTRA = String(args["agent-args"] || process.env.OUTREACH_CLOUD_LI_AGENT_ARGS || "").split(/\s+/).filter(Boolean);
const BACKOFF_BASE = Number(args["backoff-base"] || 5) * 1000;
const BACKOFF_MAX = 5 * 60 * 1000;
const STOP_GRACE = Number(args["stop-grace"] || 10) * 1000;
const STATE_FILE = path.join(DIR, "supervisor-state.json");
const PROFILES = path.join(DIR, "profiles");

const log = (...a) => console.log(new Date().toISOString(), ...a);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const running = new Map();       // slot_id -> {child, startedAt, runSeq}
const held = new Map();          // slot_id -> {runSeq, why}  (stopped for safety, or crashed: waiting)
const crashes = new Map();       // slot_id -> {count, until}
let wiped = {};                  // slot_id -> wipe_seq already handled (persisted)
let stopping = false;

try { wiped = JSON.parse(fs.readFileSync(STATE_FILE, "utf8")).wiped || {}; } catch { wiped = {}; }

function readSlots() {
  try {
    const data = JSON.parse(fs.readFileSync(path.join(DIR, "slots.json"), "utf8"));
    return Array.isArray(data.slots) ? data.slots.filter((s) => s && /^[a-z0-9]{4,20}$/.test(s.slot_id || "") && s.token) : [];
  } catch (e) {
    if (e.code !== "ENOENT") log("could not read slots.json:", e.message);
    return [];
  }
}

function writeJson(file, data) {
  const temp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(temp, JSON.stringify(data), { mode: 0o600 });
  fs.renameSync(temp, file);
}

// A slot's browser profile is always DIR/profiles/<slot id> (slot ids are checked above): the profile_dir written in
// slots.json is informational and never used as a path to create or delete.
const profileOf = (slot) => path.join(PROFILES, slot.slot_id);

function start(slot) {
  const profile = profileOf(slot);
  fs.mkdirSync(profile, { recursive: true, mode: 0o700 });
  try { fs.chmodSync(profile, 0o700); } catch { /* not ours */ }
  const env = { PATH: process.env.PATH, HOME: process.env.HOME || DIR, LANG: process.env.LANG || "C.UTF-8", TZ: process.env.TZ || "Asia/Kolkata",
    MCN_TOKEN: slot.token, MCN_PORTAL: PORTAL,
    ...(process.env.PLAYWRIGHT_BROWSERS_PATH ? { PLAYWRIGHT_BROWSERS_PATH: process.env.PLAYWRIGHT_BROWSERS_PATH } : {}),
    ...(process.env.MCN_PLAYWRIGHT_DIR ? { MCN_PLAYWRIGHT_DIR: process.env.MCN_PLAYWRIGHT_DIR } : {}) };
  const child = spawn(process.execPath, [AGENT, "--cloud", "--headless", "--portal", PORTAL, "--profile", profile, ...EXTRA],
    { env, stdio: ["ignore", "pipe", "pipe"] });
  const tag = `[${slot.slot_id}]`;
  for (const stream of [child.stdout, child.stderr]) {
    let buf = "";
    stream.on("data", (d) => { buf += d; let i; while ((i = buf.indexOf("\n")) >= 0) { console.log(tag, buf.slice(0, i)); buf = buf.slice(i + 1); } });
  }
  const entry = { child, startedAt: Date.now(), runSeq: slot.run_seq || 0, restartSeq: slot.restart_seq || 0, stopping: false };
  running.set(slot.slot_id, entry);
  log(`started agent for ${slot.slot_id} (${slot.label || "no label"}), pid ${child.pid}`);
  child.on("exit", (code, signal) => {
    if (running.get(slot.slot_id) === entry) running.delete(slot.slot_id);
    if (entry.stopping || stopping) { log(`agent for ${slot.slot_id} stopped`); return; }
    if (code === 3) {
      held.set(slot.slot_id, { runSeq: entry.runSeq, why: "stopped itself for safety" });
      log(`agent for ${slot.slot_id} stopped itself for safety (exit 3); it starts again when the owner acts in the portal`);
      return;
    }
    const before = crashes.get(slot.slot_id) || { count: 0 };
    const count = Date.now() - entry.startedAt > 10 * 60 * 1000 ? 1 : before.count + 1;     // a long healthy run resets the backoff
    const wait = Math.min(BACKOFF_MAX, BACKOFF_BASE * 2 ** (count - 1));
    crashes.set(slot.slot_id, { count, until: Date.now() + wait });
    log(`agent for ${slot.slot_id} exited (code ${code}${signal ? `, ${signal}` : ""}); restarting in ${Math.round(wait / 1000)} s`);
  });
}

async function stop(slotId, why) {
  const entry = running.get(slotId);
  if (!entry) return;
  entry.stopping = true;
  log(`stopping agent for ${slotId}: ${why}`);
  entry.child.kill("SIGTERM");
  for (let waited = 0; waited < STOP_GRACE && entry.child.exitCode === null && entry.child.signalCode === null; waited += 200) await sleep(200);
  if (entry.child.exitCode === null && entry.child.signalCode === null) entry.child.kill("SIGKILL");
  running.delete(slotId);
}

function wipe(slotId) {
  fs.rmSync(path.join(PROFILES, slotId), { recursive: true, force: true });
}

async function pass() {
  const slots = readSlots();
  const known = new Set(slots.map((s) => s.slot_id));
  for (const id of [...running.keys()]) if (!known.has(id)) await stop(id, "its cloud account was removed");
  try {                                            // browser profiles of removed accounts
    for (const name of fs.readdirSync(PROFILES)) if (!known.has(name) && !running.has(name)) { wipe(name); log(`deleted the browser profile of removed account ${name}`); }
  } catch { /* no profiles yet */ }
  for (const id of Object.keys(wiped)) if (!known.has(id)) delete wiped[id];
  for (const map of [crashes, held]) for (const id of [...map.keys()]) if (!known.has(id)) map.delete(id);
  let started = running.size;
  for (const slot of slots) {
    const id = slot.slot_id;
    if ((slot.wipe_seq || 0) > (wiped[id] || 0)) {
      await stop(id, "logged out in the portal");
      wipe(id);
      wiped[id] = slot.wipe_seq || 0;
      writeJson(STATE_FILE, { wiped });
      log(`deleted the browser session of ${id} (logged out)`);
    }
    if (!slot.enabled) { if (running.has(id)) { await stop(id, "switched off in the portal"); started -= 1; } held.delete(id); continue; }
    if (running.has(id) && (slot.restart_seq || 0) > running.get(id).restartSeq) { await stop(id, "restart pressed in the portal"); started -= 1; crashes.delete(id); }
    if (running.has(id)) continue;
    const hold = held.get(id);
    if (hold && hold.runSeq === (slot.run_seq || 0)) continue;
    held.delete(id);
    const crash = crashes.get(id);
    if (crash && Date.now() < crash.until) continue;
    if (started >= MAX) continue;
    start(slot);
    started += 1;
  }
  const agents = Object.fromEntries(slots.map((s) => [s.slot_id, running.has(s.slot_id) ? "running" : held.has(s.slot_id) ? "held"
    : !s.enabled ? "off" : crashes.has(s.slot_id) && Date.now() < crashes.get(s.slot_id).until ? "restarting" : "waiting"]));
  try { writeJson(path.join(DIR, "supervisor.json"), { at: new Date().toISOString(), pid: process.pid, max: MAX, agents }); }
  catch (e) { log("could not write supervisor.json:", e.message); }
}

async function shutdown(signal) {
  if (stopping) return;
  stopping = true;
  log(`${signal}: stopping all agents`);
  await Promise.all([...running.keys()].map((id) => stop(id, "the supervisor is stopping")));
  process.exit(0);
}
process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));

fs.mkdirSync(DIR, { recursive: true, mode: 0o700 });
fs.mkdirSync(PROFILES, { recursive: true, mode: 0o700 });
log(`cloud LinkedIn supervisor: ${DIR}, portal ${PORTAL}, at most ${MAX} agent(s), every ${INTERVAL / 1000} s`);
for (;;) {
  try { await pass(); } catch (e) { log("pass failed:", e.message); }
  await sleep(INTERVAL);
}
